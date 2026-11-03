# Test data

|File|What it is|
|---|---|
|`hay_bale.blend`|"Hay Bale" by Mish7913, <https://opengameart.org/content/hay-bale-0> (published 2024-05-16), licence **CC0-1.0** as stated on that page. One material, `hay_material`, linked to the image `//textures/hay.jpg`. `test_blend_colours.py` reads it to check the `.blend` reader against a real file.|

`hay_bale.blend` is the file from that page (saved by Blender 3.0 or later) with one change: the page's copy is zstd-compressed, so it was decompressed and compressed again with gzip (`gzip -9`, no timestamp), which every Python the tools support can read. The Blender data inside is untouched. SHA-256 of the file as published: `1a9c6dfebbc1b42423aff44292a12f5472a1e94860be9c200198cb64a08d296c`; of its decompressed contents: `8365440653ec3c99c82a226d24fea3b0268546a0fbe17b9182ad5a0a179a9ad4`.
