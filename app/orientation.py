from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


ORIENTATION_DECISION_SCHEMA = "workpiece-resin-orientation-decision-v2"
METRIC_SOURCES = {"geometry-proxy", "sliced-validation"}
DEFAULT_WEIGHTS = {
    "max_layer_area_mm2": 0.40,
    "material_volume_mm3": 0.25,
    "footprint_area_mm2": 0.20,
    "z_height_mm": 0.15,
}


class OrientationPlanError(ValueError):
    pass


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationPlanError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationPlanError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise OrientationPlanError(f"{name} must be a finite number.")
    return result


def _nonnegative(name: str, value: float) -> float:
    result = _finite(name, value)
    if result < 0:
        raise OrientationPlanError(f"{name} must be non-negative.")
    return result


def _count(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OrientationPlanError(f"{name} must be a non-negative integer.")
    return value


def _canonical_angle(value: float) -> float:
    normalized = _finite("orientation angle", value) % 360.0
    if math.isclose(normalized, 360.0, abs_tol=1e-9) or math.isclose(normalized, 0.0, abs_tol=1e-9):
        return 0.0
    return round(normalized, 6)


@dataclass(frozen=True)
class OrientationMetrics:
    """Comparable orientation metrics.

    For sliced-validation these values must come from the exact retained printer-native
    artifact. Critical UVtools issue counts are hard blockers before soft scoring.
    """

    max_layer_area_mm2: float
    material_volume_mm3: float
    footprint_area_mm2: float
    z_height_mm: float
    unresolved_islands: int = 0
    unresolved_suction_cups: int = 0
    unresolved_resin_traps: int = 0
    unresolved_touching_bounds: int = 0
    unresolved_empty_layers: int = 0
    source: str = "geometry-proxy"

    def validate(self) -> "OrientationMetrics":
        _nonnegative("max_layer_area_mm2", self.max_layer_area_mm2)
        _nonnegative("material_volume_mm3", self.material_volume_mm3)
        _nonnegative("footprint_area_mm2", self.footprint_area_mm2)
        _nonnegative("z_height_mm", self.z_height_mm)
        _count("unresolved_islands", self.unresolved_islands)
        _count("unresolved_suction_cups", self.unresolved_suction_cups)
        _count("unresolved_resin_traps", self.unresolved_resin_traps)
        _count("unresolved_touching_bounds", self.unresolved_touching_bounds)
        _count("unresolved_empty_layers", self.unresolved_empty_layers)
        if self.source not in METRIC_SOURCES:
            raise OrientationPlanError(
                f"metrics source must be one of: {', '.join(sorted(METRIC_SOURCES))}."
            )
        return self


@dataclass(frozen=True)
class OrientationCandidate:
    x_deg: float
    y_deg: float
    z_deg: float
    metrics: OrientationMetrics

    def validate(self) -> "OrientationCandidate":
        for axis, value in (("x_deg", self.x_deg), ("y_deg", self.y_deg), ("z_deg", self.z_deg)):
            numeric = _finite(axis, value)
            if not (-360.0 <= numeric <= 360.0):
                raise OrientationPlanError(f"{axis} must be between -360 and 360 degrees.")
        self.metrics.validate()
        return self

    @property
    def canonical_key(self) -> tuple[float, float, float]:
        return (
            _canonical_angle(self.x_deg),
            _canonical_angle(self.y_deg),
            _canonical_angle(self.z_deg),
        )

    @property
    def rotation_magnitude(self) -> float:
        def shortest(value: float) -> float:
            angle = _canonical_angle(value)
            return min(angle, 360.0 - angle)

        return round(shortest(self.x_deg) + shortest(self.y_deg) + shortest(self.z_deg), 6)


@dataclass(frozen=True)
class ScoredOrientation:
    candidate: OrientationCandidate
    blocked_reasons: tuple[str, ...]
    score: float | None
    score_components: Mapping[str, float]

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons)


@dataclass(frozen=True)
class OrientationDecision:
    selected: ScoredOrientation | None
    ranked: tuple[ScoredOrientation, ...]
    require_sliced_validation: bool

    @property
    def manual_review_required(self) -> bool:
        return self.selected is None


def _validated_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    raw = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if set(raw) != set(DEFAULT_WEIGHTS):
        raise OrientationPlanError(
            f"weights must contain exactly: {', '.join(DEFAULT_WEIGHTS)}."
        )
    result = {name: _nonnegative(f"weight {name}", value) for name, value in raw.items()}
    total = sum(result.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise OrientationPlanError("orientation weights must sum to 1.0.")
    return result


def _blocked_reasons(candidate: OrientationCandidate, *, require_sliced_validation: bool) -> tuple[str, ...]:
    metrics = candidate.metrics
    reasons: list[str] = []
    if metrics.unresolved_islands:
        reasons.append("unresolved-islands")
    if metrics.unresolved_suction_cups:
        reasons.append("unresolved-suction-cups")
    if metrics.unresolved_resin_traps:
        reasons.append("unresolved-resin-traps")
    if metrics.unresolved_touching_bounds:
        reasons.append("touching-bounds")
    if metrics.unresolved_empty_layers:
        reasons.append("empty-layers")
    if require_sliced_validation and metrics.source != "sliced-validation":
        reasons.append("metrics-not-sliced-validation")
    return tuple(reasons)


def _metric_value(candidate: OrientationCandidate, name: str) -> float:
    return float(getattr(candidate.metrics, name))


def rank_orientation_candidates(
    candidates: Sequence[OrientationCandidate],
    *,
    require_sliced_validation: bool = False,
    weights: Mapping[str, float] | None = None,
) -> OrientationDecision:
    if not candidates:
        raise OrientationPlanError("At least one orientation candidate is required.")

    validated = [candidate.validate() for candidate in candidates]
    seen: set[tuple[float, float, float]] = set()
    for candidate in validated:
        if candidate.canonical_key in seen:
            raise OrientationPlanError(
                "Orientation candidates must be unique after canonical angle normalization."
            )
        seen.add(candidate.canonical_key)

    selected_weights = _validated_weights(weights)
    blocked = {
        candidate.canonical_key: _blocked_reasons(
            candidate, require_sliced_validation=require_sliced_validation
        )
        for candidate in validated
    }
    eligible = [candidate for candidate in validated if not blocked[candidate.canonical_key]]

    ranges: dict[str, tuple[float, float]] = {}
    for name in selected_weights:
        values = [_metric_value(candidate, name) for candidate in eligible]
        ranges[name] = (min(values), max(values)) if values else (0.0, 0.0)

    scored: list[ScoredOrientation] = []
    for candidate in validated:
        reasons = blocked[candidate.canonical_key]
        if reasons:
            scored.append(
                ScoredOrientation(
                    candidate=candidate,
                    blocked_reasons=reasons,
                    score=None,
                    score_components={},
                )
            )
            continue

        components: dict[str, float] = {}
        score = 0.0
        for name, weight in selected_weights.items():
            low, high = ranges[name]
            value = _metric_value(candidate, name)
            normalized = 0.0 if math.isclose(high, low) else (value - low) / (high - low)
            contribution = weight * normalized
            components[name] = round(contribution, 9)
            score += contribution
        scored.append(
            ScoredOrientation(
                candidate=candidate,
                blocked_reasons=(),
                score=round(score, 9),
                score_components=components,
            )
        )

    def sort_key(item: ScoredOrientation) -> tuple:
        if item.score is None:
            return (1, math.inf, math.inf, item.candidate.canonical_key)
        return (0, item.score, item.candidate.rotation_magnitude, item.candidate.canonical_key)

    ranked = tuple(sorted(scored, key=sort_key))
    selected = next((item for item in ranked if item.score is not None), None)
    return OrientationDecision(
        selected=selected,
        ranked=ranked,
        require_sliced_validation=require_sliced_validation,
    )


def orientation_decision_manifest(
    decision: OrientationDecision,
    *,
    weights: Mapping[str, float] | None = None,
) -> dict:
    selected_weights = _validated_weights(weights)

    def candidate_payload(item: ScoredOrientation) -> dict:
        candidate = item.candidate
        metrics = candidate.metrics
        return {
            "orientation_deg": {
                "x": candidate.canonical_key[0],
                "y": candidate.canonical_key[1],
                "z": candidate.canonical_key[2],
            },
            "metric_source": metrics.source,
            "metrics": {
                "max_layer_area_mm2": float(metrics.max_layer_area_mm2),
                "material_volume_mm3": float(metrics.material_volume_mm3),
                "footprint_area_mm2": float(metrics.footprint_area_mm2),
                "z_height_mm": float(metrics.z_height_mm),
                "unresolved_islands": metrics.unresolved_islands,
                "unresolved_suction_cups": metrics.unresolved_suction_cups,
                "unresolved_resin_traps": metrics.unresolved_resin_traps,
                "unresolved_touching_bounds": metrics.unresolved_touching_bounds,
                "unresolved_empty_layers": metrics.unresolved_empty_layers,
            },
            "blocked_reasons": list(item.blocked_reasons),
            "score": item.score,
            "score_components": dict(item.score_components),
        }

    selected_payload = candidate_payload(decision.selected) if decision.selected else None
    return {
        "schema": ORIENTATION_DECISION_SCHEMA,
        "status": "manual-review-required" if decision.manual_review_required else "selected",
        "automatic_production_authority": False,
        "require_sliced_validation": decision.require_sliced_validation,
        "hard_blockers": [
            "unresolved-islands",
            "unresolved-suction-cups",
            "unresolved-resin-traps",
            "touching-bounds",
            "empty-layers",
        ],
        "scoring": {
            "normalization": "min-max across unblocked candidates",
            "weights": selected_weights,
            "metric_contract": "exact retained native artifact for sliced-validation",
            "tie_break": "lowest score, then least total rotation, then canonical XYZ angles",
        },
        "selected": selected_payload,
        "candidates": [candidate_payload(item) for item in decision.ranked],
        "review_rule": (
            "This decision is an orientation proposal, not production authorization. "
            "Sliced-validation metrics must be reproducibly extracted from the exact retained printer-native artifact; "
            "production additionally requires retained artifact inspection and physical acceptance of the calibrated tuple."
        ),
    }
