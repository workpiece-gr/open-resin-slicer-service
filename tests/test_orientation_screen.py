import math

import pytest

from app.orientation_candidates import OrientationSpec
from app.orientation_proxy import GeometryProxyMetrics
from app.orientation_screen import (
    OrientationScreenError,
    ProxyCandidate,
    proxy_screening_manifest,
    screen_geometry_proxies,
)


def _metrics(*, area=100, moment=100, height=50, open_contours=0):
    return GeometryProxyMetrics(
        triangle_count=12,
        sampled_layer_count=20,
        full_layer_count=200,
        layer_sampling_stride=10,
        max_sampled_layer_area_mm2=area,
        z_height_mm=height,
        xy_width_mm=30,
        xy_depth_mm=20,
        downward_projected_area_mm2=40,
        downward_support_moment_mm3=moment,
        open_contour_sample_count=open_contours,
    )


def _candidate(x, *, area=100, moment=100, height=50, open_contours=0):
    return ProxyCandidate(
        spec=OrientationSpec(x, 0, 0),
        metrics=_metrics(area=area, moment=moment, height=height, open_contours=open_contours),
    )


def test_pareto_front_precedes_dominated_candidates_and_respects_cap():
    candidates = [
        _candidate(0, area=10, moment=100, height=100),
        _candidate(10, area=100, moment=10, height=100),
        _candidate(20, area=100, moment=100, height=10),
        _candidate(30, area=120, moment=120, height=120),
    ]
    decision = screen_geometry_proxies(candidates, finalist_limit=3)
    assert [item.pareto_rank for item in decision.ranked] == [0, 0, 0, 1]
    assert [item.candidate.spec.canonical_key for item in decision.finalists] == [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (20.0, 0.0, 0.0),
    ]


def test_weight_free_balance_tie_break_prefers_low_maximum_regret():
    candidates = [
        _candidate(10, area=0.1, moment=10, height=10),
        _candidate(20, area=5, moment=5, height=5),
        _candidate(30, area=10, moment=0.1, height=0.1),
    ]
    decision = screen_geometry_proxies(candidates, finalist_limit=1)
    assert decision.finalists[0].candidate.spec.canonical_key == (20.0, 0.0, 0.0)
    assert decision.finalists[0].balance_score < 1


def test_open_contours_are_blocked_before_pareto_ranking():
    decision = screen_geometry_proxies([
        _candidate(10, area=1, moment=1, height=1, open_contours=2),
        _candidate(20, area=100, moment=100, height=100),
    ])
    assert decision.finalists[0].candidate.spec.canonical_key == (20.0, 0.0, 0.0)
    blocked = next(item for item in decision.ranked if item.blocked)
    assert blocked.blocked_reasons == ("open-contours",)
    assert blocked.pareto_rank is None


def test_all_unreliable_candidates_fail_closed_to_manual_review():
    decision = screen_geometry_proxies([
        _candidate(10, open_contours=1),
        _candidate(20, open_contours=2),
    ])
    assert decision.manual_review_required is True
    assert decision.finalists == ()
    assert all(item.blocked for item in decision.ranked)


def test_duplicate_orientations_are_rejected_after_canonicalization():
    with pytest.raises(OrientationScreenError, match="unique"):
        screen_geometry_proxies([
            ProxyCandidate(OrientationSpec(0, 0, 0), _metrics()),
            ProxyCandidate(OrientationSpec(360, 0, 0), _metrics()),
        ])


def test_invalid_metrics_and_finalist_limit_fail_closed():
    bad = _metrics(area=math.inf)
    with pytest.raises(OrientationScreenError, match="max_sampled_layer_area_mm2"):
        screen_geometry_proxies([ProxyCandidate(OrientationSpec(0, 0, 0), bad)])
    with pytest.raises(OrientationScreenError, match="safety cap"):
        screen_geometry_proxies([_candidate(0)], finalist_limit=9)
    with pytest.raises(OrientationScreenError, match="positive integer"):
        screen_geometry_proxies([_candidate(0)], finalist_limit=True)


def test_manifest_keeps_proxy_and_production_authority_separate():
    decision = screen_geometry_proxies([
        _candidate(10, area=50, moment=70, height=60),
        _candidate(20, area=40, moment=80, height=50),
    ], finalist_limit=2)
    manifest = proxy_screening_manifest(decision)
    assert manifest["schema"] == "workpiece-resin-orientation-proxy-screen-v1"
    assert manifest["automatic_production_authority"] is False
    assert manifest["ranking"]["weights"] is None
    assert manifest["objectives"] == [
        "max_sampled_layer_area_mm2",
        "downward_support_moment_mm3",
        "z_height_mm",
    ]
    assert len(manifest["finalists"]) == 2
    assert "not prusa support volume" in manifest["review_rule"].lower()
