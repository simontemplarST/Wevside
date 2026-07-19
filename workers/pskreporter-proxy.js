/**
 * pskreporter-proxy — a tiny Cloudflare Worker that lets the browser query
 * PSKReporter for the on-air widget.
 *
 * PSKReporter's retrieve API sends no CORS headers, so a page on cameronheard.com
 * can't fetch it directly. This Worker fetches it server-side, adds permissive
 * CORS, and caches the response for 5 minutes (PSKReporter asks for no more than
 * one query every 5 minutes — the cache enforces that no matter how many
 * visitors hit the badge).
 *
 * Deploy (free tier):
 *   1. npm i -g wrangler   (or use the dashboard "Quick edit")
 *   2. wrangler deploy workers/pskreporter-proxy.js --name pskreporter-proxy
 *   3. Set hugo.toml:  onAirProxy = "https://pskreporter-proxy.<you>.workers.dev/?url="
 *
 * Security: only proxies PSKReporter URLs, so it can't be abused as an open proxy.
 */

const ALLOW_ORIGIN = "*"; // tighten to "https://cameronheard.com" if you prefer
const CACHE_TTL = 300;    // seconds

export default {
  async fetch(request, _env, ctx) {
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));
    if (request.method !== "GET") return cors(new Response("method not allowed", { status: 405 }));

    const target = new URL(request.url).searchParams.get("url");
    if (!target || !target.startsWith("https://retrieve.pskreporter.info/")) {
      return cors(new Response("only retrieve.pskreporter.info is proxied", { status: 400 }));
    }

    const cache = caches.default;
    const cacheKey = new Request("https://psk-cache/" + encodeURIComponent(target));
    let response = await cache.match(cacheKey);

    if (!response) {
      const upstream = await fetch(target, {
        headers: { "User-Agent": "onair-widget (github.com/simontemplarST/Wevside)" },
      });
      response = new Response(upstream.body, upstream);
      response.headers.set("Cache-Control", `public, max-age=${CACHE_TTL}`);
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }

    return cors(response);
  },
};

function cors(res) {
  const out = new Response(res.body, res);
  out.headers.set("Access-Control-Allow-Origin", ALLOW_ORIGIN);
  out.headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
  out.headers.set("Access-Control-Max-Age", "86400");
  return out;
}
