// A local HTTP server that gives the renderer's page three's ES modules, the page's own scripts
// and the model files, and nothing else: it binds 127.0.0.1 and serves four fixed roots.
import { createServer, type Server } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".ts": "text/javascript; charset=utf-8",
  ".wasm": "application/wasm",
  ".json": "application/json",
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
  ".bin": "application/octet-stream",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

export function threeRoot(): string {
  // The package does not export its package.json: go up from build/three.module.js.
  return path.dirname(path.dirname(fileURLToPath(import.meta.resolve("three"))));
}

export interface AssetServer {
  /** `http://127.0.0.1:<port>` */
  origin: string;
  close(): Promise<void>;
}

/**
 * Serves `/page/` (this directory's `page/`, `.ts` with its types stripped), `/three/` (the three
 * package), `/repo/` (the repository being drawn) and, when given, `/base/` (the base tree).
 */
export async function startServer(repo: string, base?: string): Promise<AssetServer> {
  const roots: Record<string, string> = {
    page: path.join(here, "page"),
    three: threeRoot(),
    repo: path.resolve(repo),
  };
  if (base) roots.base = path.resolve(base);

  const server: Server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      const parts = decodeURIComponent(url.pathname).split("/").filter((p) => p.length > 0);
      if (url.pathname === "/") parts.splice(0, 0, "page", "index.html");
      const root = roots[parts[0] ?? ""];
      if (!root || parts.some((p) => p === ".." || p === "." || p.includes("\0"))) {
        res.writeHead(404).end("not found");
        return;
      }
      const file = path.join(root, ...parts.slice(1));
      const info = await stat(file);
      if (!info.isFile()) {
        res.writeHead(404).end("not found");
        return;
      }
      const ext = path.extname(file).toLowerCase();
      let body: Buffer | string = await readFile(file);
      if (ext === ".ts") body = stripTypeScriptTypes(body.toString("utf8"), { mode: "strip" });
      res.writeHead(200, {
        "content-type": TYPES[ext] ?? "application/octet-stream",
        "cache-control": "no-store",
      });
      res.end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("no server address");
  return {
    origin: `http://127.0.0.1:${address.port}`,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
