from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .orientation_candidates import OrientationSpec
from .orientation_proxy import GeometryProxyMetrics


PROXY_SCREEN_SCHEMA = "workpiece-resin-orientation-proxy-screen-v1"
PROXY_OBJECTIVES = (
    "max_sampled_layer_area_mm2",
    "downward_support_moment_mm3",
    "z_height_mm",
)
DEFAULT_PROXY_FINALISTS = 5
MAX_PROXY_FINALISTS = 8


class OrientationScreenError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyCandidate:
    spec: OrientationSpec
    metrics: GeometryProxyMetrics


@dataclass(frozen=True)
class ScreenedProxy:
    candidate: ProxyCandidate
    blocked_reasons: tuple[str, ...]
    pareto_rank: int | None
    balance_score: float | None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons)


@dataclass(frozen=True)
class ProxyScreeningDecision:
    finalists: tuple[ScreenedProxy, ...]
    ranked: tuple[ScreenedProxy, ...]
    finalist_limit: int

    @property
    def manual_review_required(self) -> bool:
        return not self.finalists


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrientationScreenError(f"{name} must be a positive integer.")
    return value


def _nonnegative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OrientationScreenError(f"{name} must be a non-negative integer.")
    return value


def _nonnegative_finite(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationScreenError(f"{name} must be a non-negative finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationScreenError(f"{name} must be a non-negative finite number.") from exc
    if not math.isfinite(result) or result < 0:
        raise OrientationScreenError(f"{name} must be a non-negative finite number.")
    return result


def _validate_metrics(metrics: GeometryProxyMetrics) -> GeometryProxyMetrics:
    triangle_count = _positive_int("triangle_count", metrics.triangle_count)
    sampled = _positive_int("sampled_layer_count", metrics.sampled_layer_count)
    full = _positive_int("full_layer_count", metrics.full_layer_count)
    _positive_int("layer_sampling_stride", metrics.layer_sampling_stride)
    if sampled > full:
        raise OrientationScreenError("sampled_layer_count cannot exceed full_layer_count.")
    open_count = _nonnegative_int("open_contour_sample_count", metrics.open_contour_sample_count)
    if open_count > sampled:
        raise OrientationScreenError("open_contour_sample_count cannot exceed sampled_layer_count.")
    if triangle_count < 1:
        raise OrientationScreenError("triangle_count must be positive.")

    area = _nonnegative_finite("max_sampled_layer_area_mm2", metrics.max_sampled_layer_area_mm2)
    height = _nonnegative_finite("z_height_mm", metrics.z_height_mm)
    _nonnegative_finite("xy_width_mm", metrics.xy_width_mm)
    _nonnegative_finite("xy_depth_mm", metrics.xy_depth_mm)
    _nonnegative_finite("downward_projected_area_mm2", metrics.downward_projected_area_mm2)
    _nonnegative_finite("downward_support_moment_mm3", metrics.downward_support_moment_mm3)
    if area <= 0:
        raise OrientationScreenError("max_sampled_layer_area_mm2 must be positive for proxy screening.")
    if height <= 0:
        raise OrientationScreenError("z_height_mm must be positive for proxy screening.")
    return metrics


def _rotation_magnitude(spec: OrientationSpec) -> float:
    return round(sum(min(angle, 360.0 - angle) for angle in spec.canonical_key), 6)


def _objective_values(candidate: ProxyCandidate) -> tuple[float, ...]:
    return tuple(float(getattr(candidate.metrics, name)) for name in PROXY_OBJECTIVES)


def _dominates(first: ProxyCandidate, second: ProxyCandidate) -> bool:
    a = _objective_values(first)
    b = _objective_values(second)
    return all(left <= right for left, right in zip(a, b)) and any(
        left < right for left, right in zip(a, b)
    )


def _pareto_ranks(candidates: Sequence[ProxyCandidate]) -> dict[int, int]:
    remaining = set(range(len(candidates)))
    ranks: dict[int, int] = {}
    rank = 0
    while remaining:
        front = [
            index
            for index in sorted(remaining)
            if not any(
                other != index and _dominates(candidates[other], candidates[index])
                for other in remaining
            )
        ]
        if not front:
            raise OrientationScreenError("Unable to derive deterministic Pareto ranks.")
        for index in front:
            ranks[index] = rank
        remaining.difference_update(front)
        rank += 1
    return ranks


def screen_geometry_proxies(
    candidates: Sequence[ProxyCandidate],
    *,
    finalist_limit: int = DEFAULT_PROXY_FINALISTS,
) -> ProxyScreeningDecision:
    """Prune geometry proxies without converting them into authoritative support metrics."""
    if not candidates:
        raise OrientationScreenError("At least one geometry-proxy candidate is required.")
    limit = _positive_int("finalist_limit", finalist_limit)
    if limit > MAX_PROXY_FINALISTS:
        raise OrientationScreenError(
            f"finalist_limit cannot exceed the {MAX_PROXY_FINALISTS}-candidate safety cap."
        )

    seen: set[tuple[float, float, float]] = set()
    blocked_by_index: dict[int, tuple[str, ...]] = {}
    eligible_indices: list[int] = []
    for index, candidate in enumerate(candidates):
        candidate.spec.validate()
        _validate_metrics(candidate.metrics)
        key = candidate.spec.canonical_key
        if key in seen:
            raise OrientationScreenError(
                "Geometry-proxy candidates must be unique after canonical angle normalization."
            )
        seen.add(key)

        reasons: list[str] = []
        if not candidate.metrics.reliable_for_auto_screening:
            reasons.append("open-contours")
        blocked_by_index[index] = tuple(reasons)
        if not reasons:
            eligible_indices.append(index)

    if not eligible_indices:
        blocked = tuple(
            ScreenedProxy(
                candidate=candidates[index],
                blocked_reasons=blocked_by_index[index],
                pareto_rank=None,
                balance_score=None,
            )
            for index in sorted(range(len(candidates)), key=lambda item: candidates[item].spec.canonical_key)
        )
        return ProxyScreeningDecision(finalists=(), ranked=blocked, finalist_limit=limit)

    eligible = [candidates[index] for index in eligible_indices]
    ranks = _pareto_ranks(eligible)

    ranges: dict[str, tuple[float, float]] = {}
    for name in PROXY_OBJECTIVES:
        values = [float(getattr(candidate.metrics, name)) for candidate in eligible]
        ranges[name] = (min(values), max(values))

    normalized: dict[tuple[float, float, float], tuple[float, ...]] = {}
    for candidate in eligible:
        values: list[float] = []
        for name in PROXY_OBJECTIVES:
            low, high = ranges[name]
            raw = float(getattr(candidate.metrics, name))
            values.append(0.0 if math.isclose(low, high) else (raw - low) / (high - low))
        normalized[candidate.spec.canonical_key] = tuple(values)

    screened: list[ScreenedProxy] = []
    for local_index, candidate in enumerate(eligible):
        norm = normalized[candidate.spec.canonical_key]
        screened.append(
            ScreenedProxy(
                candidate=candidate,
                blocked_reasons=(),
                pareto_rank=ranks[local_index],
                balance_score=round(max(norm), 9),
            )
        )

    screened.sort(
        key=lambda item: (
            item.pareto_rank,
            item.balance_score,
            round(sum(normalized[item.candidate.spec.canonical_key]), 9),
            _rotation_magnitude(item.candidate.spec),
            item.candidate.spec.canonical_key,
        )
    )

    blocked = [
        ScreenedProxy(
            candidate=candidates[index],
            blocked_reasons=blocked_by_index[index],
            pareto_rank=None,
            balance_score=None,
        )
        for index in range(len(candidates))
        if blocked_by_index[index]
    ]
    blocked.sort(key=lambda item: item.candidate.spec.canonical_key)
    ranked = tuple(screened + blocked)
    return ProxyScreeningDecision(
        finalists=tuple(screened[:limit]),
        ranked=ranked,
        finalist_limit=limit,
    )


def proxy_screening_manifest(decision: ProxyScreeningDecision) -> dict:
    def payload(item: ScreenedProxy) -> dict:
        metrics = item.candidate.metrics
        return {
            "orientation_deg": {
                "x": item.candidate.spec.canonical_key[0],
                "y": item.candidate.spec.canonical_key[1],
                "z": item.candidate.spec.canonical_key[2],
            },
            "pareto_rank": item.pareto_rank,
            "balance_score": item.balance_score,
            "blocked_reasons": list(item.blocked_reasons),
            "proxy_metrics": {
                "max_sampled_layer_area_mm2": metrics.max_sampled_layer_area_mm2,
                "downward_support_moment_mm3": metrics.downward_support_moment_mm3,
                "z_height_mm": metrics.z_height_mm,
                "downward_projected_area_mm2": metrics.downward_projected_area_mm2,
                "open_contour_sample_count": metrics.open_contour_sample_count,
                "sampled_layer_count": metrics.sampled_layer_count,
                "full_layer_count": metrics.full_layer_count,
            },
        }

    finalist_keys = {item.candidate.spec.canonical_key for item in decision.finalists}
    return {
        "schema": PROXY_SCREEN_SCHEMA,
        "status": "manual-review-required" if decision.manual_review_required else "finalists-selected",
        "automatic_production_authority": False,
        "finalist_limit": decision.finalist_limit,
        "objectives": list(PROXY_OBJECTIVES),
        "ranking": {
            "primary": "non-dominated Pareto rank; lower is better",
            "within_rank": "lowest normalized maximum regret, then normalized total, least rotation, canonical XYZ",
            "weights": None,
        },
        "hard_blockers": ["open-contours"],
        "finalists": [payload(item) for item in decision.finalists],
        "candidates": [
            {**payload(item), "is_finalist": item.candidate.spec.canonical_key in finalist_keys}
            for item in decision.ranked
        ],
        "review_rule": (
            "Proxy screening only chooses candidates for expensive sliced validation. "
            "Proxy downward-area/moment signals are not Prusa support volume or support-contact authority, "
            "and no proxy finalist may enter production without sliced validation and retained artifact review."
        ),
    }
