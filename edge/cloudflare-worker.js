// LeakTrace on Cloudflare Workers: per-viewer A/B switching in front of the bucket that holds the packaged titles.
//
// Origin layout: each title's cmaf/ folder at /<title>/ (an R2 bucket bound as MEDIA, or any HTTPS origin in ORIGIN).
// Route:   <your media host>/wm/*
// Secrets: LEAKTRACE_EDGE_KEY, LEAKTRACE_NAMING_KEY   (hex; `python -m leaktrace edge-keys --out edge.env`)
// Vars:    ORIGIN (e.g. https://media-origin.example.com) unless MEDIA is bound;
//          LEAKTRACE_EDGE_LAYOUT=folders and LEAKTRACE_EDGE_FIRST_SEGMENT for a platform's own packaging (README)
//
// A viewer's player loads /wm/<token>/<title>/master.m3u8 (or manifest.mpd); every segment under it is mapped to
// the shared object for that viewer's variant. Objects are fetched by their own URL, so the cache is shared by
// every viewer with the same variant; only the token in the request path is per viewer.
import core from "./core.js";

const { leaktraceRoute } = core;
const TYPES = { m3u8: "application/vnd.apple.mpegurl", mpd: "application/dash+xml", mp4: "video/mp4", m4s: "video/iso.segment" };
const CORS = { "Access-Control-Allow-Origin": "*" };
const imported = new Map();

async function hmac(keyHex, message) {
  let key = imported.get(keyHex);
  if (!key) {
    const raw = new Uint8Array(keyHex.match(/../g).map((h) => parseInt(h, 16)));
    key = await crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    imported.set(keyHex, key);
  }
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/wm/") || !["GET", "HEAD"].includes(request.method)) {
      return new Response("Not found", { status: 404 });
    }
    const r = await leaktraceRoute(decodeURIComponent(url.pathname.slice(4)), {
      edgeKey: env.LEAKTRACE_EDGE_KEY, namingKey: env.LEAKTRACE_NAMING_KEY, now: Date.now() / 1000, hmac,
      layout: env.LEAKTRACE_EDGE_LAYOUT || "cmaf", first: Number(env.LEAKTRACE_EDGE_FIRST_SEGMENT || 0),
    });
    if (r.status !== 200) return new Response(r.status === 403 ? "Forbidden" : "Not found", { status: r.status, headers: CORS });
    const ext = r.key.split(".").pop();
    const manifest = ext === "m3u8" || ext === "mpd";
    let body;
    if (env.MEDIA) {
      const obj = await env.MEDIA.get(r.key);
      if (!obj) return new Response("Not found", { status: 404, headers: CORS });
      body = obj.body;
    } else {
      const upstream = await fetch(`${env.ORIGIN}/${r.key}`, { cf: { cacheEverything: true, cacheTtl: manifest ? 60 : 31536000 } });
      if (!upstream.ok) return new Response("Not found", { status: 404, headers: CORS });
      body = upstream.body;
    }
    return new Response(body, {
      headers: { ...CORS, "Content-Type": TYPES[ext] || "application/octet-stream",
                 "Cache-Control": manifest ? "public, max-age=60" : "public, max-age=31536000, immutable" },
    });
  },
};
