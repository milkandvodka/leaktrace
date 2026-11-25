// Runs the JS edge core on paths from stdin (JSON) and prints what it maps each to, for test_edge.py.
import { createHmac } from "node:crypto";
import { createRequire } from "node:module";

const { leaktraceRoute } = createRequire(import.meta.url)("../edge/core.js");
const job = JSON.parse(await new Promise((ok) => {
  let s = "";
  process.stdin.on("data", (d) => (s += d)).on("end", () => ok(s));
}));
const hmac = async (keyHex, msg) => new Uint8Array(createHmac("sha256", Buffer.from(keyHex, "hex")).update(msg).digest());
const out = [];
for (const path of job.paths) {
  const r = await leaktraceRoute(path, { edgeKey: job.edgeKey, namingKey: job.namingKey, now: job.now, hmac,
                                         layout: job.layout, first: job.first });
  out.push(r.status === 200 ? { status: 200, key: r.key } : { status: r.status });
}
process.stdout.write(JSON.stringify(out));
