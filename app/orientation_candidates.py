from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .orientation import OrientationCandidate, OrientationMetrics, OrientationPlanError


DEFAULT_TILT_DEGREES = (15.0, 30.0, 45.0)
MAX_ORIENTATION_SPECS = 64


def _angle(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationPlanError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationPlanError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise OrientationPlanError(f"{name} must be a finite number.")
    return result


def _canonical(value: float) -> float:
    result = _angle("orientation angle", value) % 360.0
    if math.isclose(result, 0.0, abs_tol=1e-9) or math.isclose(result, 360.0, abs_tol=1e-9):
        return 0.0
    return round(result, 6)


@dataclass(frozen=True)
class OrientationSpec:
    x_deg: float
    y_deg: float
    z_deg: float = 0.0

    def validate(self) -> "OrientationSpec":
        for name, value in (("x_deg", self.x_deg), ("y_deg", self.y_deg), ("z_deg", self.z_deg)):
            numeric = _angle(name, value)
            if not (-360.0 <= numeric <= 360.0):
                raise OrientationPlanError(f"{name} must be between -360 and 360 degrees.")
        return self

    @property
    def canonical_key(self) -> tuple[float, float, float]:
        self.validate()
        return (_canonical(self.x_deg), _canonical(self.y_deg), _canonical(self.z_deg))

    def with_metrics(self, metrics: OrientationMetrics) -> OrientationCandidate:
        self.validate()
        metrics.validate()
        return OrientationCandidate(
            x_deg=self.x_deg,
            y_deg=self.y_deg,
            z_deg=self.z_deg,
            metrics=metrics,
        )


def _validated_tilts(values: Iterable[float]) -> tuple[float, ...]:
    normalized: list[float] = []
    for raw in values:
        value = _angle("tilt angle", raw)
        if not (0.0 < value < 90.0):
            raise OrientationPlanError("tilt angles must be greater than 0 and less than 90 degrees.")
        normalized.append(round(value, 6))
    if len(set(normalized)) != len(normalized):
        raise OrientationPlanError("tilt angles must be unique.")
    return tuple(sorted(normalized))


def generate_orientation_specs(
    *,
    tilt_degrees: Iterable[float] = DEFAULT_TILT_DEGREES,
    include_cardinal: bool = True,
) -> tuple[OrientationSpec, ...]:
    """Generate a bounded deterministic resin-orientation proposal set.

    Z spin is deliberately fixed at zero because it does not change the resin build
    direction. A later physical-plate planner may choose Z rotation for packing.
    """
    if not isinstance(include_cardinal, bool):
        raise OrientationPlanError("include_cardinal must be boolean.")

    tilts = _validated_tilts(tilt_degrees)
    raw: list[OrientationSpec] = [OrientationSpec(0.0, 0.0, 0.0)]
    for angle in tilts:
        raw.extend((
            OrientationSpec(angle, 0.0),
            OrientationSpec(-angle, 0.0),
            OrientationSpec(0.0, angle),
            OrientationSpec(0.0, -angle),
            OrientationSpec(angle, angle),
            OrientationSpec(angle, -angle),
            OrientationSpec(-angle, angle),
            OrientationSpec(-angle, -angle),
        ))

    if include_cardinal:
        # Identity above covers +Z. These five cover the other principal build directions.
        raw.extend((
            OrientationSpec(90.0, 0.0),
            OrientationSpec(-90.0, 0.0),
            OrientationSpec(0.0, 90.0),
            OrientationSpec(0.0, -90.0),
            OrientationSpec(180.0, 0.0),
        ))

    result: list[OrientationSpec] = []
    seen: set[tuple[float, float, float]] = set()
    for spec in raw:
        key = spec.canonical_key
        if key not in seen:
            result.append(spec)
            seen.add(key)

    if len(result) > MAX_ORIENTATION_SPECS:
        raise OrientationPlanError(
            f"Generated orientation set exceeds the {MAX_ORIENTATION_SPECS}-candidate safety limit."
        )
    return tuple(result)
