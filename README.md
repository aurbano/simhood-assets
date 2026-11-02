# simhood-assets

This is the home of every 3D model used by **simhood**, a city simulation game, and of the list of models the game still wants.

The game itself is a deterministic city simulation and lives in a private repository. This repository holds only the models, the wanted list, the checks and the guides.

## See what is wanted

The board at <https://simhood.aurbano.eu/assets/> lists every slot (a place in the game that needs a model): what it should be, how big, how many variants, what we already have, and what is still missing. Each slot links to the rules for making one.

To help, read [CONTRIBUTING.md](CONTRIBUTING.md) and [STYLE.md](STYLE.md), claim a slot with the issue form, and open a pull request.

## What is in here

|Folder|What it holds|
|---|---|
|`packs/`|Every model the game uses, one folder per pack, each model with a small `.toml` manifest saying what it is, who made it and under what licence|
|`catalogue/`|The wanted list: kinds, size and triangle budgets, and one entry per slot|
|`examples/`|Pictures and files used by the style guide, including models we turned down and why|
|`tools/`|The checks (validator), the picture renderer and the credits and board-data scripts|

## How a model gets into the game

1. You open a pull request. Automatic checks tell you at once whether the files are acceptable and post pictures of your model.
2. The owner reviews it, and may edit, simplify or decline it.
3. The owner merges it, then syncs this repository into the game. The game's credits are generated from the manifests.
4. The model appears on the board after the next website deploy.

## Licences

- Tooling (everything in `tools/`): MIT OR Apache-2.0 (`LICENSE-MIT`, `LICENSE-APACHE`).
- The catalogue, documents and style guide: CC0-1.0 (`LICENSE-CC0`).
- Every model keeps its own licence, stated in its manifest: CC0-1.0, CC-BY-3.0 or CC-BY-4.0.

Attribution is in each model's manifest and on the board's credits section.
