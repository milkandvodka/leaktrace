// Runs a bundled CloudFront Function (argv[2]) on /wm/ paths from stdin, printing each rewritten URI or status.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const handler = new Function("require", "Buffer", readFileSync(process.argv[2], "utf8") + "\nreturn handler;")(
  createRequire(import.meta.url), Buffer);
const paths = JSON.parse(readFileSync(0, "utf8"));
const out = [];
for (const p of paths) {
  const r = await handler({ request: { uri: "/wm/" + p, querystring: {} } });
  out.push(r.statusCode ? r.statusCode : r.uri);
}
process.stdout.write(JSON.stringify(out));
