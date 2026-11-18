import { timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { run } from "./ready-commands.mjs";

export function createReadyTickServer(secret, runPass) {
  if (!/^[a-f0-9]{64}$/.test(secret)) throw new Error("Ready tick requires a 32-byte lowercase hex bearer");
  const expected = Buffer.from(secret);
  let active = false;
  return createServer({ requestTimeout: 5000, headersTimeout: 5000 }, (req, res) => {
    if (req.method !== "POST" || req.url !== "/tick") return res.writeHead(404).end();
    if (req.headers.origin) return res.writeHead(403).end();
    const token = /^Bearer ([a-f0-9]{64})$/.exec(req.headers.authorization ?? "")?.[1];
    if (!token || !timingSafeEqual(Buffer.from(token), expected)) {
      return res.writeHead(401, { "WWW-Authenticate": "Bearer" }).end();
    }
    if (req.headers["transfer-encoding"] || (req.headers["content-length"] ?? "0") !== "0") {
      return res.writeHead(400).end();
    }
    res.writeHead(202, { "Content-Type": "application/json" });
    if (active) return res.end('{"status":"busy"}');
    active = true;
    res.end('{"status":"accepted"}');
    void Promise.resolve().then(runPass).catch(() => console.error("Ready pass failed"))
      .finally(() => { active = false; });
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = process.env.RALPH_READY_CONFIG;
  if (!file || !isAbsolute(file)) throw new Error("Set RALPH_READY_CONFIG to a private absolute config path");
  const ids = Object.keys(JSON.parse(readFileSync(file, "utf8")).jobs ?? {});
  if (!ids.length || ids.some((id) => !/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))) {
    throw new Error("Ready tick requires operator-configured positive ticket IDs");
  }
  const secret = process.env.RALPH_READY_TICK_TOKEN;
  delete process.env.RALPH_READY_TICK_TOKEN;
  const port = Number(process.env.RALPH_READY_TICK_PORT ?? 8766);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid Ready tick port");
  createReadyTickServer(secret, async () => {
    for (const id of ids) {
      for (const script of ["run-ready-ticket.mjs", "run-ready-merge.mjs"]) {
        try {
          const result = await run(process.execPath, [resolve(import.meta.dirname, script), id], { timeout: 22 * 60_000 });
          console.log(`${script} ticket=${id} status=${JSON.parse(result).status}`);
        } catch {
          console.error(`${script} ticket=${id} failed`);
        }
      }
    }
  }).listen(port, "127.0.0.1");
}
