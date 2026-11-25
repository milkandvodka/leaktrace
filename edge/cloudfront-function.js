// LeakTrace on Amazon CloudFront: a CloudFront Function (runtime cloudfront-js-2.0, viewer request) on the /wm/*
// cache behavior, whose origin is the S3 bucket holding each title's cmaf/ folder at /<title>/.
//
// Don't deploy this file as it is: `python -m leaktrace edge-bundle cloudfront --out leaktrace-cf.js` writes a copy
// with core.js inlined above this code and the two keys filled in (keep that file private: it holds the keys).
// The function rewrites the request to the viewer's object; CloudFront caches by the rewritten URI, so every object
// is cached once for everyone. Add CORS with a response headers policy (Access-Control-Allow-Origin: *).
var crypto = require("crypto");
var LEAKTRACE_EDGE_KEY = "__LEAKTRACE_EDGE_KEY__";
var LEAKTRACE_NAMING_KEY = "__LEAKTRACE_NAMING_KEY__";
var LEAKTRACE_LAYOUT = "__LEAKTRACE_EDGE_LAYOUT__";
var LEAKTRACE_FIRST = Number("__LEAKTRACE_EDGE_FIRST_SEGMENT__");

async function leaktraceHmac(keyHex, message) {
  var hex = crypto.createHmac("sha256", Buffer.from(keyHex, "hex")).update(message).digest("hex");
  var out = new Uint8Array(hex.length / 2);
  for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(2 * i, 2), 16);
  return out;
}

async function handler(event) {
  var request = event.request;
  if (request.uri.indexOf("/wm/") !== 0) return request;
  var r = await leaktraceRoute(decodeURIComponent(request.uri.slice(4)), {
    edgeKey: LEAKTRACE_EDGE_KEY, namingKey: LEAKTRACE_NAMING_KEY, now: Date.now() / 1000, hmac: leaktraceHmac,
    layout: LEAKTRACE_LAYOUT, first: LEAKTRACE_FIRST,
  });
  if (r.status !== 200) return { statusCode: r.status, statusDescription: r.status === 403 ? "Forbidden" : "Not Found" };
  request.uri = "/" + r.key;
  request.querystring = {};
  return request;
}
