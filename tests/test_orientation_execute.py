import hashlib

import pytest

from app.engine import NativeArtifact, Orientation
from app.orientation_candidates import OrientationSpec
from app.orientation_execute import (
    FinalistSliceResult,
    SlicedFinalistExecutionError,
    SlicedMeasurements,
    execute_sliced_finalists,
)
from app.orientation_pipeline import ProxyOrientationPlan
from app.orientation_proxy import GeometryProxyMetrics
from app.orientation_screen import ProxyCandidate, screen_geometry_proxies


SOURCE = b"exact source stl bytes"
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()


def _proxy_metrics(area: float, moment: float, height: float) -> GeometryProxyMetrics:
    return GeometryProxyMetrics(
        triangle_count=12,
        sampled_layer_count=10,
        full_layer_count=10,
        layer_sampling_stride=1,
        max_sampled_layer_area_mm2=area,
        z_height_mm=height,
        xy_width_mm=20,
        xy_depth_mm=20,
        downward_projected_area_mm2=10,
        downward_support_moment_mm3=moment,
        open_contour_sample_count=0,
    )


def _plan() -> ProxyOrientationPlan:
    candidates = (
        ProxyCandidate(OrientationSpec(0, 0, 0), _proxy_metrics(300, 900, 20)),
        ProxyCandidate(OrientationSpec(15, 0, 0), _proxy_metrics(220, 700, 24)),
    )
    return ProxyOrientationPlan(
        source_sha256=SOURCE_SHA,
        triangle_count=12,
        candidates=candidates,
        screening=screen_geometry_proxies(candidates, finalist_limit=2),
    )


def _result(
    spec: OrientationSpec,
    *,
    islands: int = 0,
    source_sha: str = SOURCE_SHA,
    orientation_override: Orientation | None = None,
    measurement_project_sha: str | None = None,
    printer_profile: str = "elegoo-mars-2",
) -> FinalistSliceResult:
    token = "a" if spec.canonical_key[0] == 0 else "b"
    project_sha = token * 64
    intermediate_sha = ("c" if token == "a" else "d") * 64
    native_sha = ("e" if token == "a" else "f") * 64
    artifact = NativeArtifact(
        project_bytes=b"3mf-" + token.encode(),
        project_filename=f"{token}.3mf",
        project_sha256=project_sha,
        intermediate_bytes=b"sl1-" + token.encode(),
        intermediate_filename=f"{token}.sl1",
        bytes=b"ctb-" + token.encode(),
        filename=f"{token}.ctb",
        media_type="application/octet-stream",
        source_sha256=source_sha,
        intermediate_sha256=intermediate_sha,
        native_sha256=native_sha,
        issue_summary={"islands": islands, "suction_cups": 0, "resin_traps": 0},
        issue_text="",
        printer_profile=printer_profile,
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        orientation=orientation_override or Orientation(spec.x_deg, spec.y_deg, spec.z_deg),
    )
    measurements = SlicedMeasurements(
        source_sha256=source_sha,
        review_project_sha256=measurement_project_sha or project_sha,
        intermediate_sha256=intermediate_sha,
        native_sha256=native_sha,
        max_layer_area_mm2=180 if token == "b" else 220,
        material_volume_mm3=1800 if token == "b" else 2100,
        footprint_area_mm2=420 if token == "b" else 500,
        z_height_mm=29 if token == "b" else 24,
    )
    return FinalistSliceResult(artifact=artifact, measurements=measurements)


def _execute(plan: ProxyOrientationPlan, callback):
    return execute_sliced_finalists(
        proxy_plan=plan,
        source_stl=SOURCE,
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        execute_finalist=callback,
    )


def test_executes_exact_proxy_finalists_and_builds_native_metric_validation():
    plan = _plan()
    execution = _execute(plan, lambda spec: _result(spec))
    expected = [item.candidate.spec.canonical_key for item in plan.screening.finalists]
    actual = [item.artifact.orientation for item in execution.results]
    assert [(item.x % 360, item.y % 360, item.z % 360) for item in actual] == expected
    assert execution.validation.source_sha256 == SOURCE_SHA
    assert execution.validation.selected_evidence is not None
    assert execution.validation.selected_evidence.metrics.source == "sliced-validation"
    assert execution.validation.selected_evidence.material_volume_mm3 > 0
    assert execution.validation.selected_evidence.footprint_area_mm2 > 0


def test_uvtools_critical_issue_blocks_only_the_affected_finalist():
    plan = _plan()
    blocked_key = plan.screening.finalists[0].candidate.spec.canonical_key

    def callback(spec: OrientationSpec):
        return _result(spec, islands=1 if spec.canonical_key == blocked_key else 0)

    execution = _execute(plan, callback)
    assert execution.validation.selected_evidence is not None
    assert execution.validation.selected_evidence.canonical_key != blocked_key


def test_rejects_source_that_does_not_match_proxy_plan():
    plan = _plan()
    with pytest.raises(SlicedFinalistExecutionError, match="source-bound proxy"):
        execute_sliced_finalists(
            proxy_plan=plan,
            source_stl=b"different source",
            printer_profile="elegoo-mars-2",
            resin_profile="elegoo-water-washable-grey",
            quality_profile="balanced-0p05-medium",
            execute_finalist=lambda spec: _result(spec),
        )


def test_rejects_slicer_orientation_or_profile_mismatch():
    plan = _plan()
    first_key = plan.screening.finalists[0].candidate.spec.canonical_key

    def bad_orientation(spec: OrientationSpec):
        if spec.canonical_key == first_key:
            return _result(spec, orientation_override=Orientation(45, 0, 0))
        return _result(spec)

    with pytest.raises(SlicedFinalistExecutionError, match="orientation"):
        _execute(plan, bad_orientation)
    with pytest.raises(SlicedFinalistExecutionError, match="printer profile"):
        _execute(plan, lambda spec: _result(spec, printer_profile="wrong-printer"))


def test_rejects_measurements_not_bound_to_exact_artifact_hashes():
    plan = _plan()
    with pytest.raises(SlicedFinalistExecutionError, match="review 3MF"):
        _execute(plan, lambda spec: _result(spec, measurement_project_sha="9" * 64))
