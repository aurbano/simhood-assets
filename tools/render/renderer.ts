// Drives the renderer's page in headless Chromium (software WebGL, as the dashboard's Playwright
// config does) and turns report entries into pictures.
import { chromium, type Browser, type Page } from "playwright";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Colour, Options, Result, Spec } from "./page/scene.ts";
import type { ReportModel } from "./report.ts";
import { startServer, threeRoot, type AssetServer } from "./server.ts";

export type { Colour, Options, Result, Spec };

export const MODEL_TIMEOUT_MS = 30_000;

const LAUNCH_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];

export interface Rendered extends Omit<Result, "image" | "tinted"> {
  image: Buffer | null;
  tinted: Buffer | null;
}

export function threeVersion(): string {
  const manifest = path.join(threeRoot(), "package.json");
  return (JSON.parse(readFileSync(manifest, "utf8")) as { version: string }).version;
}

/** Which tree a model's files are read from. */
export type Tree = "repo" | "base";

/** `sourceMaterials` draws the model with its own materials, for the rejected examples. */
export function specFor(model: ReportModel, tree: Tree, sourceMaterials = false): Spec {
  const url = `/${tree}/${model.model_path.split("/").map(encodeURIComponent).join("/")}`;
  return {
    url,
    forward: model.forward ?? "+x",
    measure: model.measure,
    measureValue: model.measure_value,
    factor: model.factor,
    flatShading: model.flat_shading ?? false,
    tintKey: model.tint_key,
    tintTolerance: model.tint_tolerance ?? 0.25,
    sourceMaterials,
  };
}

export class Renderer {
  private server!: AssetServer;
  private browser!: Browser;
  private page: Page | null = null;
  chromiumVersion = "";

  static async open(repo: string, base?: string): Promise<Renderer> {
    const renderer = new Renderer();
    renderer.server = await startServer(repo, base);
    try {
      renderer.browser = await chromium.launch({ headless: true, args: LAUNCH_ARGS });
    } catch (error) {
      await renderer.server.close();
      throw error;
    }
    renderer.chromiumVersion = renderer.browser.version();
    return renderer;
  }

  private async ensurePage(): Promise<Page> {
    if (this.page) return this.page;
    const page = await this.browser.newPage({ viewport: { width: 512, height: 512 } });
    const problems: string[] = [];
    page.on("pageerror", (error) => problems.push(error.message));
    await page.goto(`${this.server.origin}/`);
    try {
      await page.waitForFunction("window.simhoodReady === true", undefined, { timeout: 15_000 });
    } catch {
      await page.close();
      throw new Error(`the renderer page did not start: ${problems.join("; ") || "no error reported"}`);
    }
    this.page = page;
    return page;
  }

  /** Loads and draws one model; rejects on any failure, and after `MODEL_TIMEOUT_MS`. */
  async render(spec: Spec, options: Options): Promise<Rendered> {
    const page = await this.ensurePage();
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`timed out after ${MODEL_TIMEOUT_MS / 1000} s`)), MODEL_TIMEOUT_MS);
    });
    try {
      const result = await Promise.race([
        page.evaluate(
          ([s, o]) => (window as unknown as { simhood: { run(s: Spec, o: Options): Promise<Result> } }).simhood.run(s, o),
          [spec, options] as const,
        ),
        timeout,
      ]);
      return {
        ...result,
        image: result.image === null ? null : Buffer.from(result.image, "base64"),
        tinted: result.tinted === null ? null : Buffer.from(result.tinted, "base64"),
      };
    } catch (error) {
      // The page may be stuck or in a bad state: the next model gets a fresh one.
      this.page = null;
      void page.close().catch(() => undefined);
      throw new Error(firstLine(error));
    } finally {
      clearTimeout(timer);
    }
  }

  async close(): Promise<void> {
    await this.browser.close();
    await this.server.close();
  }
}

function firstLine(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return text.split("\n")[0];
}
