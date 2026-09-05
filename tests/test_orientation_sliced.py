import pytest

from app.orientation_candidates import OrientationSpec
from app.orientation_proxy import GeometryProxyMetrics
from app.orientation_screen import ProxyCandidate, screen_geometry_proxies
from app.orientation_sliced import (
    SlicedFinalistEvidence,
    SlicedOrientationValidationError,
    sliced_orientation_manifest,
    validate_sliced_finalists,
)


PROJECT_SHA = "a" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


def _proxy_metrics(*, area: float, moment: float, height: float) -> GeometryProxyMetrics:
    return GeometryProxyMetrics(
        triangle_count=12,
        sampled_layer_count=20,
        full_layer_count=20,
        layer_sampling_stride=1,
        max_sampled_layer_area_mm2=area,
        z_height_mm=height,
        xy_width_mm=20,
        xy_depth_mm=20,
        downward_projected_area_mm2=10,
        downward_support_moment_mm3=moment,
        open_contour_sample_count=0,
    )


def _screening(finalist_limit: int = 2):
    return screen_geometry_proxies(
        (
            ProxyCandidate(
                OrientationSpec(0, 0, 0),
                _proxy_metrics(area=300, moment=1000, height=20),
            ),
            ProxyCandidate(
                OrientationSpec(15, 0, 0),
                _proxy_metrics(area=220, moment=800, height=23),
            ),
            ProxyCandidate(
                OrientationSpec(30, 0, 0),
                _proxy_metrics(area=180, moment=700, height=28),
            ),
        ),
        finalist_limit=finalist_limit,
    )


def _evidence(
    spec: OrientationSpec,
    *,
    layer_area: float,
    support_volume: float,
    support_contact: float,
    height: float,
    islands: int = 0,
    suction: int = 0,
    traps: int = 0,
) -> SlicedFinalistEvidence:
    return SlicedFinalistEvidence(
        spec=spec,
        review_project_sha256=PROJECT_SHA,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        max_layer_area_mm2=layer_area,
        support_volume_mm3=support_volume,
        support_contact_area_mm2=support_contact,
        z_height_mm=height,
        unresolved_islands=islands,
        unresolved_suction_cups=suction,
        unresolved_resin_traps=traps,
    )


def _exact_evidence(screening):
    specs = [item.candidate.spec for item in screening.finalists]
    assert len(specs) == 2
    return (
        _evidence(
            specs[0],
            layer_area=220,
            support_volume=100,
            support_contact=40,
            height=24,
        ),
        _evidence(
            specs[1],
            layer_area=180,
            support_volume=70,
            support_contact=25,
            height=29,
        ),
    )


def test_sliced_validation_requires_exact_proxy_finalist_coverage():
    screening = _screening()
    evidence = _exact_evidence(screening)

    with pytest.raises(SlicedOrientationValidationError, match="cover every proxy finalist"):
        validate_sliced_finalists(screening, evidence[:1])

    extra = _evidence(
        OrientationSpec(45, 0, 0),
        layer_area=100,
        support_volume=50,
        support_contact=20,
        height=35,
    )
    with pytest.raises(SlicedOrientationValidationError, match="cover every proxy finalist"):
        validate_sliced_finalists(screening, (*evidence, extra))


def test_sliced_validation_rejects_duplicate_or_invalid_artifact_evidence():
    screening = _screening()
    evidence = _exact_evidence(screening)

    with pytest.raises(SlicedOrientationValidationError, match="Duplicate"):
        validate_sliced_finalists(screening, (evidence[0], evidence[0]))

    broken = SlicedFinalistEvidence(
        spec=evidence[0].spec,
        review_project_sha256="bad",
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        max_layer_area_mm2=100,
        support_volume_mm3=50,
        support_contact_area_mm2=20,
        z_height_mm=20,
    )
    with pytest.raises(SlicedOrientationValidationError, match="SHA-256"):
        validate_sliced_finalists(screening, (broken, evidence[1]))


def test_only_sliced_validation_metrics_reach_final_orientation_ranking():
    screening = _screening()
    validation = validate_sliced_finalists(screening, _exact_evidence(screening))

    assert validation.decision.require_sliced_validation is True
    assert validation.decision.selected is not None
    assert all(
        item.candidate.metrics.source == "sliced-validation"
        for item in validation.decision.ranked
    )


def test_critical_sliced_issues_hard_block_a_finalist():
    screening = _screening()
    evidence = list(_exact_evidence(screening))
    blocked = evidence[1]
    evidence[1] = _evidence(
        blocked.spec,
        layer_area=1,
        support_volume=1,
        support_contact=1,
        height=1,
        islands=2,
    )

    validation = validate_sliced_finalists(screening, evidence)
    assert validation.decision.selected is not None
    assert validation.decision.selected.candidate.canonical_key == evidence[0].canonical_key
    blocked_ranked = next(
        item for item in validation.decision.ranked
        if item.candidate.canonical_key == evidence[1].canonical_key
    )
    assert blocked_ranked.blocked_reasons == ("unresolved-islands",)


def test_all_blocked_sliced_finalists_require_manual_review():
    screening = _screening()
    specs = [item.candidate.spec for item in screening.finalists]
    evidence = (
        _evidence(
            specs[0], layer_area=10, support_volume=10, support_contact=10, height=10,
            suction=1,
        ),
        _evidence(
            specs[1], layer_area=10, support_volume=10, support_contact=10, height=10,
            traps=1,
        ),
    )

    validation = validate_sliced_finalists(screening, evidence)
    assert validation.decision.manual_review_required is True
    assert validation.selected_evidence is None


def test_sliced_manifest_binds_selected_orientation_to_exact_artifact_hashes():
    screening = _screening()
    validation = validate_sliced_finalists(screening, _exact_evidence(screening))
    manifest = sliced_orientation_manifest(validation)

    assert manifest["schema"] == "workpiece-resin-orientation-sliced-validation-v1"
    assert manifest["finalist_coverage"] == "exact"
    assert manifest["automatic_production_authority"] is False
    assert manifest["decision"]["require_sliced_validation"] is True
    assert manifest["selected_artifacts"] == {
        "review_3mf_sha256": PROJECT_SHA,
        "intermediate_sl1_sha256": INTERMEDIATE_SHA,
        "printer_native_sha256": NATIVE_SHA,
    }
    assert len(manifest["evidence"]) == 2
