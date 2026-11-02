import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { PNG } from "pngjs";
import { Renderer, specFor, type Rendered } from "../renderer.ts";
import type { Report, ReportModel } from "../report.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const report = JSON.parse(readFileSync(path.join(here, "fixtures/report.json"), "utf8")) as Report;
const model = (id: string): ReportModel => {
  const found = report.models.find((m) => m.id === id);
  assert.ok(found, `${id} is in the fixture report`);
  return found;
};
const none = { image: false, tinted: false, analyse: false };

interface GltfJson {
  scenes?: { nodes?: number[] }[];
  scene?: number;
  nodes: { mesh?: number; children?: number[] }[];
  meshes: { primitives: { mode?: number; indices?: number; attributes: { POSITION: number } }[] }[];
  accessors: { count: number }[];
}

function gltfJson(file: string): GltfJson {
  const bytes = readFileSync(path.join(repo, file));
  if (!file.endsWith(".glb")) return JSON.parse(bytes.toString("utf8")) as GltfJson;
  const length = bytes.readUInt32LE(12);
  return JSON.parse(bytes.subarray(20, 20 + length).toString("utf8")) as GltfJson;
}

/** Triangles per mesh instance (a node using a mesh), counted from the glTF itself. */
function countTriangles(file: string): number {
  const json = gltfJson(file);
  const perMesh = json.meshes.map((mesh) =>
    mesh.primitives.reduce((sum, p) => {
      const count = json.accessors[p.indices ?? p.attributes.POSITION].count;
      const mode = p.mode ?? 4;
      return sum + (mode === 4 ? count / 3 : mode === 5 || mode === 6 ? Math.max(count - 2, 0) : 0);
    }, 0),
  );
  const roots = json.scenes?.[json.scene ?? 0]?.nodes ?? json.nodes.map((_, i) => i);
  let total = 0;
  const visit = (index: number) => {
    const node = json.nodes[index];
    if (node.mesh !== undefined) total += perMesh[node.mesh];
    node.children?.forEach(visit);
  };
  roots.forEach(visit);
  return total;
}

describe("renderer", () => {
  let renderer: Renderer;
  before(async () => {
    renderer = await Renderer.open(repo);
  });
  after(async () => {
    await renderer.close();
  });

  it("sees the triangle count the report says", async () => {
    for (const m of report.models) {
      const result = await renderer.render(specFor(m, "repo"), none);
      assert.equal(result.triangles, m.triangles, `${m.id}: three.js against the fixture report`);
      assert.equal(result.triangles, countTriangles(m.model_path), `${m.id}: three.js against the glTF`);
    }
  });

  it("stands the model on the ground with the measured size", async () => {
    for (const m of report.models) {
      const r = await renderer.render(specFor(m, "repo"), none);
      assert.ok(Math.abs(r.min[1]) < 1e-4, `${m.id}: min y ${r.min[1]}`);
      assert.ok(Math.abs(r.min[0] + r.max[0]) < 1e-4 && Math.abs(r.min[2] + r.max[2]) < 1e-4, `${m.id}: centred`);
      const extent = r.max.map((v, i) => v - r.min[i]);
      const wanted = m.measure_value as number;
      if (m.measure === "length") assert.ok(Math.abs(extent[0] - wanted) < 1e-4, `${m.id}: length ${extent[0]}`);
      if (m.measure === "height") assert.ok(Math.abs(extent[1] - wanted) < 1e-4, `${m.id}: height ${extent[1]}`);
    }
    // A +z model is turned: the delivery van's z extent (1.5 model units) becomes the width.
    const van = await renderer.render(specFor(model("kenney:delivery"), "repo"), none);
    assert.ok(Math.abs(van.max[2] - van.min[2] - 1.5 * van.factor) < 1e-4);
  });

  it("covers some of a tinted model and none for a key that matches nothing", async () => {
    const bush = model("kaykit:bush_1_b");
    const all = { image: false, tinted: true, analyse: true };
    const tinted: Rendered = await renderer.render(specFor(bush, "repo"), all);
    assert.ok((tinted.tintCoverage ?? 0) > 0, `coverage ${tinted.tintCoverage}`);
    assert.ok((tinted.tintCoverageReview ?? 0) > 0, `review coverage ${tinted.tintCoverageReview}`);
    assert.ok(tinted.tinted !== null);

    const nothing = await renderer.render(specFor({ ...bush, tint_key: "#ff00ff", tint_tolerance: 0.05 }, "repo"), all);
    assert.equal(nothing.tintCoverage, 0);
    assert.equal(nothing.tintCoverageReview, 0);
  });

  it("draws an opaque 512 by 512 picture and lists its colours", async () => {
    const r = await renderer.render(specFor(model("kenney:delivery"), "repo"), { image: true, tinted: true, analyse: true });
    assert.ok(r.image);
    const png = PNG.sync.read(r.image);
    assert.equal(png.width, 512);
    assert.equal(png.height, 512);
    for (let i = 3; i < png.data.length; i += 4) assert.equal(png.data[i], 255);
    assert.equal(r.tinted, null, "no tint key, no tinted picture");
    assert.ok(r.colours && r.colours.length > 0 && r.colours.length <= 8);
    assert.ok(r.colours.every((c) => /^#[0-9a-f]{6}$/.test(c.hex) && c.share > 0 && c.share <= 1));
  });

  it("reports a model that does not load and carries on", async () => {
    const missing = { ...model("kenney:delivery"), model_path: "packs/kenney/vehicles/does-not-exist.glb" };
    await assert.rejects(renderer.render(specFor(missing, "repo"), none));
    const result = await renderer.render(specFor(model("kenney:delivery"), "repo"), none);
    assert.equal(result.triangles, 2476);
  });
});
