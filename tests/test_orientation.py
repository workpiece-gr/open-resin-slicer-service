import math

import pytest

from app.orientation import (
    OrientationCandidate,
    OrientationMetrics,
    OrientationPlanError,
    orientation_decision_manifest,
    rank_orientation_candidates,
)


def _candidate(
    x=0,
    y=0,
    z=0,
    *,
    area=100,
    material=1000,
    footprint=400,
    height=50,
    islands=0,
    cups=0,
    traps=0,
    source="geometry-proxy",
):
    return OrientationCandidate(
        x_deg=x,
        y_deg=y,
        z_deg=z,
        metrics=OrientationMetrics(
            max_layer_area_mm2=area,
            material_volume_mm3=material,
            footprint_area_mm2=footprint,
            z_height_mm=height,
            unresolved_islands=islands,
            unresolved_suction_cups=cups,
            unresolved_resin_traps=traps,
            source=source,
        ),
    )


def test_lower_peel_cross_section_wins_when_other_metrics_match():
    decision = rank_orientation_candidates([
        _candidate(x=0, area=200),
        _candidate(x=20, area=80),
    ])
    assert decision.selected is not None
    assert decision.selected.candidate.canonical_key == (20.0, 0.0, 0.0)


def test_lower_material_and_footprint_burden_are_soft_objectives():
    decision = rank_orientation_candidates([
        _candidate(x=10, material=2000, footprint=600),
        _candidate(x=20, material=1200, footprint=300),
    ])
    assert decision.selected is not None
    assert decision.selected.candidate.canonical_key == (20.0, 0.0, 0.0)


def test_critical_resin_failures_block_candidate_before_soft_scoring():
    decision = rank_orientation_candidates([
        _candidate(x=10, area=20, material=500, footprint=200, height=20, cups=1),
        _candidate(x=30, area=100, material=1000, footprint=400, height=50),
    ])
    assert decision.selected is not None
    assert decision.selected.candidate.canonical_key == (30.0, 0.0, 0.0)
    blocked = next(
        item for item in decision.ranked if item.candidate.canonical_key == (10.0, 0.0, 0.0)
    )
    assert blocked.score is None
    assert blocked.blocked_reasons == ("unresolved-suction-cups",)


def test_sliced_validation_requirement_filters_proxy_metrics():
    decision = rank_orientation_candidates([
        _candidate(x=10, source="geometry-proxy"),
        _candidate(x=20, source="sliced-validation"),
    ], require_sliced_validation=True)
    assert decision.selected is not None
    assert decision.selected.candidate.canonical_key == (20.0, 0.0, 0.0)
    proxy = next(
        item for item in decision.ranked if item.candidate.canonical_key == (10.0, 0.0, 0.0)
    )
    assert "metrics-not-sliced-validation" in proxy.blocked_reasons


def test_no_viable_orientation_fails_closed_to_manual_review():
    decision = rank_orientation_candidates([
        _candidate(x=10, islands=1),
        _candidate(x=20, traps=1),
    ])
    assert decision.selected is None
    assert decision.manual_review_required is True
    manifest = orientation_decision_manifest(decision)
    assert manifest["status"] == "manual-review-required"
    assert manifest["selected"] is None


def test_exact_tie_prefers_least_rotation_then_canonical_xyz():
    decision = rank_orientation_candidates([
        _candidate(x=30),
        _candidate(y=20),
        _candidate(z=-20),
    ])
    assert decision.selected is not None
    assert decision.selected.candidate.canonical_key == (0.0, 0.0, 340.0)


def test_duplicate_canonical_orientations_are_rejected():
    with pytest.raises(OrientationPlanError, match="unique"):
        rank_orientation_candidates([
            _candidate(z=0),
            _candidate(z=360),
        ])


def test_invalid_metrics_and_weights_fail_closed():
    with pytest.raises(OrientationPlanError, match="max_layer_area_mm2"):
        rank_orientation_candidates([_candidate(area=math.inf)])
    with pytest.raises(OrientationPlanError, match="unresolved_islands"):
        rank_orientation_candidates([_candidate(islands=True)])
    with pytest.raises(OrientationPlanError, match="sum to 1.0"):
        rank_orientation_candidates([_candidate()], weights={
            "max_layer_area_mm2": 1,
            "material_volume_mm3": 1,
            "footprint_area_mm2": 0,
            "z_height_mm": 0,
        })


def test_manifest_preserves_native_metric_contract_and_review_boundary():
    decision = rank_orientation_candidates([
        _candidate(x=15, area=120, source="sliced-validation"),
        _candidate(x=30, area=80, source="sliced-validation"),
    ], require_sliced_validation=True)
    manifest = orientation_decision_manifest(decision)
    assert manifest["schema"] == "workpiece-resin-orientation-decision-v2"
    assert manifest["automatic_production_authority"] is False
    assert manifest["require_sliced_validation"] is True
    assert manifest["selected"]["orientation_deg"] == {"x": 30.0, "y": 0.0, "z": 0.0}
    assert manifest["selected"]["metric_source"] == "sliced-validation"
    assert set(manifest["selected"]["score_components"]) == {
        "max_layer_area_mm2",
        "material_volume_mm3",
        "footprint_area_mm2",
        "z_height_mm",
    }
    assert manifest["scoring"]["metric_contract"] == "exact retained native artifact for sliced-validation"
    assert "not production authorization" in manifest["review_rule"].lower()
