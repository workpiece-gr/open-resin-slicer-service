"""Which UVtools findings are real print risks and which are the support structure itself.

UVtools' island detector looks at every layer for cured area that has no cured area directly below it. A part with a
flat underside rests on the support tips: the layer just above the tips is a large area whose only contact with the
layer below is a handful of tiny tip pixels, so UVtools reports one Island (and an Overhang) there, on EVERY orientation
of such a part, even though the supports are doing exactly what they are for. Measured on a real 40 x 30 x 20 mm box
(issue #15): supported print 35.15 mm for a 30 mm model, lift 5.15 mm = 103 layers at 0.05 mm, Island at layer 103 on
all five finalists.

``triage_issue_summary`` keeps such an island in the report as information (``support_tip_islands``) and removes it from
``islands``, the count that blocks an orientation and raises the "critical issues" warnings. An island on any other layer
(a real mid-body island, a detached floating feature) stays critical.
"""
from __future__ import annotations

import re

SUPPORT_TIP_TOLERANCE_LAYERS = 1
_ISSUE_ROW = re.compile(r"^\s*(?P<kind>[A-Za-z]+),\s*(?P<layer>\d+),")


def support_lift_layers(print_height_mm: float, model_height_mm: float, layer_height_mm: float) -> int | None:
    """Layers between the build plate and the first layer of the model body: (printed height - model height) / layer height."""
    try:
        lift = float(print_height_mm) - float(model_height_mm)
        layers = round(lift / float(layer_height_mm))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return layers if layers >= 1 else None   # no supports under the part: nothing to excuse


def island_layers(issue_text: str) -> list[int]:
    """The layer index of every ``Island`` row of UVtools' ``print-issues`` text."""
    layers = []
    for line in str(issue_text or "").splitlines():
        match = _ISSUE_ROW.match(line)
        if match and match.group("kind") == "Island":
            layers.append(int(match.group("layer")))
    return layers


def is_first_model_layer_island(layer: int, lift_layers: int | None) -> bool:
    """True for an island on the first layer of the model body above the support tips (lift layers, +/- one layer)."""
    return lift_layers is not None and abs(int(layer) - int(lift_layers)) <= SUPPORT_TIP_TOLERANCE_LAYERS


def triage_issue_summary(issue_summary: dict[str, int], issue_text: str, *, lift_layers: int | None) -> dict[str, int]:
    """A copy of the issue counts where first-model-layer islands move from ``islands`` to ``support_tip_islands``.

    The row count and the summary must agree (the parser guarantees it); if they do not, the summary is returned
    unchanged rather than guessing.
    """
    result = dict(issue_summary)
    layers = island_layers(issue_text)
    if len(layers) != int(result.get("islands", 0)):
        result["support_tip_islands"] = 0
        return result
    excused = sum(1 for layer in layers if is_first_model_layer_island(layer, lift_layers))
    result["islands"] = len(layers) - excused
    result["support_tip_islands"] = excused
    return result
