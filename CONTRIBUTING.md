# Contributing

Thank you for helping. This page covers how to pick a model to make, how to package it, what the automatic checks refuse, and what happens after you open a pull request. Read [STYLE.md](STYLE.md) first so your model fits the game's look.

## Picking a slot

A slot is one place in the game that needs a model, such as `vehicles.panel-van`. Every slot has a kind, a real-world size, a style note, a priority and a status:

- `missing`: nothing yet. Best place to start.
- `stand-in`: something stands in, but a better one is wanted.
- `have`: done (more variants may still be wanted).
- `procedural`: the game draws it in code; no model needed.

Find slots on the board at <https://simhood.aurbano.eu/assets/> or in the `catalogue/` folder (one `.toml` file per area; `catalogue/kinds.toml` lists the kinds and triangle budgets).

Then **claim** the slot with the issue form (Issues, "Claim a slot"; the board's claim button opens it with the slot filled in). It tells others you are on it. A claim is a courtesy, not a lock.

## Original work or third-party work

**Original work** is a model you made. Give it `CC0-1.0` (preferred) or `CC-BY-4.0`. It must state whether generative AI was used (see below), yes or no.

**Third-party work** is someone else's model that you adapt or bring in. It is accepted only if its licence is `CC0-1.0`, `CC-BY-3.0` or `CC-BY-4.0`. Save the source's licence page under your pack's `licences/` folder (`.txt`, `.html`, `.md`, `.pdf` or `.png`) and point `licence_evidence` at it. Share-alike, non-commercial, no-derivatives, OGA-BY and unclear licences are refused. If the source does not say whether AI made the model, say `ai_generated = "unknown"`.

Say honestly which it is. A model that is not wholly your own work must have `licence_evidence`. The validator cannot tell the difference; the review comment shows each model as "third-party (licence evidence: ...)" or "original (no licence_evidence)", and the owner reads it. A third-party model passed off as original is removed.

### Saving the licence page

Save the source's licence page with your browser (File > Save Page As, HTML) or with `curl -L <url> -o packs/<pack>/licences/<name>.html`. The saved page must show the licence and identify the model or its pack.

Anything under CC-BY needs a `title`, and the credits are generated from your manifest, so fill it in properly.

## The pack format

A pack is a folder under `packs/` holding a `pack.toml`, your models and their manifests, and any licence evidence:

```
packs/my-pack/
  pack.toml
  vehicles/
    panel-van.glb
    panel-van.toml
  licences/            (third-party work only)
    source-licence.html
```

Names use only letters, digits, `.`, `_` and `-`, and sit at most three folders below the pack. No symlinks.

### `pack.toml`

Values here apply to every model in the pack unless a model's manifest overrides them.

```toml
id = "my-pack:base"          # namespace:name
version = "1.0.0"
licence = "CC0-1.0"          # CC0-1.0, CC-BY-3.0 or CC-BY-4.0
ai_generated = "no"          # yes, no, or unknown (third-party only)
author = "Your Name"
source = "https://github.com/yourname"
```

### A model's manifest: `panel-van.toml`

```toml
id = "my-pack:panel-van"
slot = "vehicles.panel-van"
kind = "van"
title = "Panel van"
length = 5.4
forward = "+z"
weight = 2
tint_key = "#d9d9d9"
tint_tolerance = 0.25
licence = "CC0-1.0"
ai_generated = "no"
author = "Your Name"
source = "https://github.com/yourname/panel-van"
changes = []
contributors = ["yourgithubname"]
```

Every key:

|Key|Meaning|
|---|---|
|`id`|`pack-name:model-name`, unique.|
|`slot`|The catalogue slot this model fills. It must exist and have the same `kind`.|
|`kind`|What the thing is (`van`, `street.bench`, `tree.broadleaf` and so on); see `catalogue/kinds.toml`. It decides the triangle budget.|
|`title`|The work's name as its source gives it. Required for any CC-BY model.|
|Size key|Exactly one of `length` (metres along the forward axis, for vehicles and long things), `height` (metres tall) or `scale` (multiplies the model's own units, taken as metres). The game scales your model so it ends up this size.|
|`forward`|Which model axis points the way the thing faces: `+x` (default), `-x`, `+z` or `-z`. The game turns the model so this points along +x. Needed for vehicles and anything with a front. If the size check shows length and width swapped or wildly off, this is the usual cause: try each of `+x`, `-x`, `+z`, `-z` (see "If a check fails").|
|`weight`|How often the game picks this variant compared with others of the same kind. Optional.|
|`tint_key`|A `#rrggbb` colour. Parts of the model near this colour take the game's palette colour for each instance (the body paint of a van, say). Leave it out if nothing should change colour.|
|`tint_tolerance`|How near a colour must be to the key, 0 to 1. Default 0.25.|
|`flat_shading`|Set `true` to draw the model with flat facets. Optional.|
|`licence`|`CC0-1.0`, `CC-BY-3.0` or `CC-BY-4.0`. Inherited from `pack.toml` when absent.|
|`licence_evidence`|Third-party work only: path inside the pack to the saved licence page, under `licences/`. Its presence is what marks the work as third-party.|
|`ai_generated`|`yes`, `no` or `unknown`. Original work must say `yes` or `no`.|
|`ai_tool`, `ai_model`, `ai_plan`|Required when `ai_generated = "yes"`: the tool, the model, and the plan (`paid`, `free` or `self-hosted`).|
|`author`|Who made the original. Never replaced by an editor.|
|`attribution`|A credit line the licensor asked for, copied exactly. Only if they asked.|
|`source`|Where the model comes from, as an `https` link for third-party work.|
|`changes`|What was done to the original, oldest first (`["scaled to 5.4 m long", "recoloured body"]`). CC-BY asks that changes are indicated.|
|`contributors`|GitHub handles of people who submitted or edited the model, oldest first.|

When you edit someone else's model, only add to `changes` and `contributors`; the checks refuse removing earlier entries, and `author` must not change.

### Choosing a `tint_key`

Pick one flat colour, or one cell of your colour atlas, that **no other part of the model uses**. Everything within `tint_tolerance` of it takes the palette colour, so if a window or tyre shares the colour it will be recoloured too. The pull request report lists the model's colours and how much of the model the key matches, so you can check. A key that matches nothing fails the checks; one that only matches a surface hidden from the review view passes with a warning.

## Budgets and size

- Triangles: prop 1,500, vehicle 3,000, large piece 6,000, counted over every part of the model including repeated ones. A slot may state its own limit.
- Size: after the game scales your model, its height and its footprint (the longer side against the slot's length, the shorter against its width) are compared with the slot's size. Within 20% is fine; beyond that you get a warning; beyond 50% it is refused.

## What the checks refuse outright

- File types other than `pack.toml`, `.toml` manifests, `.glb` models and licence evidence (`.txt`, `.html`, `.md`, `.pdf`, `.png`) inside a pack. Names with other characters, folders deeper than three levels and symlinks.
- A `.glb` over 4 MiB, or a pull request adding more than 20 MiB.
- Anything that is not glTF 2.0 that the Khronos glTF Validator accepts, or that points at an external file.
- Textures that are not PNG, are over 1024 px a side, or number more than two.
- Any glTF extension other than `KHR_materials_unlit`, `KHR_texture_transform` and `KHR_materials_emissive_strength`.
- Animations or skins, unless the kind is `person.*` or `animal.*`.
- Things the checks cannot measure reliably: sparse accessors, morph targets, quantised positions, more than one scene, more than 256 nodes, 32 levels deep, 64 meshes or 200,000 vertices. Such a model gets no triangle count or size and fails.
- Pull requests that change anything outside your pack other than the status line of the slot you fill.
- A commit without a sign-off (see below).

## AI-generated models

Models made with generative AI are accepted **with disclosure**.

- Set `ai_generated = "yes"` and name `ai_tool`, `ai_model` and `ai_plan`.
- The board labels these models "AI-generated with <tool> (<model>)", the same appears in the pull request report and the credits, and they are kept out of the game's marketing shots.
- A model later found to be AI-generated without being disclosed is removed.
- Ordinary tools such as decimation, UV unwrapping and baking are not generative AI.

Two things are refused:

- **Meshy on its free plan**: its free output is licensed CC BY 4.0 to Meshy and needs a credit to Meshy, which does not suit us.
- **Hunyuan3D**: its licence forbids using or showing its outputs in the EU, the UK and South Korea.

## Getting a model into shape

These steps take you from a Blender file or a download to a `.glb` and a manifest that the checks accept. The tools sit in `tools/`; run everything from the repository root.

### 1. Exporting from Blender

File > Export > glTF 2.0, then:

- Format: **glTF Binary (.glb)**.
- Include: only the model (tick "Selected Objects" and select just the model, not lights, cameras or helpers).
- Transform: **+Y Up**.
- Mesh: **Apply Modifiers**. Apply object transforms (Object > Apply > All Transforms) before exporting, so the model is not left with a scale or rotation on the object.
- Textures: PNG, at most 1024 px a side and at most two of them. Leave animations, skinning, cameras and lights out unless the kind is `person.*` or `animal.*`.
- Stand the model on the ground (lowest point at y = 0) with its middle at the origin. The game stands it on the ground itself, so this only matters to you when you measure it.

### 2. Converting a download (FBX, OBJ, glTF)

Downloads usually come as FBX, OBJ or `.gltf` with loose files. `tools/convert.sh` turns them into one `.glb` each, with every object's transform baked into the vertices:

```
tools/convert.sh path/to/Model.fbx path/to/more-models/ --out /tmp/converted
```

Give it files or folders (folders are scanned, and mirrored under `--out`). It needs [Godot 4.7](https://godotengine.org/) (`GODOT=<binary>` if it is not on your PATH as `godot`; on macOS it finds `/Applications/Godot.app`) and, for OBJ files only, Node with network access for the first run: OBJ goes through `npx obj2gltf@3.2.0`.

After each model it prints one line:

```
triangles=1530 size=4.086 x 1.676 x 1.745 (x, y, z, model units) origin_at_bottom_centre=no (...) textures=none
```

The triangles and size are what the validator will measure. Read the textures part: it warns when a texture is over 1024 px or there are more than two. A model that fails prints `FAIL <path>: <reason>`, the run carries on, and the exit status is non-zero. The origin does not have to be at the bottom centre; the game stands the model up itself.

### 3. Recovering colours from a `.blend`

FBX and OBJ exports often lose the colours of a Blender model, so it converts white. If you have the original `.blend`, put the colours back:

```
uv run tools/blend_colours.py --blend Model.blend --list
uv run tools/blend_colours.py --blend Model.blend --glb /tmp/converted/Model.glb --out /tmp/converted/Model.coloured.glb
```

`--list` prints the materials the `.blend` holds. The patch gives each material of the `.glb` the colour, or the PNG texture, of the `.blend` material with the same name (a trailing `.001` and `_` against `.` are ignored). It lists materials it could not match; pair those by hand with `--map <glb material>=<blend material>`. Textures are looked up by file name next to the `.blend` and in any `--textures <folder>`. Only PNG is embedded: a JPEG texture is refused, so convert it to PNG first. Without `--out` the `.glb` is patched in place; convert again to start over. Blender 3.0 and later save compressed `.blend` files, which need Python 3.14 or newer (`uv run --python 3.14 ...`); the tool says so if yours is one.

### 4. Starting the manifest

Put the `.glb` in your pack (`packs/<your-pack>/<folder>/`) and run:

```
uv run tools/new_model.py packs/my-pack/vehicles/panel-van.glb --slot vehicles.panel-van
```

It measures the model the way the validator does, looks up the slot and writes `panel-van.toml` beside it (it never overwrites one): `id`, `slot`, `kind`, the size key the slot's kind wants (`length` for vehicles, `height` for the rest, taken from the slot's size) and a suggested `forward`. Lines for what only you can say (`title`, `licence`, `author`, `source`, `licence_evidence`, `ai_generated`, `contributors`) are left commented: fill them in, or leave out those `pack.toml` already gives. It then prints the scaled size against the slot's and the triangles against the budget. If a size is far off, change the size key or `forward` before anything else. The `forward` sign is a guess: the review picture is a three-quarter view from the front right, so a vehicle's front should face you on the right; flip the sign if it does not.

If the pack has no `pack.toml`, the tool prints what to create. Last, set the status of the slot you fill in `catalogue/<area>.toml` (`missing` to `stand-in` or `have`), and run the checks below.

## Running the checks yourself

You need [uv](https://docs.astral.sh/uv/) and [pnpm](https://pnpm.io/). The validator runs the Khronos glTF Validator, which comes from pnpm, so install the tools' packages once before anything else (the pictures need them too):

```
pnpm --dir tools install
```

**Whole repository** (strict mode), and the same with a report file:

```
uv run tools/validate.py
uv run tools/validate.py --report report.json
```

**As the pull request check runs it** (compares your branch with `main`). Make a plain clone of `main` beside your checkout (a clone, not a git worktree), then:

```
git clone https://github.com/aurbano/simhood-assets ../assets-base
uv run tools/validate.py --pr --base ../assets-base --pr-author <your GitHub login> --report report.json
```

**Pictures** (the same renderer the pull request uses). Use absolute paths, because `pnpm --dir tools` runs inside `tools/`:

```
pnpm --dir tools install
pnpm --dir tools render -- --repo "$PWD" --report "$PWD/report.json" --out "$PWD/out" --only <your-pack:id>
```

The pictures land in `out/<pack>/`, which git ignores. pnpm's "Ignored build scripts" notice is harmless.

## If a check fails

- **Too many triangles, or a file too big:** reduce the model in your tool (for example Blender's Decimate modifier), resize textures to at most 1024 px and save them as PNG, then re-export as `.glb`.
- **A large size error on a model that looks plausible:** usually a wrong `forward`. `forward` is the model's own axis that points the way a vehicle drives (or a prop faces), in the glTF frame with Y up. The validator turns the model so that axis points along +X. If the scaled size shows length and width swapped or wildly off, try `+x`, `-x`, `+z` and `-z` in turn.
- **How size messages read:** they compare the footprint's long and short sides with the slot's length and width, whatever the model's orientation, and its height with the slot's height.

## Signing off your commits

Every commit in your pull request must carry a `Signed-off-by: Your Name <you@example.com>` line, with the same email as the commit's author. Add it with `git commit -s`; fix commits you have already made with `git commit --amend -s` (the last one) or `git rebase --signoff` (several).

The sign-off is the [Developer Certificate of Origin](https://developercertificate.org/): you certify that you made the work or have the right to submit it under the stated licence. The check fails and names any commit that lacks it.

## What a pull request may change

Only files inside your own `packs/<pack>/` folder, plus the **status line** of a slot that one of your models fills: `missing` to `stand-in` or `have`, or `stand-in` to `have`. These rules apply to everyone not listed in `CODEOWNERS`, and the pull request's author decides it. Changes to sizes, the rest of the catalogue, `tools/`, `examples/`, `.github/` and root files are the owner's alone and fail the checks.

### Replacing a stand-in

Add your model, delete the stand-in in the same pull request, and set the slot to `have`. Or keep the stand-in as another variant when the slot wants more than one.

## Opening the pull request

Fill in the template: the slot, original or third-party, licence and the AI statement. Keep **Allow edits by maintainers** switched on, so the owner can fix small things instead of sending you back.

The checks post commit statuses `assets/validate` and `assets/dco`, and keep one comment up to date: a verdict (each model's slot, triangles against budget, size against the slot, licence, AI status, warnings and failures) and pictures. For a pull request from a new contributor, the owner approves the picture run first.

## Review

The owner reads the verdict and looks at the pictures. They may edit your model or manifest, simplify it, ask for changes or decline it. Declining is about fit with the game's style and needs, not about you.

## After the merge

The owner syncs this repository into the game. The model then shows on the board after the next website deploy, and you are credited from your manifest.
