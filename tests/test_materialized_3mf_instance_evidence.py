from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from dataclasses import replace

import pytest

from app.coordinate_mapping import ManufacturingDisplayTransform
from app.materialization_selected import materialize_selected_plate_project
from app.materialized_3mf_instance_evidence import (
    Materialized3MFInstanceEvidenceError,
    VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE,
    finalize_selected_materialized_plate_from_verified_build_items,
    materialized_3mf_instance_evidence_manifest,
)
from app.orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    SelectedOrientationPlatePlan,
)
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances
from app.profiles import ProfileRegistry


SOURCE_SHA = "d" * 64
CONFIG_SHA = "9" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64
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
        model_info = zipfile.ZipInfo(MODEL_MEMBER, date_time=(2026, 1, 2, 3, 4, 6))
        model_info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(model_info, model)
        support_info = zipfile.ZipInfo(
            "Metadata/Slic3r_PE_sla_support_points.txt",
            date_time=(2026, 1, 2, 3, 4, 6),
        )
        support_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(support_info, b"exact-selected-support-metadata")
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


def _registry(tmp_path) -> ProfileRegistry:
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
                "production_ready": False,
                "config": "printers/printer-a.ini",
                "display_width_mm": 80,
                "display_height_mm": 60,
                "manufacturing_envelope_width_mm": 80,
                "manufacturing_envelope_depth_mm": 60,
                "manufacturing_envelope_coordinate_mapping": "validated",
                "manufacturing_to_display_transform": _mapping().manifest(),
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
        effective_config_sha256=CONFIG_SHA,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
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


def _materialization(tmp_path):
    source = _source_project()
    selected = _selected(source)
    materialization = materialize_selected_plate_project(
        selected,
        registry=_registry(tmp_path),
        plate_index=1,
        selected_review_project_bytes=source,
    )
    return source, selected, materialization


def _replace_member(project: bytes, member: str, payload: bytes) -> bytes:
    source = io.BytesIO(project)
    output = io.BytesIO()
    with zipfile.ZipFile(source, "r") as input_zip, zipfile.ZipFile(output, "w") as output_zip:
        for info in input_zip.infolist():
            target = copy.copy(info)
            data = payload if info.filename == member else input_zip.read(info.filename)
            output_zip.writestr(target, data)
    return output.getvalue()


def test_verified_build_items_drive_per_instance_manufacturing_envelopes(tmp_path):
    source, selected, materialization = _materialization(tmp_path)
    evidence = finalize_selected_materialized_plate_from_verified_build_items(
        selected,
        materialization,
        selected_review_project_bytes=source,
    )

    assert materialization.display_placements[0].rotation_z_deg == -90
    assert evidence.build_items.selected_review_project_sha256 == _sha(source)
    assert evidence.build_items.project_sha256 == materialization.project.sha256
    assert evidence.build_items.instance_indices == (1, 2)
    assert evidence.build_items.object_id == 1
    assert evidence.manufacturing_envelopes == (
        Envelope2D(5, 30, 5, 50),
        Envelope2D(35, 60, 5, 50),
    )
    observations = evidence.materialized_plate.materialized_plate.observations
    assert [item.instance_index for item in observations] == [1, 2]
    assert {item.source for item in observations} == {
        VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE
    }
    assert evidence.materialized_plate.materialized_plate.automatic_materialization_authority is True

    manifest = materialized_3mf_instance_evidence_manifest(evidence)
    assert manifest["schema"] == "workpiece-resin-materialized-3mf-instance-evidence-v1"
    assert manifest["full_project_deterministic_reconstruction_matched"] is True
    assert manifest["geometry_reextraction_performed"] is False
    assert manifest["per_instance_materialized_project_validation_satisfied"] is True
    assert manifest["whole_plate_native_validation_still_required"] is True
    assert manifest["instance_count"] == 2
    assert [item["instance_index"] for item in manifest["instances"]] == [1, 2]


def test_non_build_metadata_tamper_is_rejected_even_with_recomputed_project_hash(tmp_path):
    source, selected, materialization = _materialization(tmp_path)
    tampered_bytes = _replace_member(
        materialization.project.bytes,
        "Metadata/Slic3r_PE_sla_support_points.txt",
        b"tampered-support-metadata",
    )
    tampered = replace(
        materialization,
        project=replace(
            materialization.project,
            bytes=tampered_bytes,
            sha256=_sha(tampered_bytes),
        ),
    )

    with pytest.raises(
        Materialized3MFInstanceEvidenceError,
        match="byte-for-byte equal",
    ):
        finalize_selected_materialized_plate_from_verified_build_items(
            selected,
            tampered,
            selected_review_project_bytes=source,
        )


def test_wrong_selected_review_bytes_are_rejected_before_instance_proof(tmp_path):
    _, selected, materialization = _materialization(tmp_path)
    with pytest.raises(
        Materialized3MFInstanceEvidenceError,
        match="sliced-winner SHA-256",
    ):
        finalize_selected_materialized_plate_from_verified_build_items(
            selected,
            materialization,
            selected_review_project_bytes=b"different selected review project",
        )


def test_materializer_transform_receipt_drift_is_rejected(tmp_path):
    source, selected, materialization = _materialization(tmp_path)
    transforms = list(materialization.project.display_transforms)
    first = list(transforms[0])
    first[-3] += 1
    transforms[0] = tuple(first)
    drifted = replace(
        materialization,
        project=replace(
            materialization.project,
            display_transforms=tuple(transforms),
        ),
    )

    with pytest.raises(
        Materialized3MFInstanceEvidenceError,
        match="receipt differs",
    ):
        finalize_selected_materialized_plate_from_verified_build_items(
            selected,
            drifted,
            selected_review_project_bytes=source,
        )


def test_selected_sliced_chain_drift_is_rejected(tmp_path):
    source, selected, materialization = _materialization(tmp_path)
    changed = replace(selected, native_sha256="f" * 64)
    with pytest.raises(
        Materialized3MFInstanceEvidenceError,
        match="exact selected sliced-orientation artifact chain",
    ):
        finalize_selected_materialized_plate_from_verified_build_items(
            changed,
            materialization,
            selected_review_project_bytes=source,
        )
