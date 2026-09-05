from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace

import pytest

import app.order_selected as order_selected
from app.coordinate_mapping import ManufacturingDisplayTransform
from app.materialization_selected import materialize_selected_plate_project
from app.materialized_3mf_instance_evidence import (
    finalize_selected_materialized_plate_from_verified_build_items,
)
from app.materialized_plate_execution import SelectedMaterializedPlateNativeExecution
from app.materialized_plate_native_validation import validate_whole_plate_native_envelope
from app.materialized_plate_slice import MaterializedPlateNativeArtifact
from app.orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    SelectedOrientationPlatePlan,
)
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances
from app.plate_authority import (
    PlateAuthorityError,
    selected_plate_authority_manifest,
    validate_selected_plate_authority,
)
from app.profiles import ProfileRegistry
from app.uvtools_metrics import NativeArtifactMetrics, NativeBoundingRectangle


SOURCE_SHA = "d" * 64
SELECTED_INTERMEDIATE_SHA = "b" * 64
SELECTED_NATIVE_SHA = "c" * 64
CONFIG_BYTES = b"exact selected effective config"
MODEL_MEMBER = "3D/3dmodel.model"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_project() -> bytes:
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <resources>\n'
        '  <object id="1" type="model"><mesh><vertices/><triangles/></mesh></object>\n'
        ' </resources>\n'
        ' <build>\n'
        '  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 20 0" printable="1"/>\n'
        ' </build>\n'
        '</model>\n'
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MODEL_MEMBER, model)
        archive.writestr(
            "Metadata/Slic3r_PE_sla_support_points.txt",
            b"exact-selected-support-metadata",
        )
        archive.writestr("Metadata/Slic3r_PE.config", b"exact-selected-project-config")
    return output.getvalue()


def _mapping() -> ManufacturingDisplayTransform:
    return ManufacturingDisplayTransform(
        origin_display_x_mm=0,
        origin_display_y_mm=60,
        x_axis_display_x=1,
        x_axis_display_y=0,
        y_axis_display_x=0,
        y_axis_display_y=-1,
    )


def _registry(tmp_path, *, production_ready: bool) -> ProfileRegistry:
    printers = tmp_path / "printers"
    printers.mkdir()
    (printers / "printer-a.ini").write_text(
        "printer_technology = SLA\n",
        encoding="utf-8",
    )
    (printers / "printer-a.json").write_text(
        json.dumps(
            {
                "id": "printer-a",
                "candidate_ready": True,
                "production_ready": production_ready,
                "config": "printers/printer-a.ini",
                "display_width_mm": 80,
                "display_height_mm": 60,
                "display_pixels_x": 1600,
                "display_pixels_y": 1200,
                "manufacturing_envelope_width_mm": 80,
                "manufacturing_envelope_depth_mm": 60,
                "manufacturing_envelope_coordinate_mapping": "validated",
                "manufacturing_to_display_transform": _mapping().manifest(),
                "native_format": "ctb",
                "uvtools_target": "ctb",
            }
        ),
        encoding="utf-8",
    )
    return ProfileRegistry(tmp_path)


def _selected(source: bytes) -> SelectedOrientationPlatePlan:
    plan = plan_rectangular_instances(
        footprint_width_mm=45,
        footprint_depth_mm=25,
        quantity=2,
        plate_width_mm=80,
        plate_depth_mm=60,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    assert plan.rotation_z_deg == 90
    return SelectedOrientationPlatePlan(
        orientation_deg=(15.0, 0.0, 0.0),
        source_sha256=SOURCE_SHA,
        review_project_sha256=_sha(source),
        effective_config_sha256=_sha(CONFIG_BYTES),
        intermediate_sha256=SELECTED_INTERMEDIATE_SHA,
        native_sha256=SELECTED_NATIVE_SHA,
        pretranslation_envelope=Envelope2D(10, 55, 15, 40),
        printer_plate_plan=PrinterPlatePlan(
            printer_profile_id="printer-a",
            manufacturing_envelope_coordinate_mapping="validated",
            plan=plan,
        ),
        pretranslation_coordinate_space=MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
        native_display_envelope=Envelope2D(10, 55, 20, 45),
        manufacturing_to_display_transform=_mapping(),
    )


def _issue_summary() -> dict[str, int]:
    return {
        "islands": 0,
        "overhangs": 0,
        "resin_traps": 0,
        "suction_cups": 0,
        "touching_bounds": 0,
        "empty_layers": 0,
    }


def _authority_inputs(tmp_path, *, production_ready: bool = True):
    source = _source_project()
    registry = _registry(tmp_path, production_ready=production_ready)
    printer = registry.get("printer", "printer-a")
    selected = _selected(source)
    materialization = materialize_selected_plate_project(
        selected,
        registry=registry,
        plate_index=1,
        selected_review_project_bytes=source,
    )
    instance_evidence = finalize_selected_materialized_plate_from_verified_build_items(
        selected,
        materialization,
        selected_review_project_bytes=source,
    )

    intermediate = b"exact final plate sl1"
    native = b"exact final plate ctb"
    metrics = NativeArtifactMetrics(
        layer_count=2,
        max_layer_area_mm2=900,
        material_volume_mm3=20,
        footprint_area_mm2=2475,
        z_height_mm=0.1,
        bounding_rectangle=NativeBoundingRectangle(
            x_mm=5,
            y_mm=10,
            width_mm=55,
            height_mm=45,
        ),
    )
    artifact = MaterializedPlateNativeArtifact(
        project_bytes=materialization.project.bytes,
        project_sha256=materialization.project.sha256,
        effective_config_bytes=CONFIG_BYTES,
        effective_config_sha256=_sha(CONFIG_BYTES),
        intermediate_bytes=intermediate,
        intermediate_sha256=_sha(intermediate),
        native_bytes=native,
        native_sha256=_sha(native),
        intermediate_filename="plate.sl1",
        native_filename="plate.ctb",
        issue_summary=_issue_summary(),
        issue_text="Issues: 0\n",
        native_metrics=metrics,
        printer_profile_id=printer.id,
    )
    spec = materialization.spec
    execution = SelectedMaterializedPlateNativeExecution(
        source_sha256=spec.source_sha256,
        selected_orientation_deg=spec.selected_orientation_deg,
        selected_review_3mf_sha256=spec.selected_review_3mf_sha256,
        selected_effective_config_sha256=spec.selected_effective_config_sha256,
        selected_intermediate_sl1_sha256=spec.selected_intermediate_sl1_sha256,
        selected_printer_native_sha256=spec.selected_printer_native_sha256,
        plate_index=spec.plate_spec.plate_index,
        materialized_project_sha256=materialization.project.sha256,
        artifact=artifact,
    )
    whole_plate = validate_whole_plate_native_envelope(
        materialization,
        execution,
        printer=printer,
    )
    return printer, instance_evidence, execution, whole_plate


def test_complete_evidence_chain_grants_plate_authority_without_enabling_production(tmp_path):
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    authority = validate_selected_plate_authority(
        instance_evidence,
        execution,
        whole_plate,
        printer=printer,
    )

    assert authority.printer_profile_id == "printer-a"
    assert authority.plate_index == 1
    assert authority.materialized_project_sha256 == execution.materialized_project_sha256
    assert authority.plate_printer_native_sha256 == execution.artifact.native_sha256
    manifest = selected_plate_authority_manifest(authority)
    assert manifest["schema"] == "workpiece-resin-plate-authority-v1"
    assert manifest["production_plate_authority_ready"] is True
    assert manifest["production_enablement_performed"] is False
    assert manifest["instance_evidence"]["per_instance_materialized_project_validation_satisfied"] is True
    assert manifest["whole_plate_native_evidence"]["whole_plate_native_validation_passed"] is True


def test_plate_authority_rejects_printer_not_explicitly_production_ready(tmp_path):
    printer, instance_evidence, execution, whole_plate = _authority_inputs(
        tmp_path,
        production_ready=False,
    )
    with pytest.raises(PlateAuthorityError, match="not production-ready"):
        validate_selected_plate_authority(
            instance_evidence,
            execution,
            whole_plate,
            printer=printer,
        )


def test_plate_authority_rejects_critical_native_issue(tmp_path):
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    issues = dict(execution.artifact.issue_summary)
    issues["islands"] = 1
    changed_execution = replace(
        execution,
        artifact=replace(execution.artifact, issue_summary=issues),
    )
    with pytest.raises(PlateAuthorityError, match="critical UVtools"):
        validate_selected_plate_authority(
            instance_evidence,
            changed_execution,
            whole_plate,
            printer=printer,
        )


def test_plate_authority_rejects_native_envelope_proof_from_other_file(tmp_path):
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    changed = replace(whole_plate, printer_native_sha256="f" * 64)
    with pytest.raises(PlateAuthorityError, match="not bound to the exact final"):
        validate_selected_plate_authority(
            instance_evidence,
            execution,
            changed,
            printer=printer,
        )


def test_plate_authority_rejects_incomplete_issue_receipt(tmp_path):
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    issues = dict(execution.artifact.issue_summary)
    issues.pop("empty_layers")
    changed_execution = replace(
        execution,
        artifact=replace(execution.artifact, issue_summary=issues),
    )
    with pytest.raises(PlateAuthorityError, match="exact pinned UVtools"):
        validate_selected_plate_authority(
            instance_evidence,
            changed_execution,
            whole_plate,
            printer=printer,
        )


def test_production_selected_order_embeds_exact_complete_plate_authority(tmp_path):
    source = _source_project()
    selected = _selected(source)
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    authority = validate_selected_plate_authority(
        instance_evidence,
        execution,
        whole_plate,
        printer=printer,
    )
    record = order_selected.SelectedPlateArtifactRecord(
        plate_index=1,
        project_filename="plate-1.3mf",
        intermediate_filename="plate-1.sl1",
        intermediate_sha256=execution.artifact.intermediate_sha256,
        native_filename="plate-1.ctb",
        native_sha256=execution.artifact.native_sha256,
        issue_summary=execution.artifact.issue_summary,
        materialization=instance_evidence.materialized_plate,
        authority_evidence=authority,
    )

    manifest = order_selected.build_selected_orientation_order_manifest(
        source_filename="part.stl",
        source_sha256=SOURCE_SHA,
        requested_quantity=2,
        printer_profile="printer-a",
        resin_profile="resin-a",
        quality_profile="quality-a",
        selected_orientation_plan=selected,
        plate_artifacts=(record,),
        prusaslicer_version="2.9.6",
        prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
        uvtools_version="6.2.0",
        authority="production-authoritative",
    )

    assert manifest["schema"] == "workpiece-resin-order-manifest-v4"
    assert manifest["authority"] == "production-authoritative"
    assert manifest["production_enablement_performed"] is False
    assert manifest["plates"][0]["files"]["review_3mf"]["sha256"] == authority.materialized_project_sha256
    assert manifest["plates"][0]["files"]["intermediate_sl1"]["sha256"] == authority.plate_intermediate_sha256
    assert manifest["plates"][0]["files"]["printer_native"]["sha256"] == authority.plate_printer_native_sha256
    assert manifest["plates"][0]["plate_authority"]["production_plate_authority_ready"] is True
    assert manifest["plates"][0]["plate_authority"]["production_enablement_performed"] is False


def test_production_selected_order_rejects_authority_bound_to_different_final_native(tmp_path):
    source = _source_project()
    selected = _selected(source)
    printer, instance_evidence, execution, whole_plate = _authority_inputs(tmp_path)
    authority = validate_selected_plate_authority(
        instance_evidence,
        execution,
        whole_plate,
        printer=printer,
    )
    record = order_selected.SelectedPlateArtifactRecord(
        plate_index=1,
        project_filename="plate-1.3mf",
        intermediate_filename="plate-1.sl1",
        intermediate_sha256=execution.artifact.intermediate_sha256,
        native_filename="plate-1.ctb",
        native_sha256="f" * 64,
        issue_summary=execution.artifact.issue_summary,
        materialization=instance_evidence.materialized_plate,
        authority_evidence=authority,
    )

    with pytest.raises(
        order_selected.SelectedOrientationOrderError,
        match="printer-native hash does not match",
    ):
        order_selected.build_selected_orientation_order_manifest(
            source_filename="part.stl",
            source_sha256=SOURCE_SHA,
            requested_quantity=2,
            printer_profile="printer-a",
            resin_profile="resin-a",
            quality_profile="quality-a",
            selected_orientation_plan=selected,
            plate_artifacts=(record,),
            prusaslicer_version="2.9.6",
            prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
            uvtools_version="6.2.0",
            authority="production-authoritative",
        )
