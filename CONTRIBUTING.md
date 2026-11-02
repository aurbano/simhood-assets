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
|`forward`|Which model axis points the way the thing faces: `+x` (default), `-x`, `+z` or `-z`. The game turns the model so this points along +x. Needed for vehicles and anything with a front.|
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

## Running the checks yourself

Validator (needs [uv](https://docs.astral.sh/uv/); no other install):

```
uv run tools/validate.py
```

Pictures (the same renderer the pull request uses):

```
pnpm --dir tools install
pnpm --dir tools render -- --repo . --report <report.json> --out out --only my-pack:panel-van
```

Write the report first with `uv run tools/validate.py --report <report.json>`, then pass the same path to the renderer. The pictures land in `out/`.

## Signing off your commits

Every commit in your pull request must carry a `Signed-off-by: Your Name <you@example.com>` line, with the same email as the commit's author. Add it with `git commit -s`; fix commits you have already made with `git commit --amend -s` (the last one) or `git rebase --signoff` (several).

The sign-off is the [Developer Certificate of Origin](https://developercertificate.org/): you certify that you made the work or have the right to submit it under the stated licence. The check fails and names any commit that lacks it.

## What a pull request may change

Only files inside your own `packs/<pack>/` folder, plus the **status line** of a slot that one of your models fills: `missing` to `stand-in` or `have`, or `stand-in` to `have`. Changes to sizes, the rest of the catalogue, `tools/`, `examples/`, `.github/` and root files are the owner's alone and fail the checks.

### Replacing a stand-in

Add your model, delete the stand-in in the same pull request, and set the slot to `have`. Or keep the stand-in as another variant when the slot wants more than one.

## Opening the pull request

Fill in the template: the slot, original or third-party, licence and the AI statement. Keep **Allow edits by maintainers** switched on, so the owner can fix small things instead of sending you back.

The checks post commit statuses `assets/validate` and `assets/dco`, and keep one comment up to date: a verdict (each model's slot, triangles against budget, size against the slot, licence, AI status, warnings and failures) and pictures. For a pull request from a new contributor, the owner approves the picture run first.

## Review

The owner reads the verdict and looks at the pictures. They may edit your model or manifest, simplify it, ask for changes or decline it. Declining is about fit with the game's style and needs, not about you.

## After the merge

The owner syncs this repository into the game. The model then shows on the board after the next website deploy, and you are credited from your manifest.
