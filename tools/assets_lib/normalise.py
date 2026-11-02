"""How the client normalises a model, and the size comparison with a slot (contract, D6).

The client (`godot/client/render/asset_packs.gd`) merges the meshes, turns the model about Y so
`forward` points along +X, measures the box, scales by `length` (extent x), `height` (extent y) or
`scale` (1.0), and stands the model on y = 0 centred. Only extents matter for sizes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_TOLERANCE = 0.5
WARN_SHARE = 0.4
# An axis whose scaled size is below this many metres is not compared with the slot.
FLAT_AXIS_M = 0.02
LONG_SIDE = "footprint long side"
SHORT_SIDE = "footprint short side"


@dataclass
class Scaled:
    factor: float
    length: float  # extent along +X after the turn
    width: float  # extent along Z after the turn
    height: float


def normalise(
    bounds_min: tuple[float, float, float],
    bounds_max: tuple[float, float, float],
    forward: str,
    measure: str,
    value: float,
) -> Scaled | str:
    """The scaled size of a model, or a sentence saying why it cannot be."""
    ex = bounds_max[0] - bounds_min[0]
    ey = bounds_max[1] - bounds_min[1]
    ez = bounds_max[2] - bounds_min[2]
    # A turn by +-PI/2 swaps the x and z extents; PI and the identity keep them.
    turned_x, turned_z = (ez, ex) if forward in ("+z", "-z") else (ex, ez)
    if measure == "length":
        measured = turned_x
    elif measure == "height":
        measured = ey
    else:
        measured = 1.0
    if not measured > 1e-9:
        return f"has no extent along its {measure} axis, so it cannot be scaled"
    factor = value / measured
    scaled = Scaled(factor, turned_x * factor, turned_z * factor, ey * factor)
    if not all(math.isfinite(v) for v in (scaled.factor, scaled.length, scaled.width, scaled.height)):
        return "has a non-finite scaled size"
    return scaled


def deviation(measured: float, wanted: float) -> float:
    """d = max(r, 1/r) - 1 for r = measured / wanted; infinite when either is not positive."""
    if measured <= 0 or wanted <= 0:
        return math.inf
    r = measured / wanted
    return max(r, 1 / r) - 1


def compare(scaled: Scaled, wanted: dict, tolerance: float | None) -> list[tuple[str, str, float, float]]:
    """Axis comparisons against a slot's size: (axis, level, deviation, tolerance) for each axis
    that warns or fails. The footprint's long and short sides are compared with the slot's, so a
    prop's orientation does not matter."""
    tol = DEFAULT_TOLERANCE if tolerance is None else tolerance
    found = []
    long_m, short_m = max(scaled.length, scaled.width), min(scaled.length, scaled.width)
    long_w, short_w = max(wanted["length"], wanted["width"]), min(wanted["length"], wanted["width"])
    for axis, got, want in ((LONG_SIDE, long_m, long_w), (SHORT_SIDE, short_m, short_w), ("height", scaled.height, wanted["height"])):
        if got < FLAT_AXIS_M:
            continue  # a flat piece (a door panel) has no depth to compare
        d = deviation(got, want)
        if d > tol:
            found.append((axis, "fail", d, tol))
        elif d > WARN_SHARE * tol:
            found.append((axis, "warn", d, tol))
    return found
