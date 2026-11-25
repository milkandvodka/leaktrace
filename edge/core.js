// LeakTrace edge core: maps a viewer's request on the shared manifests to the shared object for their variant.
// Same rules as leaktrace/edge.py (the tests run both on the same paths). Plain JavaScript with no imports, so it
// runs in Cloudflare Workers, CloudFront Functions (bundled by `python -m leaktrace edge-bundle`) and Node.
//
// path: "<token>/<title>/<file>" (whatever follows the /wm/ prefix)
// opts: { edgeKey, namingKey (hex strings), now (seconds), hmac: async (keyHex, message) => Uint8Array (SHA-256),
//         layout: "cmaf" (default, LeakTrace's own packaging) or "folders" (a platform's own, A/ and B/),
//         first: number of the first segment in file names (folders layout) }
// returns { status: 200, key: "<title>/<object>" } or { status: 403 | 404 }

var LT_TITLE = /^[a-z0-9][a-z0-9-]{0,63}$/;
var LT_MANIFEST = /^(master\.m3u8|manifest\.mpd|[a-z0-9]+\.m3u8)$/;
var LT_INIT = /^([a-z0-9]+)\/init\.mp4$/;
var LT_SEGMENT = /^([a-z0-9]+)\/(\d{5,6})\.m4s$/;
var LT_FOLDER_SEGMENT = /(\d+)\.(m4s|mp4|ts|cmfv|cmfa|aac|m4v|m4a)$/;
var LT_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function ltB64(bytes) {
  var out = "", i;
  for (i = 0; i + 2 < bytes.length; i += 3) {
    var v = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += LT_B64[v >> 18] + LT_B64[(v >> 12) & 63] + LT_B64[(v >> 6) & 63] + LT_B64[v & 63];
  }
  if (bytes.length - i === 1) {
    out += LT_B64[bytes[i] >> 2] + LT_B64[(bytes[i] & 3) << 4];
  } else if (bytes.length - i === 2) {
    var w = (bytes[i] << 8) | bytes[i + 1];
    out += LT_B64[w >> 10] + LT_B64[(w >> 4) & 63] + LT_B64[(w & 15) << 2];
  }
  return out;
}

function ltUnb64(s) {
  var out = [], acc = 0, nbits = 0;
  for (var i = 0; i < s.length; i++) {
    var v = LT_B64.indexOf(s[i]);
    if (v < 0) return null;
    acc = (acc << 6) | v;
    nbits += 6;
    if (nbits >= 8) {
      nbits -= 8;
      out.push((acc >> nbits) & 255);
    }
  }
  return out;
}

function ltHex(bytes) {
  var out = "";
  for (var i = 0; i < bytes.length; i++) out += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
  return out;
}

function ltSame(a, b) {
  if (a.length !== b.length) return false;
  var d = 0;
  for (var i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function ltFolders(title, file, bits, first) {
  var parts = file.split("/");
  for (var i = 0; i < parts.length; i++) {
    if (parts[i] === "" || parts[i] === "." || parts[i] === "..") return { status: 404 };
  }
  if (file.indexOf("\\") >= 0) return { status: 404 };
  var name = parts[parts.length - 1];
  if (/\.(m3u8|mpd)$/.test(name) || name.indexOf("init") >= 0) return { status: 200, key: title + "/A/" + file };
  var m = LT_FOLDER_SEGMENT.exec(name);
  var n = m ? Number(m[1]) - first : -1;
  if (!(n >= 0 && n < 8 * bits.length)) return { status: 404 };
  return { status: 200, key: title + "/" + (bits[n >> 3] & (0x80 >> (n & 7)) ? "A" : "B") + "/" + file };
}

async function leaktraceRoute(path, opts) {
  var first = path.indexOf("/"), second = first < 0 ? -1 : path.indexOf("/", first + 1);
  if (second < 0) return { status: 404 };
  var token = path.slice(0, first), title = path.slice(first + 1, second), file = path.slice(second + 1);
  if (!LT_TITLE.test(title)) return { status: 404 };
  var t = token.split(".");
  if (t.length !== 3 || !/^\d{1,12}$/.test(t[0])) return { status: 403 };
  var sig = ltB64(Array.prototype.slice.call(await opts.hmac(opts.edgeKey, title + "|" + t[0] + "|" + t[1]), 0, 16));
  var bits = ltUnb64(t[1]);
  if (!ltSame(sig, t[2]) || bits === null) return { status: 403 };
  if (Number(t[0]) < opts.now) return { status: 403 };
  if (opts.layout === "folders") return ltFolders(title, file, bits, Number(opts.first || 0));
  if (LT_MANIFEST.test(file)) return { status: 200, key: title + "/" + file };
  var m = LT_INIT.exec(file);
  if (m) return { status: 200, key: title + "/" + m[1] + "-init.mp4" };
  m = LT_SEGMENT.exec(file);
  if (!m || Number(m[2]) >= 8 * bits.length) return { status: 404 };
  var n = Number(m[2]);
  var variant = bits[n >> 3] & (0x80 >> (n & 7)) ? "A" : "B";
  var name = await opts.hmac(opts.namingKey, title + "|" + m[1] + "|" + n + "|" + variant);
  return { status: 200, key: title + "/" + ltHex(Array.prototype.slice.call(name, 0, 16)) + ".m4s" };
}

if (typeof module !== "undefined") module.exports = { leaktraceRoute: leaktraceRoute };
