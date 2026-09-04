import math

import pytest

from app.orientation import (
    OrientationCandidate,
    OrientationEvidence,
    OrientationEvaluationError,
    OrientationPolicy,
    evaluate_orientation,
    rank_orientations,
)


POLICY = OrientationPolicy(policy_id="mars2-grey-orientation-candidate-v1")


def _provenance(stage="uvtools-inspected"):
    values = {"source_stl_sha256": "a" * 64}
    if stage in {"sliced", "uvtools-inspected"}:
        values.update(
            {
                "review_3mf_sha256": "b" * 64,
                "intermediate_sl1_sha256": "c" * 64,
                "prusaslicer_commit": "b028299c770b8380ee81c921a2867d522f288123",
            }
        )
    if stage == "uvtools-inspected":
        values.update({"printer_native_sha256": "d" * 64, "uvtools_version": "6.2.0"})
    return values


def _evidence(**overrides):
    stage = overrides.get("evidence_stage", "uvtools-inspected")
    args = dict(
        evidence_stage=stage,
        bounds_fit=True,
        z_height_mm=80,
        peak_layer_area_mm2=2500,
        support_volume_mm3=1200,
        support_contact_count=12,
        estimated_print_time_s=7200,
        island_count=0,
        suction_cup_count=0,
        sealed_cavity_count=0,
        resin_trap_count=0,
        provenance=_provenance(stage),
    )
    args.update(overrides)
    return OrientationEvidence(**args)


def _candidate(candidate_id="c1", **overrides):
    args = dict(candidate_id=candidate_id, x_deg=370, y_deg=-190, z_deg=0)
    args.update(overrides)
    return OrientationCandidate(**args)


def test_evaluation_is_deterministic_and_preserves_component_breakdown():
    result = evaluate_orientation(_candidate(), _evidence(), policy=POLICY)
    assert result["orientation_deg"] == {"x": 10.0, "y": 170.0, "z": 0.0}
    assert result["hard_blocked"] is False
    assert result["production_evidence_complete"] is True
    assert result["human_review_required"] is True
    assert result["authority"] == "candidate-ranking-only"
    assert result["soft_score"] == sum(result["score_components"].values())


def test_hard_blockers_fail_closed_before_soft_ranking():
    result = evaluate_orientation(
        _candidate(),
        _evidence(
            bounds_fit=False,
            island_count=1,
            suction_cup_count=1,
            sealed_cavity_count=1,
            resin_trap_count=1,
        ),
        policy=POLICY,
    )
    assert result["hard_blocked"] is True
    assert result["hard_blockers"] == [
        "outside_validated_manufacturing_envelope",
        "islands_exceed_policy",
        "suction_cups_exceed_policy",
        "sealed_cavities_exceed_policy",
        "resin_traps_exceed_policy",
    ]


def test_geometry_only_evidence_can_rank_but_never_claims_production_completeness():
    result = evaluate_orientation(
        _candidate(),
        _evidence(evidence_stage="geometry-only", provenance=_provenance("geometry-only")),
        policy=POLICY,
    )
    assert result["production_evidence_complete"] is False
    assert result["authority"] == "candidate-ranking-only"


def test_uvtools_stage_requires_exact_downstream_provenance():
    provenance = _provenance()
    del provenance["printer_native_sha256"]
    with pytest.raises(OrientationEvaluationError, match="printer_native_sha256"):
        evaluate_orientation(_candidate(), _evidence(provenance=provenance), policy=POLICY)


def test_ranking_puts_unblocked_candidates_before_blocked_then_uses_score_and_id():
    b = evaluate_orientation(_candidate("b"), _evidence(z_height_mm=90), policy=POLICY)
    a = evaluate_orientation(_candidate("a"), _evidence(z_height_mm=90), policy=POLICY)
    blocked = evaluate_orientation(_candidate("blocked"), _evidence(island_count=1, z_height_mm=1), policy=POLICY)
    assert [item["candidate_id"] for item in rank_orientations([blocked, b, a])] == ["a", "b", "blocked"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("z_height_mm", math.inf),
        ("support_volume_mm3", -1),
        ("support_contact_count", True),
        ("island_count", -1),
    ],
)
def test_invalid_measurements_are_rejected(field, value):
    with pytest.raises(OrientationEvaluationError):
        evaluate_orientation(_candidate(), _evidence(**{field: value}), policy=POLICY)


def test_policy_references_must_be_positive_and_ids_unique():
    with pytest.raises(OrientationEvaluationError, match="greater than zero"):
        evaluate_orientation(
            _candidate(),
            _evidence(),
            policy=OrientationPolicy(policy_id="p", reference_z_height_mm=0),
        )
    first = evaluate_orientation(_candidate("same"), _evidence(), policy=POLICY)
    second = evaluate_orientation(_candidate("same"), _evidence(z_height_mm=81), policy=POLICY)
    with pytest.raises(OrientationEvaluationError, match="unique"):
        rank_orientations([first, second])


def test_provenance_hashes_are_validated():
    provenance = _provenance()
    provenance["printer_native_sha256"] = "not-a-hash"
    with pytest.raises(OrientationEvaluationError, match="SHA-256"):
        evaluate_orientation(_candidate(), _evidence(provenance=provenance), policy=POLICY)
