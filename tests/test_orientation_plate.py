from pathlib import Path

import pytest

from app.native_envelope import native_envelope_from_rectangle
from app.orientation_candidates import OrientationSpec
from app.orientation_plate import (
    OrientationPlatePlanError,
    orientation_plate_plan_manifest,
    plan_selected_sliced_orientation,
)
from app.orientation_proxy import GeometryProxyMetrics
from app.orientation_screen import ProxyCandidate, screen_geometry_proxies
from app.orientation_sliced import SlicedFinalistEvidence, validate_sliced_finalists
from app.profiles import ProfileRegistry
from app.uvtools_metrics import NativeBoundingRectangle


SOURCE_SHA = "d" * 64
PROJECT_SHA = "a" * 64
CONFIG_SHA = "9" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


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


def _sliced_validation(*, block_all: bool = False):
    screening = screen_geometry_proxies(
        (
            ProxyCandidate(OrientationSpec(0, 0), _proxy_metrics(300, 900, 20)),
            ProxyCandidate(OrientationSpec(15, 0), _proxy_metrics(220, 700, 24)),
        ),
        finalist_limit=2,
    )
    specs = [item.candidate.spec for item in screening.finalists]
    evidence = (
        SlicedFinalistEvidence(
            spec=specs[0],
            source_sha256=SOURCE_SHA,
            review_project_sha256=PROJECT_SHA,
            effective_config_sha256=CONFIG_SHA,
            intermediate_sha256=INTERMEDIATE_SHA,
            native_sha256=NATIVE_SHA,
            max_layer_area_mm2=220,
            material_volume_mm3=2100,
            footprint_area_mm2=500,
            z_height_mm=24,
            unresolved_islands=1 if block_all else 0,
        ),
        SlicedFinalistEvidence(
            spec=specs[1],
            source_sha256=SOURCE_SHA,
            review_project_sha256=PROJECT_SHA,
            effective_config_sha256=CONFIG_SHA,
            intermediate_sha256=INTERMEDIATE_SHA,
            native_sha256=NATIVE_SHA,
            max_layer_area_mm2=180,
            material_volume_mm3=1800,
            footprint_area_mm2=420,
            z_height_mm=29,
            unresolved_resin_traps=1 if block_all else 0,
        ),
    )
    return validate_sliced_finalists(screening, evidence)


def _registry() -> ProfileRegistry:
    return ProfileRegistry(Path(__file__).parents[1] / "profiles")


def _native_envelope(*, native_sha: str = NATIVE_SHA, printer: str = "elegoo-mars-2"):
    return native_envelope_from_rectangle(
        printer_profile_id=printer,
        printer_native_sha256=native_sha,
        rectangle=NativeBoundingRectangle(
            x_mm=10,
            y_mm=20,
            width_mm=30,
            height_mm=30,
        ),
    )


def test_selected_ctb_native_envelope_drives_profile_backed_plate_plan_dimensions():
    validation = _sliced_validation()
    result = plan_selected_sliced_orientation(
        registry=_registry(),
        printer_profile_id="elegoo-mars-2",
        sliced_validation=validation,
        native_envelope=_native_envelope(),
        quantity=3,
    )
    assert result.source_sha256 == SOURCE_SHA
    assert result.review_project_sha256 == PROJECT_SHA
    assert result.effective_config_sha256 == CONFIG_SHA
    assert result.intermediate_sha256 == INTERMEDIATE_SHA
    assert result.native_sha256 == NATIVE_SHA
    assert result.pretranslation_envelope.min_x_mm == 10
    assert result.pretranslation_envelope.min_y_mm == 20
    assert result.pretranslation_envelope.width_mm == 30
    assert result.pretranslation_envelope.depth_mm == 30
    assert result.pretranslation_coordinate_space == "uvtools-native-display-millimetres"
    assert result.printer_plate_plan.plan.instance_footprint_width_mm == 30
    assert result.printer_plate_plan.plan.instance_footprint_depth_mm == 30
    assert result.printer_plate_plan.printer_profile_id == "elegoo-mars-2"


def test_plate_plan_rejects_native_envelope_not_bound_to_selected_ctb():
    with pytest.raises(OrientationPlatePlanError, match="exact selected printer-native"):
        plan_selected_sliced_orientation(
            registry=_registry(),
            printer_profile_id="elegoo-mars-2",
            sliced_validation=_sliced_validation(),
            native_envelope=_native_envelope(native_sha="f" * 64),
            quantity=1,
        )


def test_plate_plan_rejects_native_envelope_from_different_printer_profile():
    with pytest.raises(OrientationPlatePlanError, match="printer profile"):
        plan_selected_sliced_orientation(
            registry=_registry(),
            printer_profile_id="elegoo-mars-2",
            sliced_validation=_sliced_validation(),
            native_envelope=_native_envelope(printer="different-printer"),
            quantity=1,
        )


def test_plate_plan_rejects_manual_review_only_orientation_result():
    with pytest.raises(OrientationPlatePlanError, match="no selected finalist"):
        plan_selected_sliced_orientation(
            registry=_registry(),
            printer_profile_id="elegoo-mars-2",
            sliced_validation=_sliced_validation(block_all=True),
            native_envelope=_native_envelope(),
            quantity=1,
        )


def test_manifest_binds_native_envelope_and_keeps_mars2_non_authoritative():
    result = plan_selected_sliced_orientation(
        registry=_registry(),
        printer_profile_id="elegoo-mars-2",
        sliced_validation=_sliced_validation(),
        native_envelope=_native_envelope(),
        quantity=3,
    )
    manifest = orientation_plate_plan_manifest(result)
    assert manifest["schema"] == "workpiece-resin-orientation-plate-plan-v2"
    assert manifest["source_sha256"] == SOURCE_SHA
    assert manifest["selected_review_3mf_sha256"] == PROJECT_SHA
    assert manifest["selected_sliced_artifacts"] == {
        "review_3mf_sha256": PROJECT_SHA,
        "effective_config_sha256": CONFIG_SHA,
        "intermediate_sl1_sha256": INTERMEDIATE_SHA,
        "printer_native_sha256": NATIVE_SHA,
    }
    envelope = manifest["supported_pretranslation_envelope_mm"]
    assert envelope["width"] == 30
    assert envelope["depth"] == 30
    assert envelope["coordinate_space"] == "uvtools-native-display-millimetres"
    assert envelope["source"] == "exact-selected-printer-native-bounding-rectangle"
    assert manifest["plate_plan"]["printer_profile_id"] == "elegoo-mars-2"
    assert manifest["plate_plan"]["manufacturing_envelope_coordinate_mapping"] == "unverified"
    assert manifest["automatic_materialization_authority"] is False
