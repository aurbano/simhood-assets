// pnpm --dir tools render -- --repo <root> --report <report.json> --out <dir> [--only <id,id>]
//   [--base-repo <root> --base-report <report.json>] [--examples]
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { Renderer, specFor, threeVersion, type Colour, type Rendered, type Tree } from "./renderer.ts";
import type { Report, ReportModel } from "./report.ts";

interface IndexEntry {
  image: string | null;
  tinted: string | null;
  before: string | null;
  triangles: number | null;
  tint_coverage: number | null;
  tint_coverage_review: number | null;
  colours: Colour[] | null;
  error: string | null;
}

const USAGE = `usage: render --repo <root> --report <report.json> --out <dir> [--only <id,id>]
              [--base-repo <root> --base-report <report.json>] [--examples]`;

/** `kenney:delivery` becomes `kenney__delivery`. */
export function fileStem(id: string): string {
  return id.replaceAll(":", "__");
}

async function readReport(file: string): Promise<Report> {
  const report = JSON.parse(await readFile(file, "utf8")) as Report;
  if (report.version !== 1 || !Array.isArray(report.models)) throw new Error(`${file}: not a version 1 validator report`);
  return report;
}

async function same(a: string, b: string): Promise<boolean> {
  try {
    return Buffer.compare(await readFile(a), await readFile(b)) === 0;
  } catch {
    return false;
  }
}

/** A base model needs a before picture when its files or how the client reads them differ. */
async function changed(head: ReportModel, headRepo: string, base: ReportModel, baseRepo: string): Promise<boolean> {
  const keys = ["measure", "measure_value", "forward", "factor", "flat_shading", "tint_key", "tint_tolerance"] as const;
  if (keys.some((k) => head[k] !== base[k])) return true;
  const files = new Set([head.model_path, ...(head.model_files ?? [])]);
  const baseFiles = new Set([base.model_path, ...(base.model_files ?? [])]);
  if (files.size !== baseFiles.size) return true;
  for (const file of files) {
    if (!baseFiles.has(file) || !(await same(path.join(headRepo, file), path.join(baseRepo, file)))) return true;
  }
  return false;
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2);
  if (argv[0] === "--") argv.shift();
  const { values } = parseArgs({
    args: argv,
    options: {
      repo: { type: "string" },
      report: { type: "string" },
      out: { type: "string" },
      only: { type: "string" },
      "base-repo": { type: "string" },
      "base-report": { type: "string" },
      examples: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  const { repo, report: reportFile, out } = values;
  if (!repo || !reportFile || !out || (values["base-repo"] === undefined) !== (values["base-report"] === undefined)) {
    console.error(USAGE);
    return 2;
  }
  const report = await readReport(reportFile);
  const only = values.only ? new Set(values.only.split(",").map((s) => s.trim()).filter(Boolean)) : null;
  const wanted = (m: ReportModel) => only === null || only.has(m.id);
  const baseRepo = values["base-repo"];
  const baseReport = baseRepo ? await readReport(values["base-report"] as string) : null;
  const baseModels = baseReport ? new Map(baseReport.models.map((m) => [m.id, m])) : null;

  const renderer = await Renderer.open(repo, baseRepo);
  const models: Record<string, IndexEntry> = {};
  const examples: Record<string, IndexEntry> = {};
  let failures = 0;
  try {
    const draw = async (
      model: ReportModel,
      target: Record<string, IndexEntry>,
      dir: string,
      stem: string,
      tree: Tree,
      full: boolean,
    ) => {
      const entry: IndexEntry = {
        image: null, tinted: null, before: null, triangles: null,
        tint_coverage: null, tint_coverage_review: null, colours: null, error: null,
      };
      target[model.id] = entry;
      const rel = (suffix: string) => (dir === "" ? `${stem}${suffix}` : `${dir}/${stem}${suffix}`);
      try {
        const result: Rendered = await renderer.render(specFor(model, tree), {
          image: true,
          tinted: full,
          analyse: full,
        });
        await mkdir(path.dirname(path.join(out, rel(".png"))), { recursive: true });
        await writeFile(path.join(out, rel(".png")), result.image as Buffer);
        entry.image = rel(".png");
        entry.triangles = result.triangles;
        entry.colours = result.colours;
        if (result.tinted) {
          await writeFile(path.join(out, rel(".tinted.png")), result.tinted);
          entry.tinted = rel(".tinted.png");
          entry.tint_coverage = result.tintCoverage;
          entry.tint_coverage_review = result.tintCoverageReview;
        }
      } catch (error) {
        entry.error = error instanceof Error ? error.message : String(error);
        failures++;
        console.error(`ERROR ${model.id}: ${entry.error}`);
        return;
      }
      const base = baseModels?.get(model.id);
      if (full && baseRepo && base && (await changed(model, repo, base, baseRepo))) {
        try {
          const before = await renderer.render(specFor(base, "base"), { image: true, tinted: false, analyse: false });
          await writeFile(path.join(out, rel(".before.png")), before.image as Buffer);
          entry.before = rel(".before.png");
        } catch (error) {
          entry.error = `before picture: ${error instanceof Error ? error.message : String(error)}`;
          failures++;
          console.error(`ERROR ${model.id}: ${entry.error}`);
        }
      }
    };

    await mkdir(out, { recursive: true });
    for (const model of report.models.filter(wanted)) {
      await draw(model, models, model.pack, fileStem(model.id), "repo", true);
    }
    if (values.examples) {
      for (const example of (report.examples ?? []).filter(wanted)) {
        await draw(example, examples, "examples", fileStem(example.id), "repo", false);
      }
    }
    const index = {
      version: 1,
      renderer: `three ${threeVersion()} / chromium ${renderer.chromiumVersion} / swiftshader`,
      models,
      examples,
    };
    await writeFile(path.join(out, "index.json"), `${JSON.stringify(index, null, 2)}\n`);
  } finally {
    await renderer.close();
  }
  console.log(`rendered ${Object.keys(models).length} models, ${Object.keys(examples).length} examples, ${failures} errors`);
  return 0;
}

process.exitCode = await main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  return 2;
});
