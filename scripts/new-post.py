#!/usr/bin/env python3
"""
new-post.py — a local, terminal front-end for writing N0YEP blog posts.

A curses TUI (stdlib only, no web, no dependencies): fill in the front matter,
write the body in a built-in scrollable Markdown editor, then "Finish" to write
content/blog/<slug>.md, build the site with Hugo, and commit it to git.

Keys:
  Tab / Shift-Tab   move between fields
  ↑ ↓ ← →           move the cursor (in the body editor)
  Space             toggle the Draft field (when it's focused)
  ^E                edit the body in your $EDITOR (vim/nano/…)
  ^P                save now (draft) so your running `hugo server` shows it
  ^O                Finish → write post, hugo build, git commit
  ^X                quit (offers to save a recovery draft)

Run:  python3 scripts/new-post.py   (optionally  --title "…"  or  --resume)
"""

from __future__ import annotations

import argparse
import curses
import curses.ascii
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

FIELDS = ["title", "band", "mode", "summary", "draft", "body"]
LABELS = {"title": "Title", "band": "Band", "mode": "Mode",
          "summary": "Summary", "draft": "Draft"}
HINT = " ^O Finish   ^P Preview   ^E $EDITOR   ^X Quit   Tab Next field "


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-") or "untitled"


def recovery_path(root: str) -> str:
    # Inside .git so it is never committed and always writable in the repo.
    return os.path.join(root, ".git", "new-post-recovery.json")


class Editor:
    """A minimal but correct scrollable multi-line text editor (list of lines)."""

    def __init__(self, text: str = ""):
        self.lines = text.split("\n") or [""]
        if not self.lines:
            self.lines = [""]
        self.cy = 0   # cursor line
        self.cx = 0   # cursor column
        self.top = 0  # first visible line

    def text(self) -> str:
        return "\n".join(self.lines)

    def insert(self, ch: str) -> None:
        ln = self.lines[self.cy]
        self.lines[self.cy] = ln[:self.cx] + ch + ln[self.cx:]
        self.cx += len(ch)

    def newline(self) -> None:
        ln = self.lines[self.cy]
        rest = ln[self.cx:]
        self.lines[self.cy] = ln[:self.cx]
        self.lines.insert(self.cy + 1, rest)
        self.cy += 1
        self.cx = 0

    def backspace(self) -> None:
        if self.cx > 0:
            ln = self.lines[self.cy]
            self.lines[self.cy] = ln[:self.cx - 1] + ln[self.cx:]
            self.cx -= 1
        elif self.cy > 0:
            prev = self.lines[self.cy - 1]
            self.cx = len(prev)
            self.lines[self.cy - 1] = prev + self.lines[self.cy]
            del self.lines[self.cy]
            self.cy -= 1

    def delete(self) -> None:
        ln = self.lines[self.cy]
        if self.cx < len(ln):
            self.lines[self.cy] = ln[:self.cx] + ln[self.cx + 1:]
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] = ln + self.lines[self.cy + 1]
            del self.lines[self.cy + 1]

    def move(self, key: int, page: int) -> None:
        if key == curses.KEY_LEFT:
            if self.cx > 0:
                self.cx -= 1
            elif self.cy > 0:
                self.cy -= 1
                self.cx = len(self.lines[self.cy])
        elif key == curses.KEY_RIGHT:
            if self.cx < len(self.lines[self.cy]):
                self.cx += 1
            elif self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = 0
        elif key == curses.KEY_UP and self.cy > 0:
            self.cy -= 1
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif key == curses.KEY_DOWN and self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif key == curses.KEY_HOME:
            self.cx = 0
        elif key == curses.KEY_END:
            self.cx = len(self.lines[self.cy])
        elif key == curses.KEY_PPAGE:
            self.cy = max(0, self.cy - page)
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif key == curses.KEY_NPAGE:
            self.cy = min(len(self.lines) - 1, self.cy + page)
            self.cx = min(self.cx, len(self.lines[self.cy]))


class App:
    def __init__(self, root: str, seed: dict):
        self.root = root
        self.fields = {
            "title": seed.get("title", ""),
            "band": seed.get("band", ""),
            "mode": seed.get("mode", ""),
            "summary": seed.get("summary", ""),
        }
        self.fcx = {k: len(v) for k, v in self.fields.items()}  # per-field cursor
        self.draft = bool(seed.get("draft", False))
        self.editor = Editor(seed.get("body", "# \n\n"))
        self.focus = 0
        self.status = "New post — Tab to move, ^O to finish."
        self.finished = False
        self.last_slug = ""

    # ---- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {**self.fields, "draft": self.draft, "body": self.editor.text()}

    # ---- rendering ---------------------------------------------------------
    @staticmethod
    def _put(win, y, x, s, attr=0):
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        s = str(s)[: max(0, w - x - 1)]
        try:
            win.addstr(y, x, s, attr)
        except curses.error:
            pass

    def draw(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bold = curses.A_BOLD
        rev = curses.A_REVERSE

        self._put(stdscr, 0, 0, " N0YEP · New blog post ".ljust(w - 1), rev)

        # front-matter fields
        y = 2
        for i, key in enumerate(["title", "band", "mode", "summary"]):
            focused = self.focus == i
            label = f"{LABELS[key]:>8}: "
            self._put(stdscr, y, 2, label, bold)
            val = self.fields[key]
            self._put(stdscr, y, 2 + len(label), val,
                      curses.A_UNDERLINE if focused else 0)
            y += 1
        # draft toggle
        focused = self.focus == 4
        self._put(stdscr, y, 2, f"{'Draft':>8}: ", bold)
        box = "[x] draft (unpublished)" if self.draft else "[ ] publish now"
        self._put(stdscr, y, 12, box, rev if focused else 0)
        y += 1

        slug = slugify(self.fields["title"])
        self._put(stdscr, y, 2, f"    file: content/blog/{slug}.md", curses.A_DIM)
        y += 2

        # body editor
        self._put(stdscr, y, 2, "Body (Markdown):", bold)
        body_top = y + 1
        body_h = h - body_top - 2
        body_w = w - 4
        if body_h < 2:
            body_h = 2
        self._draw_body(stdscr, body_top, 2, body_h, body_w)

        # status + hint
        self._put(stdscr, h - 2, 0, (" " + self.status).ljust(w - 1),
                  curses.A_DIM)
        self._put(stdscr, h - 1, 0, HINT.ljust(w - 1), rev)

        self._place_cursor(stdscr, body_top, 2, body_h, body_w, y)
        stdscr.refresh()

    def _draw_body(self, stdscr, y0, x0, height, width):
        ed = self.editor
        # keep cursor line in view
        if ed.cy < ed.top:
            ed.top = ed.cy
        elif ed.cy >= ed.top + height:
            ed.top = ed.cy - height + 1
        leftcol = max(0, ed.cx - width + 1)
        for i in range(height):
            row = ed.top + i
            if row < len(ed.lines):
                self._put(stdscr, y0 + i, x0, ed.lines[row][leftcol:leftcol + width])

    def _place_cursor(self, stdscr, body_top, x0, body_h, body_w, form_y):
        if self.focus == 5:  # body
            ed = self.editor
            leftcol = max(0, ed.cx - body_w + 1)
            cy = body_top + (ed.cy - ed.top)
            cx = x0 + (ed.cx - leftcol)
            try:
                stdscr.move(cy, cx)
            except curses.error:
                pass
        elif self.focus < 4:
            key = ["title", "band", "mode", "summary"][self.focus]
            label = f"{LABELS[key]:>8}: "
            try:
                stdscr.move(2 + self.focus, 2 + len(label) + self.fcx[key])
            except curses.error:
                pass

    # ---- input -------------------------------------------------------------
    def edit_field(self, key: str, ch) -> None:
        val = self.fields[key]
        cx = self.fcx[key]
        if isinstance(ch, str):
            if ch in ("\x7f", "\x08"):            # backspace (DEL/BS as string)
                if cx > 0:
                    self.fields[key] = val[:cx - 1] + val[cx:]
                    self.fcx[key] = cx - 1
            elif ch.isprintable():
                self.fields[key] = val[:cx] + ch + val[cx:]
                self.fcx[key] = cx + 1
            return
        if ch in (curses.KEY_BACKSPACE, 127, 8):  # backspace (as key code)
            if cx > 0:
                self.fields[key] = val[:cx - 1] + val[cx:]
                self.fcx[key] = cx - 1
        elif ch == curses.KEY_DC and cx < len(val):
            self.fields[key] = val[:cx] + val[cx + 1:]
        elif ch == curses.KEY_LEFT:
            self.fcx[key] = max(0, cx - 1)
        elif ch == curses.KEY_RIGHT:
            self.fcx[key] = min(len(val), cx + 1)
        elif ch == curses.KEY_HOME:
            self.fcx[key] = 0
        elif ch == curses.KEY_END:
            self.fcx[key] = len(val)

    def handle(self, stdscr, ch) -> bool:
        """Return False to exit the loop."""
        page = max(1, stdscr.getmaxyx()[0] - 8)

        # global control keys (work regardless of focus)
        if ch == "\x0f":                       # ^O finish
            return self.finish(stdscr)
        if ch == "\x18":                       # ^X quit
            return self.quit(stdscr)
        if ch == "\x05":                       # ^E external editor
            self.external_editor(stdscr)
            return True
        if ch == "\x10":                       # ^P preview / save draft
            self.write_post(preview=True)
            return True
        if ch in ("\t", 9):                    # Tab → next field
            self.focus = (self.focus + 1) % len(FIELDS)
            return True
        if ch == curses.KEY_BTAB:              # Shift-Tab → prev field
            self.focus = (self.focus - 1) % len(FIELDS)
            return True

        if self.focus == 5:                    # body editor
            self._body_key(ch, page)
        elif self.focus == 4:                  # draft toggle
            if ch == " " or ch in ("\n", "\r", curses.KEY_ENTER):
                self.draft = not self.draft
        else:                                  # text fields
            key = ["title", "band", "mode", "summary"][self.focus]
            if ch in ("\n", "\r", curses.KEY_ENTER):
                self.focus += 1
            else:
                self.edit_field(key, ch)
        return True

    def _body_key(self, ch, page) -> None:
        ed = self.editor
        if isinstance(ch, str):
            if ch in ("\n", "\r"):
                ed.newline()
            elif ch in ("\x7f", "\x08"):
                ed.backspace()
            elif ch == "\t":
                ed.insert("    ")
            elif ch.isprintable():
                ed.insert(ch)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            ed.backspace()
        elif ch == curses.KEY_DC:
            ed.delete()
        elif ch in (curses.KEY_ENTER,):
            ed.newline()
        else:
            ed.move(ch, page)

    # ---- actions -----------------------------------------------------------
    def _flash(self, stdscr, msg: str) -> None:
        self.status = msg
        self.draw(stdscr)

    def external_editor(self, stdscr) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        fd, tmp = tempfile.mkstemp(suffix=".md", prefix="newpost-")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(self.editor.text())
        curses.endwin()
        try:
            subprocess.call([*editor.split(), tmp])
        finally:
            with open(tmp, encoding="utf-8") as fh:
                self.editor = Editor(fh.read().rstrip("\n") + "\n")
            os.unlink(tmp)
            stdscr.clear()
            curses.doupdate()
        self.status = "Body updated from $EDITOR."

    def _front_matter(self) -> str:
        date = datetime.now().astimezone().isoformat(timespec="seconds")

        def esc(s):
            return s.replace('"', "'")
        return (
            "---\n"
            f'title: "{esc(self.fields["title"])}"\n'
            f"date: {date}\n"
            f'band: "{esc(self.fields["band"])}"\n'
            f'mode: "{esc(self.fields["mode"])}"\n'
            f'summary: "{esc(self.fields["summary"])}"\n'
            f"draft: {'true' if self.draft else 'false'}\n"
            "---\n\n"
        )

    def write_post(self, preview: bool = False) -> str | None:
        title = self.fields["title"].strip()
        if not title:
            self.status = "⚠ Title is required before saving."
            return None
        slug = slugify(title)
        self.last_slug = slug
        rel = os.path.join("content", "blog", f"{slug}.md")
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self._front_matter() + self.editor.text().rstrip("\n") + "\n")
        if preview:
            self.status = (f"Saved {rel} — your hugo server shows it at "
                           f"http://localhost:1313/blog/{slug}/")
        return path

    def finish(self, stdscr) -> bool:
        path = self.write_post(preview=False)
        if not path:
            self.draw(stdscr)
            return True
        # Leave curses to run hugo + git with visible output.
        curses.endwin()
        rel = os.path.relpath(path, self.root)
        print(f"\n▸ Wrote {rel}")

        ok = True
        if which("hugo"):
            print("▸ Building site with Hugo …")
            r = subprocess.run(["hugo", "--gc", "--minify"], cwd=self.root)
            ok = r.returncode == 0
        else:
            print("! hugo not found — skipping build.")

        print("▸ Committing to git …")
        subprocess.run(["git", "add", rel], cwd=self.root)
        commit = subprocess.run(
            ["git", "commit", "-m", f"blog: {self.fields['title'].strip()}"],
            cwd=self.root)
        if commit.returncode == 0:
            ans = input("Push to origin now (triggers deploy)? [y/N] ").strip().lower()
            if ans == "y":
                subprocess.run(["git", "push"], cwd=self.root)
        else:
            print("! git commit reported nothing to commit / failed.")

        clear_recovery(self.root)
        self.finished = True
        print(f"\n✓ Done{'' if ok else ' (with build warnings above)'}. "
              f"73 de N0YEP\n")
        return False

    def quit(self, stdscr) -> bool:
        curses.endwin()
        if self.fields["title"].strip() or self.editor.text().strip("# \n"):
            ans = input("Save a recovery draft to resume later? [Y/n] ").strip().lower()
            if ans != "n":
                with open(recovery_path(self.root), "w", encoding="utf-8") as fh:
                    json.dump(self.snapshot(), fh)
                print("Saved. Resume with:  python3 scripts/new-post.py --resume")
        return False


def which(cmd: str) -> bool:
    return any(os.access(os.path.join(p, cmd), os.X_OK)
               for p in os.environ.get("PATH", "").split(os.pathsep) if p)


def clear_recovery(root: str) -> None:
    try:
        os.unlink(recovery_path(root))
    except OSError:
        pass


def run(stdscr, app: App) -> None:
    curses.raw()          # deliver ^O/^X/^E/^P to us, not the terminal driver
    curses.noecho()
    stdscr.keypad(True)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    running = True
    while running:
        app.draw(stdscr)
        try:
            ch = stdscr.get_wch()
        except curses.error:
            continue
        except KeyboardInterrupt:
            ch = "\x18"
        running = app.handle(stdscr, ch)


def main() -> int:
    p = argparse.ArgumentParser(description="Terminal front-end for N0YEP blog posts.")
    p.add_argument("--title", default="", help="pre-fill the post title")
    p.add_argument("--resume", action="store_true",
                   help="resume the last recovery draft")
    args = p.parse_args()

    root = repo_root()
    seed: dict = {"title": args.title, "body": "# \n\n"}
    if args.resume:
        try:
            with open(recovery_path(root), encoding="utf-8") as fh:
                seed = json.load(fh)
        except OSError:
            print("No recovery draft found.", file=sys.stderr)
            return 1

    if not sys.stdout.isatty():
        print("new-post.py needs an interactive terminal.", file=sys.stderr)
        return 1

    app = App(root, seed)
    curses.wrapper(run, app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
