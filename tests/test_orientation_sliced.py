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


SOURCE_SHA = "d" * 64
PROJECT_SHA = "a" * 64
CONFIG_SHA = "9" * 64
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
            ProxyCandidate(OrientationSpec(0, 0, 0), _proxy_metrics(area=300, moment=1000, height=20)),
            ProxyCandidate(OrientationSpec(15, 0, 0), _proxy_metrics(area=220, moment=800, height=23)),
            ProxyCandidate(OrientationSpec(30, 0, 0), _proxy_metrics(area=180, moment=700, height=28)),
        ),
        finalist_limit=finalist_limit,
    )


def _evidence(
    spec: OrientationSpec,
    *,
    layer_area: float,
    material_volume: float,
    footprint_area: float,
    height: float,
    source_sha: str = SOURCE_SHA,
    config_sha: str = CONFIG_SHA,
    islands: int = 0,
    suction: int = 0,
    traps: int = 0,
    touching_bounds: int = 0,
    empty_layers: int = 0,
) -> SlicedFinalistEvidence:
    return SlicedFinalistEvidence(
        spec=spec,
        source_sha256=source_sha,
        review_project_sha256=PROJECT_SHA,
        effective_config_sha256=config_sha,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        max_layer_area_mm2=layer_area,
        material_volume_mm3=material_volume,
        footprint_area_mm2=footprint_area,
        z_height_mm=height,
        unresolved_islands=islands,
        unresolved_suction_cups=suction,
        unresolved_resin_traps=traps,
        unresolved_touching_bounds=touching_bounds,
        unresolved_empty_layers=empty_layers,
    )


def _exact_evidence(screening):
    specs = [item.candidate.spec for item in screening.finalists]
    assert len(specs) == 2
    return (
        _evidence(specs[0], layer_area=220, material_volume=2100, footprint_area=500, height=24),
        _evidence(specs[1], layer_area=180, material_volume=1800, footprint_area=420, height=29),
    )


def test_sliced_validation_requires_exact_proxy_finalist_coverage():
    screening = _screening()
    evidence = _exact_evidence(screening)
    with pytest.raises(SlicedOrientationValidationError, match="cover every proxy finalist"):
        validate_sliced_finalists(screening, evidence[:1])
    extra = _evidence(
        OrientationSpec(45, 0, 0), layer_area=100, material_volume=1500, footprint_area=350, height=35
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
        source_sha256=SOURCE_SHA,
        review_project_sha256="bad",
        effective_config_sha256=CONFIG_SHA,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        max_layer_area_mm2=100,
        material_volume_mm3=1500,
        footprint_area_mm2=300,
        z_height_mm=20,
    )
    with pytest.raises(SlicedOrientationValidationError, match="SHA-256"):
        validate_sliced_finalists(screening, (broken, evidence[1]))


def test_sliced_validation_rejects_invalid_effective_config_hash():
    screening = _screening()
    evidence = list(_exact_evidence(screening))
    first = evidence[0]
    evidence[0] = _evidence(
        first.spec,
        layer_area=first.max_layer_area_mm2,
        material_volume=first.material_volume_mm3,
        footprint_area=first.footprint_area_mm2,
        height=first.z_height_mm,
        config_sha="bad",
    )
    with pytest.raises(SlicedOrientationValidationError, match="effective_config_sha256"):
        validate_sliced_finalists(screening, evidence)


def test_sliced_validation_rejects_mixed_source_stls():
    screening = _screening()
    evidence = list(_exact_evidence(screening))
    second = evidence[1]
    evidence[1] = _evidence(
        second.spec,
        layer_area=second.max_layer_area_mm2,
        material_volume=second.material_volume_mm3,
        footprint_area=second.footprint_area_mm2,
        height=second.z_height_mm,
        source_sha="e" * 64,
    )
    with pytest.raises(SlicedOrientationValidationError, match="same exact source STL"):
        validate_sliced_finalists(screening, evidence)


def test_only_sliced_validation_metrics_reach_final_orientation_ranking():
    screening = _screening()
    validation = validate_sliced_finalists(screening, _exact_evidence(screening))
    assert validation.source_sha256 == SOURCE_SHA
    assert validation.decision.require_sliced_validation is True
    assert validation.decision.selected is not None
    assert all(item.candidate.metrics.source == "sliced-validation" for item in validation.decision.ranked)


def test_critical_sliced_issues_hard_block_a_finalist():
    screening = _screening()
    evidence = list(_exact_evidence(screening))
    blocked = evidence[1]
    evidence[1] = _evidence(
        blocked.spec,
        layer_area=1,
        material_volume=1,
        footprint_area=1,
        height=1,
        islands=2,
    )
    validation = validate_sliced_finalists(screening, evidence)
    assert validation.decision.selected is not None
    assert validation.decision.selected.candidate.canonical_key == evidence[0].canonical_key
    blocked_ranked = next(
        item for item in validation.decision.ranked if item.candidate.canonical_key == evidence[1].canonical_key
    )
    assert blocked_ranked.blocked_reasons == ("unresolved-islands",)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (({"touching_bounds": 1}, "touching-bounds"), ({"empty_layers": 1}, "empty-layers")),
)
def test_remaining_engine_critical_issues_are_hard_blockers(kwargs, reason):
    screening = _screening()
    evidence = list(_exact_evidence(screening))
    blocked = evidence[1]
    evidence[1] = _evidence(
        blocked.spec,
        layer_area=1,
        material_volume=1,
        footprint_area=1,
        height=1,
        **kwargs,
    )
    validation = validate_sliced_finalists(screening, evidence)
    ranked = next(item for item in validation.decision.ranked if item.candidate.canonical_key == blocked.canonical_key)
    assert reason in ranked.blocked_reasons


def test_all_blocked_sliced_finalists_require_manual_review():
    screening = _screening()
    specs = [item.candidate.spec for item in screening.finalists]
    evidence = (
        _evidence(specs[0], layer_area=10, material_volume=10, footprint_area=10, height=10, suction=1),
        _evidence(specs[1], layer_area=10, material_volume=10, footprint_area=10, height=10, traps=1),
    )
    validation = validate_sliced_finalists(screening, evidence)
    assert validation.decision.manual_review_required is True
    assert validation.selected_evidence is None


def test_sliced_manifest_binds_recipe_native_metrics_and_exact_artifact_hashes():
    screening = _screening()
    validation = validate_sliced_finalists(screening, _exact_evidence(screening))
    manifest = sliced_orientation_manifest(validation)
    assert manifest["schema"] == "workpiece-resin-orientation-sliced-validation-v2"
    assert manifest["source_sha256"] == SOURCE_SHA
    assert manifest["finalist_coverage"] == "exact"
    assert manifest["metric_authority"] == "exact-retained-printer-native-artifact"
    assert manifest["recipe_authority"] == "exact-review-3mf-plus-effective-config"
    assert manifest["automatic_production_authority"] is False
    assert manifest["decision"]["require_sliced_validation"] is True
    assert manifest["selected_artifacts"] == {
        "review_3mf_sha256": PROJECT_SHA,
        "effective_config_sha256": CONFIG_SHA,
        "intermediate_sl1_sha256": INTERMEDIATE_SHA,
        "printer_native_sha256": NATIVE_SHA,
    }
    assert set(manifest["evidence"][0]["native_metrics"]) >= {
        "max_layer_area_mm2",
        "material_volume_mm3",
        "footprint_area_mm2",
        "z_height_mm",
    }
    assert len(manifest["evidence"]) == 2
