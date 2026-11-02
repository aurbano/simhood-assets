// The renderer's page: loads a model, normalises it the way the game client does (the contract's
// "how the client normalises a model", godot/client/render/asset_packs.gd `_build` and `_turn`),
// draws it in the review look and runs the tint-mask and colour passes. Runs in Chromium; only
// erasable TypeScript, because the server strips the types.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

export interface Spec {
  /** URL of the model's main file (`.glb` or `.gltf`) on the page's server. */
  url: string;
  forward: string;
  measure: string | null;
  measureValue: number | null;
  /** The validator's factor; computed from `measure` when absent, 1 when that is absent too. */
  factor: number | null;
  flatShading: boolean;
  tintKey: string | null;
  tintTolerance: number;
}

export interface Options {
  /** Draw the review picture. */
  image: boolean;
  /** Draw the picture with the tint in the test colour (only for a model with a tint key). */
  tinted: boolean;
  /** Run the tint-mask and colour passes. */
  analyse: boolean;
}

export interface Colour {
  hex: string;
  share: number;
  source: "material" | "texture";
}

export interface Result {
  triangles: number;
  /** The extent along each axis before scaling, after the turn. */
  rawExtent: [number, number, number];
  factor: number;
  /** The bounds after the anchor and the factor. */
  min: [number, number, number];
  max: [number, number, number];
  image: string | null;
  tinted: string | null;
  tintCoverage: number | null;
  tintCoverageReview: number | null;
  colours: Colour[] | null;
}

const SIZE = 512;
const TEST_TINT = "#d1177e";
const FOV = 30;
const MARGIN = 1.08;
const REVIEW_YAW = 35;
const REVIEW_ELEVATION = 35;

const TINT_COMMON = /* glsl */ `
uniform int uTintMode;
uniform vec3 uTintKey;
uniform vec3 uTintColour;
uniform float uTintTolerance;
float simLuma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec3 simHue(vec3 c) { return c / max(max(c.r, max(c.g, c.b)), 1e-4); }
`;

// Ported from godot/client/render/asset_tint.gdshader. Mode 0 keeps the model's colours, 1 paints
// the tint mask in the test colour, 2 writes the mask itself (white on black, unlit).
const TINT_FRAGMENT = /* glsl */ `
float simTintMask = 0.0;
if (uTintMode > 0) {
  vec3 c = diffuseColor.rgb;
  float away = distance(simHue(c), simHue(uTintKey));
  simTintMask = 1.0 - smoothstep(uTintTolerance * 0.6, uTintTolerance, away);
  float shade = pow(min(simLuma(c) / max(simLuma(uTintKey), 1e-4), 1.6), 0.7);
  if (uTintMode == 1) {
    diffuseColor.rgb = mix(c, uTintColour * shade, simTintMask);
  }
}
`;

interface Entry {
  lit: THREE.MeshPhysicalMaterial;
  id: THREE.ShaderMaterial;
  colour: THREE.ShaderMaterial;
  index: number;
  base: THREE.Color;
  textured: boolean;
  uniforms: Record<string, THREE.IUniform>;
}

interface Loaded {
  group: THREE.Group;
  meshes: THREE.Mesh[];
  entries: Entry[];
  triangles: number;
  rawExtent: THREE.Vector3;
  factor: number;
  box: THREE.Box3;
  dispose(): void;
}

function turnAngle(forward: string): number {
  switch (forward) {
    case "-x":
      return Math.PI;
    case "+z":
      return Math.PI * 0.5;
    case "-z":
      return -Math.PI * 0.5;
    default:
      return 0;
  }
}

function srgb(hex: string): THREE.Color {
  return new THREE.Color().setStyle(hex, THREE.SRGBColorSpace);
}

function colourUniforms(spec: Spec): Record<string, THREE.IUniform> {
  const key = spec.tintKey === null ? new THREE.Color(1, 1, 1) : srgb(spec.tintKey);
  return {
    uTintMode: { value: 0 },
    uTintKey: { value: new THREE.Vector3(key.r, key.g, key.b) },
    uTintColour: { value: new THREE.Vector3() },
    uTintTolerance: { value: spec.tintTolerance },
  };
}

function litMaterial(source: THREE.Material | undefined, spec: Spec, uniforms: Record<string, THREE.IUniform>) {
  const src = source as THREE.MeshStandardMaterial | undefined;
  const material = new THREE.MeshPhysicalMaterial({
    color: src?.color ? src.color.clone() : new THREE.Color(1, 1, 1),
    map: src?.map ?? null,
    roughness: 0.92,
    metalness: 0,
    // Godot's SPECULAR 0.3 against its default 0.5.
    specularIntensity: 0.6,
    flatShading: spec.flatShading,
  });
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\n${TINT_COMMON}`)
      .replace("#include <map_fragment>", `#include <map_fragment>\n${TINT_FRAGMENT}`)
      .replace(
        "#include <dithering_fragment>",
        `#include <dithering_fragment>\nif (uTintMode == 2) { gl_FragColor = vec4(vec3(simTintMask), 1.0); }`,
      );
  };
  material.customProgramCacheKey = () => "simhood-tint";
  return material;
}

function idMaterial(index: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: { uId: { value: index } },
    vertexShader: "void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: "uniform float uId; void main() { gl_FragColor = vec4(uId / 255.0, 0.0, 0.0, 1.0); }",
  });
}

// Unlit colour times texture, written as sRGB bytes (a render target is linear).
function colourMaterial(source: THREE.Material | undefined): THREE.ShaderMaterial {
  const src = source as THREE.MeshStandardMaterial | undefined;
  const map = src?.map ?? null;
  const uvMatrix = map ? map.matrix : new THREE.Matrix3();
  return new THREE.ShaderMaterial({
    uniforms: {
      uColour: { value: src?.color ? src.color.clone() : new THREE.Color(1, 1, 1) },
      uMap: { value: map },
      uHasMap: { value: map !== null },
      uMapMatrix: { value: uvMatrix },
    },
    vertexShader: `
      uniform mat3 uMapMatrix;
      varying vec2 vUv;
      void main() {
        vUv = (uMapMatrix * vec3(uv, 1.0)).xy;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform vec3 uColour;
      uniform sampler2D uMap;
      uniform bool uHasMap;
      varying vec2 vUv;
      vec3 toSrgb(vec3 c) {
        return mix(1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, c * 12.92, lessThanEqual(c, vec3(0.0031308)));
      }
      void main() {
        vec3 c = uColour;
        if (uHasMap) c *= texture2D(uMap, vUv).rgb;
        gl_FragColor = vec4(toSrgb(c), 1.0);
      }`,
  });
}

function triangleCount(geometry: THREE.BufferGeometry): number {
  const position = geometry.getAttribute("position");
  if (!position) return 0;
  return Math.floor((geometry.index ? geometry.index.count : position.count) / 3);
}

/** Flips each triangle's winding, for a node transform that mirrors. */
function flipWinding(geometry: THREE.BufferGeometry): void {
  if (!geometry.index) {
    const count = geometry.getAttribute("position").count;
    const index = new Uint32Array(count);
    for (let i = 0; i < count; i++) index[i] = i;
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
  }
  const index = geometry.index as THREE.BufferAttribute;
  for (let i = 0; i + 2 < index.count; i += 3) {
    const b = index.getX(i + 1);
    index.setX(i + 1, index.getX(i + 2));
    index.setX(i + 2, b);
  }
}

function loader(): GLTFLoader {
  const gltf = new GLTFLoader();
  const draco = new DRACOLoader();
  draco.setDecoderPath("/three/examples/jsm/libs/draco/gltf/");
  gltf.setDRACOLoader(draco);
  gltf.setMeshoptDecoder(MeshoptDecoder);
  return gltf;
}

async function load(spec: Spec): Promise<Loaded> {
  const gltf = await loader().loadAsync(spec.url);
  const root = gltf.scene;
  root.updateMatrixWorld(true);

  // Merge every mesh with its node transforms applied, then turn forward onto +X.
  const turn = new THREE.Matrix4().makeRotationY(turnAngle(spec.forward));
  const sources: { geometry: THREE.BufferGeometry; material: THREE.Material | THREE.Material[] }[] = [];
  let triangles = 0;
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry.getAttribute("position")) return;
    triangles += triangleCount(mesh.geometry);
    const geometry = mesh.geometry.clone();
    if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
    const world = new THREE.Matrix4().multiplyMatrices(turn, mesh.matrixWorld);
    geometry.applyMatrix4(world);
    if (world.determinant() < 0) flipWinding(geometry);
    sources.push({ geometry, material: mesh.material });
  });

  const low = new THREE.Vector3(Infinity, Infinity, Infinity);
  const high = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (const { geometry } of sources) {
    const position = geometry.getAttribute("position");
    const v = new THREE.Vector3();
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i);
      low.min(v);
      high.max(v);
    }
  }
  if (sources.length === 0) throw new Error("the model has no triangle meshes");
  const rawExtent = high.clone().sub(low);
  let factor = 1;
  if (spec.factor !== null) {
    factor = spec.factor;
  } else if (spec.measure !== null && spec.measureValue !== null) {
    const measured = spec.measure === "length" ? rawExtent.x : spec.measure === "height" ? rawExtent.y : 1;
    if (measured <= 1e-9) throw new Error("the model has no extent along the manifest's measure");
    factor = spec.measureValue / measured;
  }

  // The anchor is the centre of x and z at the lowest y, so the model stands on the ground.
  const anchor = new THREE.Vector3((low.x + high.x) * 0.5, low.y, (low.z + high.z) * 0.5);
  const place = new THREE.Matrix4()
    .makeScale(factor, factor, factor)
    .multiply(new THREE.Matrix4().makeTranslation(-anchor.x, -anchor.y, -anchor.z));

  const group = new THREE.Group();
  const meshes: THREE.Mesh[] = [];
  const entries: Entry[] = [];
  const bySource = new Map<THREE.Material | undefined, Entry>();
  const uniforms = colourUniforms(spec);
  const entryFor = (source: THREE.Material | undefined): Entry => {
    let entry = bySource.get(source);
    if (!entry) {
      const src = source as THREE.MeshStandardMaterial | undefined;
      const index = entries.length + 1;
      entry = {
        lit: litMaterial(source, spec, uniforms),
        id: idMaterial(index),
        colour: colourMaterial(source),
        index,
        base: src?.color ? src.color.clone() : new THREE.Color(1, 1, 1),
        textured: !!src?.map,
        uniforms,
      };
      bySource.set(source, entry);
      entries.push(entry);
    }
    return entry;
  };
  const box = new THREE.Box3();
  for (const { geometry, material } of sources) {
    geometry.applyMatrix4(place);
    geometry.computeBoundingBox();
    box.union(geometry.boundingBox as THREE.Box3);
    const list = Array.isArray(material) ? material : [material];
    const used = list.map((m) => entryFor(m));
    const mesh = new THREE.Mesh(geometry, Array.isArray(material) ? used.map((e) => e.lit) : used[0].lit);
    mesh.userData.entries = used;
    mesh.userData.array = Array.isArray(material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    meshes.push(mesh);
  }
  const dispose = () => {
    for (const m of meshes) m.geometry.dispose();
    for (const e of entries) {
      e.lit.dispose();
      e.id.dispose();
      e.colour.dispose();
    }
    root.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        for (const value of Object.values(m)) {
          if (value && (value as THREE.Texture).isTexture) (value as THREE.Texture).dispose();
        }
        m.dispose();
      }
    });
  };
  return { group, meshes, entries, triangles, rawExtent, factor, box, dispose };
}

type Pass = "lit" | "id" | "colour";

function setPass(loaded: Loaded, pass: Pass): void {
  for (const mesh of loaded.meshes) {
    const used = mesh.userData.entries as Entry[];
    const pick = used.map((e) => e[pass]);
    mesh.material = mesh.userData.array ? pick : pick[0];
  }
}

function setTint(loaded: Loaded, mode: number, colour?: THREE.Color): void {
  const u = loaded.entries[0]?.uniforms;
  if (!u) return;
  u.uTintMode.value = mode;
  if (colour) (u.uTintColour.value as THREE.Vector3).set(colour.r, colour.g, colour.b);
}

function viewCamera(centre: THREE.Vector3, radius: number, yawDeg: number, elevationDeg: number) {
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 1000);
  const half = THREE.MathUtils.degToRad(FOV * 0.5);
  const distance = (radius / Math.sin(half)) * MARGIN;
  const el = THREE.MathUtils.degToRad(elevationDeg);
  const offset = new THREE.Vector3(0, Math.sin(el), Math.cos(el)).multiplyScalar(distance);
  offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(yawDeg));
  camera.position.copy(centre).add(offset);
  camera.up.set(0, elevationDeg >= 89.9 ? 0 : 1, elevationDeg >= 89.9 ? -1 : 0);
  camera.near = distance * 0.02;
  camera.far = distance * 12;
  camera.lookAt(centre);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return { camera, distance };
}

function groundMaterial(): THREE.MeshStandardMaterial {
  const material = new THREE.MeshStandardMaterial({ color: new THREE.Color(0.9, 0.89, 0.86), roughness: 1, metalness: 0 });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vGroundPosition;")
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\nvGroundPosition = (modelMatrix * vec4(transformed, 1.0)).xyz;",
      );
    // The staging sheet tool's grid: a line every metre, a darker one every five, faded where a
    // pixel covers more than the lines are wide. Its colours are literals in linear light.
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vGroundPosition;")
      .replace(
        "#include <map_fragment>",
        `#include <map_fragment>
        {
          vec2 w = vGroundPosition.xz;
          vec2 f1 = max(fwidth(w), vec2(1e-5));
          vec2 g = abs(fract(w + 0.5) - 0.5) / f1;
          float line = (1.0 - clamp(min(g.x, g.y) - 0.3, 0.0, 1.0)) * (1.0 - smoothstep(0.3, 0.8, max(f1.x, f1.y)));
          vec2 w5 = w / 5.0;
          vec2 f5 = max(fwidth(w5), vec2(1e-5));
          vec2 g5 = abs(fract(w5 + 0.5) - 0.5) / f5;
          float line5 = (1.0 - clamp(min(g5.x, g5.y) - 0.6, 0.0, 1.0)) * (1.0 - smoothstep(0.06, 0.16, max(f5.x, f5.y)));
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.45, 0.47, 0.5), clamp(line * 0.35 + line5 * 0.5, 0.0, 1.0));
        }`,
      );
  };
  material.customProgramCacheKey = () => "simhood-ground";
  return material;
}

export function init() {
  const canvas = document.createElement("canvas");
  canvas.width = SIZE;
  canvas.height = SIZE;
  document.body.appendChild(canvas);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, alpha: false });
  renderer.setPixelRatio(1);
  renderer.setSize(SIZE, SIZE, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;

  const background = new THREE.Color().setRGB(0.8 * 0.85, 0.83 * 0.85, 0.86 * 0.85, THREE.SRGBColorSpace);
  const scene = new THREE.Scene();
  // Godot's lights are not in physical units: its energy 1 is the albedo itself.
  const ambient = new THREE.AmbientLight(new THREE.Color().setRGB(0.78, 0.82, 0.9, THREE.SRGBColorSpace), 0.65 * Math.PI);
  const sun = new THREE.DirectionalLight(new THREE.Color().setRGB(1.0, 0.92, 0.8, THREE.SRGBColorSpace), 1.35 * Math.PI);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.radius = 4;
  sun.shadow.blurSamples = 16;
  const ground = new THREE.Mesh(new THREE.CircleGeometry(1, 64), groundMaterial());
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ambient, sun, sun.target, ground);

  // The sun as the staging sheet tool sets it: rotation (-50, -40, 0) inside the view's pivot,
  // which is turned by the review yaw.
  const sunDirection = new THREE.Vector3(0, 0, -1)
    .applyAxisAngle(new THREE.Vector3(1, 0, 0), THREE.MathUtils.degToRad(-50))
    .applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(-40))
    .applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(REVIEW_YAW));

  const colourTarget = new THREE.WebGLRenderTarget(SIZE, SIZE, { samples: 0 });
  const pixels = new Uint8Array(SIZE * SIZE * 4);

  function drawPicture(loaded: Loaded, centre: THREE.Vector3, radius: number): string {
    const { camera, distance } = viewCamera(centre, radius, REVIEW_YAW, REVIEW_ELEVATION);
    ground.visible = true;
    ground.scale.setScalar(distance * 3);
    ground.position.set(centre.x, -0.002, centre.z);
    scene.background = background;
    sun.position.copy(centre).addScaledVector(sunDirection, -radius * 4);
    sun.target.position.copy(centre);
    sun.target.updateMatrixWorld(true);
    const s = radius * 1.7;
    Object.assign(sun.shadow.camera, { left: -s, right: s, top: s, bottom: -s, near: radius * 0.5, far: radius * 8 });
    sun.shadow.camera.updateProjectionMatrix();
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = radius * 0.01;
    setPass(loaded, "lit");
    renderer.setRenderTarget(null);
    renderer.setClearColor(background, 1);
    renderer.render(scene, camera);
    return canvas.toDataURL("image/png").split(",")[1];
  }

  function drawTarget(loaded: Loaded, pass: Pass, camera: THREE.Camera): Uint8Array {
    ground.visible = false;
    scene.background = null;
    setPass(loaded, pass);
    renderer.setRenderTarget(colourTarget);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    renderer.readRenderTargetPixels(colourTarget, 0, 0, SIZE, SIZE, pixels);
    return pixels;
  }

  function maskCounts(data: Uint8Array): { mask: number; model: number } {
    let mask = 0;
    let model = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 128) continue;
      model++;
      if (data[i] >= 128) mask++;
    }
    return { mask, model };
  }

  function colours(loaded: Loaded, centre: THREE.Vector3, radius: number): Colour[] {
    const { camera } = viewCamera(centre, radius, REVIEW_YAW, REVIEW_ELEVATION);
    const ids = new Uint8Array(drawTarget(loaded, "id", camera));
    const colour = drawTarget(loaded, "colour", camera);
    const plain = new Map<number, number>();
    const bins = new Map<number, { entry: number; count: number; r: number; g: number; b: number }>();
    let total = 0;
    for (let i = 0; i < colour.length; i += 4) {
      if (ids[i + 3] < 128) continue;
      total++;
      const id = ids[i];
      const entry = loaded.entries[id - 1];
      if (!entry) continue;
      if (!entry.textured) {
        plain.set(id, (plain.get(id) ?? 0) + 1);
        continue;
      }
      const r = colour[i];
      const g = colour[i + 1];
      const b = colour[i + 2];
      const key = (id << 15) | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
      const bin = bins.get(key) ?? { entry: id, count: 0, r: 0, g: 0, b: 0 };
      bin.count++;
      bin.r += r;
      bin.g += g;
      bin.b += b;
      bins.set(key, bin);
    }
    const out = new Map<string, Colour>();
    const add = (hex: string, count: number, source: Colour["source"]) => {
      const key = `${source}:${hex}`;
      const found = out.get(key);
      if (found) found.share += count / total;
      else out.set(key, { hex, share: count / total, source });
    };
    for (const [id, count] of plain) add(`#${loaded.entries[id - 1].base.getHexString(THREE.SRGBColorSpace)}`, count, "material");
    for (const bin of bins.values()) {
      const hex = [bin.r, bin.g, bin.b].map((v) => Math.round(v / bin.count).toString(16).padStart(2, "0")).join("");
      add(`#${hex}`, bin.count, "texture");
    }
    return [...out.values()]
      .sort((a, b) => b.share - a.share || (a.hex < b.hex ? -1 : 1))
      .slice(0, 8)
      .map((c) => ({ ...c, share: Math.round(c.share * 10000) / 10000 }));
  }

  function coverage(loaded: Loaded, centre: THREE.Vector3, radius: number): { all: number; review: number } {
    let mask = 0;
    let model = 0;
    setTint(loaded, 2);
    for (let k = 0; k < 7; k++) {
      const { camera } = k < 6 ? viewCamera(centre, radius, k * 60, 25) : viewCamera(centre, radius, 0, 90);
      const counts = maskCounts(drawTarget(loaded, "lit", camera));
      mask += counts.mask;
      model += counts.model;
    }
    const { camera } = viewCamera(centre, radius, REVIEW_YAW, REVIEW_ELEVATION);
    const review = maskCounts(drawTarget(loaded, "lit", camera));
    setTint(loaded, 0);
    const ratio = (m: number, n: number) => (n === 0 ? 0 : Math.round((m / n) * 10000) / 10000);
    return { all: ratio(mask, model), review: ratio(review.mask, review.model) };
  }

  async function run(spec: Spec, options: Options): Promise<Result> {
    const loaded = await load(spec);
    try {
      scene.add(loaded.group);
      const centre = loaded.box.getCenter(new THREE.Vector3());
      const radius = loaded.box.getSize(new THREE.Vector3()).length() * 0.5;
      const result: Result = {
        triangles: loaded.triangles,
        rawExtent: [loaded.rawExtent.x, loaded.rawExtent.y, loaded.rawExtent.z],
        factor: loaded.factor,
        min: [loaded.box.min.x, loaded.box.min.y, loaded.box.min.z],
        max: [loaded.box.max.x, loaded.box.max.y, loaded.box.max.z],
        image: null,
        tinted: null,
        tintCoverage: null,
        tintCoverageReview: null,
        colours: null,
      };
      const hasKey = spec.tintKey !== null;
      if (options.image) result.image = drawPicture(loaded, centre, radius);
      if (options.tinted && hasKey) {
        setTint(loaded, 1, srgb(TEST_TINT));
        result.tinted = drawPicture(loaded, centre, radius);
        setTint(loaded, 0);
      }
      if (options.analyse) {
        if (hasKey) {
          const c = coverage(loaded, centre, radius);
          result.tintCoverage = c.all;
          result.tintCoverageReview = c.review;
        }
        result.colours = colours(loaded, centre, radius);
      }
      return result;
    } finally {
      scene.remove(loaded.group);
      loaded.dispose();
    }
  }

  const gl = renderer.getContext();
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  return {
    run,
    gpu: debug ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)) : "unknown",
  };
}
