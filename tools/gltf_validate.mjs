// Runs the Khronos glTF Validator over the given files and prints one JSON document:
//   {"version": "<validator version>", "files": {"<path as given>": {"errors": [{"code", "message", "pointer"}], "warnings": <n>, "fatal": <string or null>}}}
// A `.gltf` may read files beside it (inside its own folder); nothing else is ever read.
// Usage: node gltf_validate.mjs <file.glb|file.gltf>...
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(fileURLToPath(import.meta.url));
const validator = require("gltf-validator");

const MAX_ERRORS_PER_FILE = 10;

async function check(file) {
  const full = path.resolve(file);
  const dir = path.dirname(full);
  const bytes = new Uint8Array(await readFile(full));
  try {
    const report = await validator.validateBytes(bytes, {
      uri: path.basename(full),
      maxIssues: 0,
      writeTimestamp: false,
      externalResourceFunction: async (uri) => {
        const target = path.resolve(dir, decodeURIComponent(uri));
        if (target !== dir && !target.startsWith(dir + path.sep)) {
          throw new Error(`resource ${uri} lies outside the model's folder`);
        }
        return new Uint8Array(await readFile(target));
      },
    });
    const errors = [];
    const seen = new Set();
    for (const m of report.issues.messages) {
      if (m.severity !== 0 || seen.has(m.code)) continue;
      seen.add(m.code);
      if (errors.length < MAX_ERRORS_PER_FILE) {
        errors.push({ code: m.code, message: m.message, pointer: m.pointer ?? null });
      }
    }
    return { errors, warnings: report.issues.numWarnings, fatal: null };
  } catch (e) {
    return { errors: [], warnings: 0, fatal: String(e) };
  }
}

const files = process.argv.slice(2);
const out = { version: validator.version(), files: {} };
for (const file of files) {
  try {
    out.files[file] = await check(file);
  } catch (e) {
    out.files[file] = { errors: [], warnings: 0, fatal: String(e) };
  }
}
process.stdout.write(JSON.stringify(out) + "\n");
