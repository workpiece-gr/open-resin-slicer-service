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
    analyze_geometry_proxy,
    parse_proxy_triangles,
)
from .orientation_screen import (
    DEFAULT_PROXY_FINALISTS,
    ProxyCandidate,
    ProxyScreeningDecision,
    proxy_screening_manifest,
    screen_geometry_proxies,
)


PROXY_PLAN_SCHEMA = "workpiece-resin-orientation-proxy-plan-v1"


@dataclass(frozen=True)
class ProxyOrientationPlan:
    source_sha256: str
    triangle_count: int
    candidates: tuple[ProxyCandidate, ...]
    screening: ProxyScreeningDecision


def build_proxy_orientation_plan(
    source_stl: bytes,
    *,
    tilt_degrees: Iterable[float] = DEFAULT_TILT_DEGREES,
    include_cardinal: bool = True,
    finalist_limit: int = DEFAULT_PROXY_FINALISTS,
    layer_height_mm: float = DEFAULT_LAYER_HEIGHT_MM,
    max_sample_layers: int = DEFAULT_MAX_SAMPLE_LAYERS,
) -> ProxyOrientationPlan:
    """Run the complete bounded geometry-proxy orientation stage for one exact STL."""
    triangles = parse_proxy_triangles(source_stl)
    specs: tuple[OrientationSpec, ...] = generate_orientation_specs(
        tilt_degrees=tilt_degrees,
        include_cardinal=include_cardinal,
    )
    candidates = tuple(
        ProxyCandidate(
            spec=spec,
            metrics=analyze_geometry_proxy(
                triangles,
                spec,
                layer_height_mm=layer_height_mm,
                max_sample_layers=max_sample_layers,
            ),
        )
        for spec in specs
    )
    screening = screen_geometry_proxies(
        candidates,
        finalist_limit=finalist_limit,
    )
    return ProxyOrientationPlan(
        source_sha256=hashlib.sha256(source_stl).hexdigest(),
        triangle_count=len(triangles),
        candidates=candidates,
        screening=screening,
    )


def proxy_orientation_plan_manifest(plan: ProxyOrientationPlan) -> dict:
    screen = proxy_screening_manifest(plan.screening)
    return {
        "schema": PROXY_PLAN_SCHEMA,
        "source_sha256": plan.source_sha256,
        "triangle_count": plan.triangle_count,
        "candidate_count": len(plan.candidates),
        "automatic_production_authority": False,
        "screening": screen,
        "handoff_rule": (
            "Only the exact finalist orientations in this source-bound proxy plan may proceed to sliced validation. "
            "Sliced evidence must bind back to this same source STL hash before any final orientation selection."
        ),
    }
