# Style guide

The game draws a small town seen from above, like a miniature world. Every model has to look like it belongs to that world. This guide says what that means in practice.

## One family

- Chunky, clean, low-poly and flat-shaded, with soft colours.
- Our reference is the KayKit packs already in `packs/kaykit-city` and `packs/kaykit-forest`. A new model should look natural standing next to them.
- No noisy or photographic textures, no outlines, no pixel-art textures, no PBR detail, no lighting baked into the colours.

## Real sizes

The game draws everything at real size, so model proportions must survive it:

- a car is about 4.3 m long
- a storey is about 3 m
- a door is about 2.1 m
- an adult is about 1.75 m

Toy or caricature proportions (huge wheels, giant heads) are fine only when the whole family shares them. A realistic lorry next to a toy car looks wrong. Each slot on the board gives the size it wants.

## Readable from far away

The camera sits 30 to 300 m from the town. Silhouette matters more than detail: if the shape is clear from far away, small details are wasted triangles.

Triangle budgets (a slot may state its own):

|Class|Budget|
|---|---|
|Prop|1,500 triangles|
|Vehicle|3,000 triangles|
|Large piece|6,000 triangles|

## Easy to recolour

The game recolours models per instance from a palette. Use flat colours, or one small palette or gradient atlas. The part that should change colour (the body paint of a van, say) needs one colour no other part uses; see `tint_key` in [CONTRIBUTING.md](CONTRIBUTING.md).

## Local flavour

Where there is a choice, make it British: a red post box, a red phone box, a Belisha beacon, a UK bus shelter. Otherwise keep it generic.

## What we turned down, and why

The `examples/` folder shows models we rejected. In general terms they were turned down for:

- **Wrong visual language**: realistic PBR surfaces, weathered or painted textures, patterned bark or fragmented tree crowns beside KayKit's solid ones.
- **Wrong proportions**: toy-like or blocky characters, buses that are too short and broad.
- **Wrong pose or format**: characters locked in a T-pose, models whose animation was lost.
- **Hidden problems**: a plain white export that hides its intended colours. We want to see the real colours.
- **Licence**: share-alike, non-commercial, no-derivatives and unclear licences cannot be used at all.

Rejecting a model says nothing about its quality. It means it does not fit this particular family.
