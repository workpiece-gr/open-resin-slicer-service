from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.materialization import prepare_printer_plate_materialization
from app.materialization_selected import (
    SelectedPlateMaterializationSpec,
    SelectedPlateProjectMaterialization,
)
from app.materialized_plate_execution import SelectedMaterializedPlateNativeExecution
from app.materialized_plate_native_validation import (
    MaterializedPlateNativeValidationError,
    validate_whole_plate_native_envelope,
    whole_plate_native_envelope_manifest,
)
from app.materialized_plate_slice import MaterializedPlateNativeArtifact
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances
from app.profiles import Profile
from app.prusa_3mf_instances import DisplayInstancePlacement, Materialized3MFProject
from app.uvtools_metrics import NativeArtifactMetrics, NativeBoundingRectangle


SOURCE_SHA = "d" * 64
REVIEW_SHA = "a" * 64
CONFIG_SHA = "9" * 64
WINNER_INTERMEDIATE_SHA = "b" * 64
WINNER_NATIVE_SHA = "c" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _printer(tmp_path: Path, *, include_pixels: bool = True) -> Profile:
    config = tmp_path / "printer.ini"
    config.write_text("printer_technology = SLA\n", encoding="utf-8")
    metadata = {
        "native_format": "ctb",
        "display_width_mm": 80,
        "display_height_mm": 60,
    }
    if include_pixels:
        metadata.update({"display_pixels_x": 800, "display_pixels_y": 600})
    return Profile(
        id="printer-a",
        kind="printer",
        label="Printer A",
        candidate_ready=True,
        production_ready=False,
        config=config,
        metadata=metadata,
    )


def _materialization() -> SelectedPlateProjectMaterialization:
    plan = plan_rectangular_instances(
        footprint_width_mm=20,
        footprint_depth_mm=10,
        quantity=2,
        plate_width_mm=80,
        plate_depth_mm=60,
        spacing_mm=5,
        edge_margin_mm=5,
        allow_rotate_90=False,
    )
    plate_spec = prepare_printer_plate_materialization(
        PrinterPlatePlan(
            printer_profile_id="printer-a",
            manufacturing_envelope_coordinate_mapping="validated",
            plan=plan,
        ),
        plate_index=1,
        pretranslation_envelope=Envelope2D(10, 30, 20, 30),
        require_validated_mapping=True,
    )
    project_bytes = b"exact materialized plate"
    project = Materialized3MFProject(
        bytes=project_bytes,
        sha256=_sha(project_bytes),
        instance_count=2,
        instance_indices=(1, 2),
        display_transforms=(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 20.0, 20.0, 0.0),
            (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 50.0, 20.0, 0.0),
        ),
    )
    spec = SelectedPlateMaterializationSpec(
        source_sha256=SOURCE_SHA,
        selected_orientation_deg=(15.0, 0.0, 0.0),
        selected_review_3mf_sha256=REVIEW_SHA,
        selected_effective_config_sha256=CONFIG_SHA,
        selected_intermediate_sl1_sha256=WINNER_INTERMEDIATE_SHA,
        selected_printer_native_sha256=WINNER_NATIVE_SHA,
        plate_spec=plate_spec,
    )
    return SelectedPlateProjectMaterialization(
        spec=spec,
        source_display_envelope=Envelope2D(10, 30, 20, 30),
        display_placements=(
            DisplayInstancePlacement(
                instance_index=1,
                target_display_x_mm=20,
                target_display_y_mm=20,
                rotation_z_deg=0,
            ),
            DisplayInstancePlacement(
                instance_index=2,
                target_display_x_mm=50,
                target_display_y_mm=20,
                rotation_z_deg=90,
            ),
        ),
        project=project,
    )


def _execution(
    materialization: SelectedPlateProjectMaterialization,
    *,
    rectangle: NativeBoundingRectangle,
) -> SelectedMaterializedPlateNativeExecution:
    native = b"exact final plate ctb"
    artifact = MaterializedPlateNativeArtifact(
        project_bytes=materialization.project.bytes,
        project_sha256=materialization.project.sha256,
        effective_config_bytes=b"exact config",
        effective_config_sha256=_sha(b"exact config"),
        intermediate_bytes=b"plate sl1",
        intermediate_sha256=_sha(b"plate sl1"),
        native_bytes=native,
        native_sha256=_sha(native),
        intermediate_filename="plate.sl1",
        native_filename="plate.ctb",
        issue_summary={
            "islands": 0,
            "overhangs": 0,
            "resin_traps": 0,
            "suction_cups": 0,
            "touching_bounds": 0,
            "empty_layers": 0,
        },
        issue_text="Issues: 0\n",
        native_metrics=NativeArtifactMetrics(
            layer_count=2,
            max_layer_area_mm2=100,
            material_volume_mm3=1,
            footprint_area_mm2=rectangle.area_mm2,
            z_height_mm=0.1,
            bounding_rectangle=rectangle,
        ),
        printer_profile_id="printer-a",
    )
    return SelectedMaterializedPlateNativeExecution(
        source_sha256=SOURCE_SHA,
        selected_orientation_deg=(15.0, 0.0, 0.0),
        selected_review_3mf_sha256=REVIEW_SHA,
        selected_effective_config_sha256=CONFIG_SHA,
        selected_intermediate_sl1_sha256=WINNER_INTERMEDIATE_SHA,
        selected_printer_native_sha256=WINNER_NATIVE_SHA,
        plate_index=1,
        materialized_project_sha256=materialization.project.sha256,
        artifact=artifact,
    )


def test_whole_plate_native_bounds_match_derived_selected_display_placements(tmp_path):
    materialization = _materialization()
    execution = _execution(
        materialization,
        rectangle=NativeBoundingRectangle(
            x_mm=10,
            y_mm=10,
            width_mm=45,
            height_mm=20,
        ),
    )
    evidence = validate_whole_plate_native_envelope(
        materialization,
        execution,
        printer=_printer(tmp_path),
    )

    expected = evidence.expected_display_envelope
    observed = evidence.observed_display_envelope
    assert (expected.min_x_mm, expected.max_x_mm, expected.min_y_mm, expected.max_y_mm) == (10, 55, 10, 30)
    assert observed == expected
    assert evidence.tolerance_x_mm == 0.2
    assert evidence.tolerance_y_mm == 0.2
    manifest = whole_plate_native_envelope_manifest(evidence)
    assert manifest["whole_plate_native_validation_passed"] is True
    assert manifest["per_instance_materialized_project_validation_satisfied"] is False


def test_whole_plate_native_bounds_allow_two_pixel_raster_tolerance(tmp_path):
    materialization = _materialization()
    execution = _execution(
        materialization,
        rectangle=NativeBoundingRectangle(
            x_mm=10.2,
            y_mm=9.8,
            width_mm=44.7,
            height_mm=20.4,
        ),
    )
    evidence = validate_whole_plate_native_envelope(
        materialization,
        execution,
        printer=_printer(tmp_path),
    )
    assert evidence.observed_display_envelope.min_x_mm == 10.2


def test_whole_plate_native_bounds_reject_material_placement_drift(tmp_path):
    materialization = _materialization()
    execution = _execution(
        materialization,
        rectangle=NativeBoundingRectangle(
            x_mm=11,
            y_mm=10,
            width_mm=44,
            height_mm=20,
        ),
    )
    with pytest.raises(MaterializedPlateNativeValidationError, match="does not match"):
        validate_whole_plate_native_envelope(
            materialization,
            execution,
            printer=_printer(tmp_path),
        )


def test_whole_plate_native_bounds_require_physical_display_resolution(tmp_path):
    materialization = _materialization()
    execution = _execution(
        materialization,
        rectangle=NativeBoundingRectangle(
            x_mm=10,
            y_mm=10,
            width_mm=45,
            height_mm=20,
        ),
    )
    with pytest.raises(MaterializedPlateNativeValidationError, match="display_pixels_x"):
        validate_whole_plate_native_envelope(
            materialization,
            execution,
            printer=_printer(tmp_path, include_pixels=False),
        )


def test_whole_plate_native_bounds_reject_wrong_materialized_project_receipt(tmp_path):
    materialization = _materialization()
    execution = _execution(
        materialization,
        rectangle=NativeBoundingRectangle(
            x_mm=10,
            y_mm=10,
            width_mm=45,
            height_mm=20,
        ),
    )
    execution = SelectedMaterializedPlateNativeExecution(
        source_sha256=execution.source_sha256,
        selected_orientation_deg=execution.selected_orientation_deg,
        selected_review_3mf_sha256=execution.selected_review_3mf_sha256,
        selected_effective_config_sha256=execution.selected_effective_config_sha256,
        selected_intermediate_sl1_sha256=execution.selected_intermediate_sl1_sha256,
        selected_printer_native_sha256=execution.selected_printer_native_sha256,
        plate_index=execution.plate_index,
        materialized_project_sha256="0" * 64,
        artifact=execution.artifact,
    )
    with pytest.raises(MaterializedPlateNativeValidationError, match="not bound"):
        validate_whole_plate_native_envelope(
            materialization,
            execution,
            printer=_printer(tmp_path),
        )
