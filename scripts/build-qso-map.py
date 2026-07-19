#!/usr/bin/env python3
"""
build-qso-map.py — turn an ADIF log into data/qso_map.json for the SVG QSO map.

Reads worked-station coordinates from each QSO (ADIF <lat>/<lon>, falling back
to <gridsquare>), aggregates them into unique locations, projects everything
(coastline + points + home) with a plain equirectangular projection, and writes
one self-contained JSON the Hugo partial renders as inline SVG. No runtime deps.

Usage:
    python3 scripts/build-qso-map.py n0yep-logbook.adi
    python3 scripts/build-qso-map.py log.adi --home EN33 -o data/qso_map.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

# ITU-ish continent labels for the breakdown panel.
CONTINENTS = {"NA": "North America", "SA": "South America", "EU": "Europe",
              "AS": "Asia", "AF": "Africa", "OC": "Oceania", "AN": "Antarctica"}

# ---- projection --------------------------------------------------------------
# Equirectangular. Full longitude; latitude trimmed to drop empty polar bands.
VIEW_W = 1000.0
LAT_TOP = 84.0
LAT_BOT = -58.0
VIEW_H = round(VIEW_W * (LAT_TOP - LAT_BOT) / 360.0, 1)   # keep 1:1 deg aspect


def project(lat: float, lon: float) -> tuple[float, float]:
    lon = ((lon + 180.0) % 360.0) - 180.0                # wrap to [-180,180)
    x = (lon + 180.0) / 360.0 * VIEW_W
    lat = max(min(lat, LAT_TOP), LAT_BOT)
    y = (LAT_TOP - lat) / (LAT_TOP - LAT_BOT) * VIEW_H
    return round(x, 1), round(y, 1)


# ---- coordinate parsing ------------------------------------------------------
_LAT_RE = re.compile(r"^([NS])\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$", re.I)
_LON_RE = re.compile(r"^([EW])\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$", re.I)


def parse_adif_coord(val: str, is_lon: bool) -> float | None:
    """ADIF lat/lon look like 'N037 20.812' (deg + decimal minutes)."""
    if not val:
        return None
    m = (_LON_RE if is_lon else _LAT_RE).match(val.strip())
    if not m:
        return None
    hemi, deg, minutes = m.group(1).upper(), float(m.group(2)), float(m.group(3))
    dec = deg + minutes / 60.0
    if hemi in ("S", "W"):
        dec = -dec
    return dec


def grid_to_latlon(grid: str) -> tuple[float, float] | None:
    """Maidenhead locator -> lat/lon of the square's center."""
    g = (grid or "").strip()
    if len(g) < 4:
        return None
    g = g[:6]
    try:
        lon = (ord(g[0].upper()) - 65) * 20 - 180
        lat = (ord(g[1].upper()) - 65) * 10 - 90
        lon += int(g[2]) * 2
        lat += int(g[3]) * 1
        if len(g) >= 6:
            lon += (ord(g[4].lower()) - 97) * (2 / 24)
            lat += (ord(g[5].lower()) - 97) * (1 / 24)
            lon += (2 / 24) / 2
            lat += (1 / 24) / 2
        else:
            lon += 1        # center of the 2°x1° square
            lat += 0.5
    except (ValueError, IndexError):
        return None
    return lat, lon


# ---- ADIF parsing (length-delimited; markers like <EOR> are value-less) ------
_FIELD_RE = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+)(?::[^>]*)?)?>", re.IGNORECASE)


def parse_adif(text: str):
    up = text.upper()
    if "<EOH>" in up:
        text = text[up.index("<EOH>") + 5:]
    rec, i = {}, 0
    while i < len(text):
        m = _FIELD_RE.search(text, i)
        if not m:
            break
        tag = m.group(1).upper()
        if tag == "EOR":
            if rec:
                yield rec
            rec, i = {}, m.end()
            continue
        if m.group(2) is None:
            i = m.end()
            continue
        n = int(m.group(2))
        rec[tag] = text[m.end():m.end() + n].strip()
        i = m.end() + n
    if rec:
        yield rec


def qso_latlon(rec: dict) -> tuple[float, float] | None:
    lat = parse_adif_coord(rec.get("LAT", ""), is_lon=False)
    lon = parse_adif_coord(rec.get("LON", ""), is_lon=True)
    if lat is not None and lon is not None:
        return lat, lon
    return grid_to_latlon(rec.get("GRIDSQUARE", ""))


# ---- coastline ---------------------------------------------------------------
def build_land_path(geojson_path: str) -> str:
    with open(geojson_path) as fh:
        gj = json.load(fh)
    parts: list[str] = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        polys = []
        if geom.get("type") == "Polygon":
            polys = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            polys = geom["coordinates"]
        for poly in polys:
            for ring in poly:
                pts = []
                for lon, lat in ring:
                    x, y = project(lat, lon)
                    pts.append(f"{x},{y}")
                if len(pts) > 2:
                    parts.append("M" + "L".join(pts) + "Z")
    return "".join(parts)


# ---- main --------------------------------------------------------------------
def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="Build data/qso_map.json from ADIF.")
    p.add_argument("adif", help="path to the .adi logbook export")
    p.add_argument("-o", "--output", default="data/qso_map.json")
    p.add_argument("--geojson", default=os.path.join(here, "ne_110m_land.geojson"),
                   help="coastline geojson used for the land outline")
    p.add_argument("--home", default="",
                   help="home grid square (overrides MY_GRIDSQUARE from the log)")
    p.add_argument("--round", type=float, default=1.0,
                   help="degrees to snap locations to when merging (default 1.0)")
    args = p.parse_args()

    try:
        text = open(args.adif, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    buckets: dict[tuple[int, int], dict] = {}
    home_grid = args.home.strip()
    dxcc: set[str] = set()
    continents: Counter = Counter()
    countries: Counter = Counter()
    total = 0
    r = args.round

    for rec in parse_adif(text):
        total += 1
        if rec.get("DXCC"):
            dxcc.add(rec["DXCC"])
        elif rec.get("COUNTRY"):
            dxcc.add(rec["COUNTRY"].upper())
        if rec.get("CONT"):
            continents[rec["CONT"].upper()] += 1
        if rec.get("COUNTRY"):
            countries[rec["COUNTRY"].strip()] += 1
        if not home_grid:
            home_grid = rec.get("MY_GRIDSQUARE", "") or home_grid
        ll = qso_latlon(rec)
        if not ll:
            continue
        lat, lon = ll
        key = (round(lat / r), round(lon / r))
        b = buckets.setdefault(key, {
            "lat": lat, "lon": lon, "count": 0,
            "call": rec.get("CALL", ""), "bandc": Counter(), "modes": set(),
        })
        b["count"] += 1
        if rec.get("BAND"):
            b["bandc"][rec["BAND"].lower()] += 1
        if rec.get("MODE"):
            b["modes"].add(rec["MODE"].upper())

    # home coordinates
    home_ll = grid_to_latlon(home_grid) if home_grid else None
    home = None
    if home_ll:
        hx, hy = project(*home_ll)
        home = {"x": hx, "y": hy, "la": round(home_ll[0], 2),
                "lo": round(home_ll[1], 2), "grid": home_grid.upper()}

    points = []
    for b in buckets.values():
        x, y = project(b["lat"], b["lon"])
        top_band = b["bandc"].most_common(1)[0][0] if b["bandc"] else ""
        points.append({
            "x": x, "y": y, "la": round(b["lat"], 2), "lo": round(b["lon"], 2),
            "n": b["count"], "call": b["call"], "band": top_band,
            "bands": sorted(b["bandc"]), "modes": sorted(b["modes"]),
        })
    points.sort(key=lambda d: d["n"], reverse=True)

    cont_list = [{"code": c, "name": CONTINENTS.get(c, c), "n": n}
                 for c, n in continents.most_common()]
    country_list = [{"name": c, "n": n} for c, n in countries.most_common(12)]

    out = {
        "view": {"w": VIEW_W, "h": VIEW_H, "latTop": LAT_TOP, "latBot": LAT_BOT},
        "land": build_land_path(args.geojson),
        "home": home,
        "points": points,
        "stats": {
            "qsos": total,
            "locations": len(points),
            "entities": len(dxcc),
            "mapped": sum(pt["n"] for pt in points),
            "continents": cont_list,
            "countries": country_list,
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    s = out["stats"]
    print(f"Wrote {args.output}: {s['locations']} locations from "
          f"{s['mapped']}/{s['qsos']} QSOs, {s['entities']} entities, "
          f"home {home['grid'] if home else '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
