from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from .orientation_candidates import (
    DEFAULT_TILT_DEGREES,
    OrientationSpec,
    generate_orientation_specs,
)
from .orientation_proxy import (
    DEFAULT_LAYER_HEIGHT_MM,
    DEFAULT_MAX_SAMPLE_LAYERS,
    GeometryProxyMetrics,
    OrientationProxyError,
    analyze_geometry_proxy,
    parse_proxy_triangles,
)
from .orientation_screen import (
    DEFAULT_PROXY_FINALISTS,
    MAX_PROXY_FINALISTS,
    OrientationScreenError,
    ProxyCandidate,
    ProxyScreeningDecision,
    proxy_screening_manifest,
    screen_geometry_proxies,
)


PROXY_PLAN_SCHEMA = "workpiece-resin-orientation-proxy-plan-v1"
MAX_PROXY_TRIANGLE_LAYER_EVALUATIONS = 24_000_000


@dataclass(frozen=True)
class ProxyOrientationPlan:
    source_sha256: str
    triangle_count: int
    candidates: tuple[ProxyCandidate, ...]
    screening: ProxyScreeningDecision


def _validate_finalist_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrientationScreenError("finalist_limit must be a positive integer.")
    if value > MAX_PROXY_FINALISTS:
        raise OrientationScreenError(
            f"finalist_limit cannot exceed the {MAX_PROXY_FINALISTS}-candidate safety cap."
        )
    return value


def _validate_sample_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrientationProxyError("max_sample_layers must be a positive integer.")
    return value


def _budgeted_sample_limit(*, triangle_count: int, candidate_count: int, requested: int) -> int:
    requested = _validate_sample_limit(requested)
    denominator = triangle_count * candidate_count
    if denominator <= 0:
        raise OrientationProxyError("Proxy work budget requires positive triangle and candidate counts.")
    budgeted = max(1, MAX_PROXY_TRIANGLE_LAYER_EVALUATIONS // denominator)
    return min(requested, budgeted)


def build_proxy_orientation_plan(
    source_stl: bytes,
    *,
    tilt_degrees: Iterable[float] = DEFAULT_TILT_DEGREES,
    include_cardinal: bool = True,
    finalist_limit: int = DEFAULT_PROXY_FINALISTS,
    layer_height_mm: float = DEFAULT_LAYER_HEIGHT_MM,
    max_sample_layers: int = DEFAULT_MAX_SAMPLE_LAYERS,
) -> ProxyOrientationPlan:
    """Run bounded geometry-proxy screening for one exact STL.

    The layer-sampling budget is reduced automatically for high-triangle meshes so a
    proxy request cannot multiply into unbounded triangle/layer work. This stage is
    screening only; exact retained native artifacts remain the final metric authority.
    """
    limit = _validate_finalist_limit(finalist_limit)
    requested_samples = _validate_sample_limit(max_sample_layers)
    triangles = parse_proxy_triangles(source_stl)
    specs: tuple[OrientationSpec, ...] = generate_orientation_specs(
        tilt_degrees=tilt_degrees,
        include_cardinal=include_cardinal,
    )
    effective_samples = _budgeted_sample_limit(
        triangle_count=len(triangles),
        candidate_count=len(specs),
        requested=requested_samples,
    )
    candidates = tuple(
        ProxyCandidate(
            spec=spec,
            metrics=analyze_geometry_proxy(
                triangles,
                spec,
                layer_height_mm=layer_height_mm,
                max_sample_layers=effective_samples,
            ),
        )
        for spec in specs
    )
    screening = screen_geometry_proxies(
        candidates,
        finalist_limit=limit,
    )
    return ProxyOrientationPlan(
        source_sha256=hashlib.sha256(source_stl).hexdigest(),
        triangle_count=len(triangles),
        candidates=candidates,
        screening=screening,
    )


def proxy_orientation_plan_manifest(plan: ProxyOrientationPlan) -> dict:
    screen = proxy_screening_manifest(plan.screening)
    estimated_evaluations = sum(
        item.metrics.triangle_count * item.metrics.sampled_layer_count
        for item in plan.candidates
    )
    return {
        "schema": PROXY_PLAN_SCHEMA,
        "source_sha256": plan.source_sha256,
        "triangle_count": plan.triangle_count,
        "candidate_count": len(plan.candidates),
        "automatic_production_authority": False,
        "proxy_work_budget": {
            "triangle_layer_evaluation_cap": MAX_PROXY_TRIANGLE_LAYER_EVALUATIONS,
            "estimated_triangle_layer_evaluations": estimated_evaluations,
            "max_sampled_layers_per_candidate": max(
                item.metrics.sampled_layer_count for item in plan.candidates
            ),
        },
        "screening": screen,
        "handoff_rule": (
            "Only the exact finalist orientations in this source-bound proxy plan may proceed to sliced validation. "
            "Sliced evidence must bind back to this same source STL hash before any final orientation selection."
        ),
    }
