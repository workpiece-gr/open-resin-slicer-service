import hashlib
import math

import pytest

from app.materialization import (
    MaterializedEnvelopeObservation,
    finalize_materialized_plate,
    prepare_printer_plate_materialization,
)
from app.order import OrderManifestError, PlateArtifactRecord, build_order_manifest
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances


SOURCE_SHA = "d" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


def _printer_plan(mapping: str = "validated") -> PrinterPlatePlan:
    return PrinterPlatePlan(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping=mapping,
        plan=plan_rectangular_instances(
            footprint_width_mm=30,
            footprint_depth_mm=30,
            quantity=3,
            plate_width_mm=80,
            plate_depth_mm=70,
            spacing_mm=5,
            edge_margin_mm=5,
        ),
    )


def _materialization(profile_plan: PrinterPlatePlan, plate_index: int):
    spec = prepare_printer_plate_materialization(
        profile_plan,
        plate_index=plate_index,
        pretranslation_envelope=Envelope2D(100, 130, -20, 10),
        require_validated_mapping=(
            profile_plan.manufacturing_envelope_coordinate_mapping == "validated"
        ),
    )
    project = f"exact plate {plate_index} 3mf".encode()
    digest = hashlib.sha256(project).hexdigest()
    plate = next(item for item in profile_plan.plan.plates if item.plate_index == plate_index)
    observations = tuple(
        MaterializedEnvelopeObservation(
            instance_index=placement.instance_index,
            envelope=Envelope2D(
                placement.x_mm - 15,
                placement.x_mm + 15,
                placement.y_mm - 15,
                placement.y_mm + 15,
            ),
            project_sha256=digest,
        )
        for placement in plate.placements
    )
    return finalize_materialized_plate(
        spec,
        project_bytes=project,
        observations=observations,
    )


def _records(profile_plan: PrinterPlatePlan):
    records = []
    for plate_index in (2, 1):
        materialization = _materialization(profile_plan, plate_index)
        records.append(
            PlateArtifactRecord(
                plate_index=plate_index,
                project_filename=f"plate-{plate_index}.3mf",
                project_sha256=materialization.project_sha256,
                intermediate_filename=f"plate-{plate_index}.sl1",
                intermediate_sha256=INTERMEDIATE_SHA,
                native_filename=f"plate-{plate_index}.ctb",
                native_sha256=NATIVE_SHA,
                issue_summary={"islands": 0 if plate_index == 2 else 1},
                materialization=materialization,
            )
        )
    return records


def _build(*, mapping="validated", **overrides):
    profile_plan = _printer_plan(mapping)
    args = {
        "source_filename": "part.stl",
        "source_sha256": SOURCE_SHA,
        "requested_quantity": 3,
        "orientation_deg": {"x": 10, "y": 5, "z": 0},
        "printer_profile": "elegoo-mars-2",
        "resin_profile": "elegoo-water-washable-grey",
        "quality_profile": "balanced-0p05-medium",
        "printer_plate_plan": profile_plan,
        "plate_artifacts": _records(profile_plan),
        "prusaslicer_version": "2.9.6",
        "prusaslicer_commit": "b028299c770b8380ee81c921a2867d522f288123",
        "uvtools_version": "6.2.0",
        "authority": "acceptance-candidate-only",
    }
    args.update(overrides)
    return build_order_manifest(**args)


def test_manifest_orders_artifacts_and_binds_materialization_evidence():
    manifest = _build()
    assert manifest["schema"] == "workpiece-resin-order-manifest-v2"
    assert manifest["requested_quantity"] == 3
    assert [item["plate_index"] for item in manifest["plates"]] == [1, 2]
    assert [item["instance_indices"] for item in manifest["plates"]] == [[1, 2], [3]]
    assert manifest["plate_plan"]["layout"]["plate_count"] == 2
    assert manifest["plate_plan"]["automatic_materialization_authority"] is True
    for plate in manifest["plates"]:
        assert plate["files"]["review_3mf"]["sha256"] == plate["materialization"]["project_sha256"]


def test_candidate_manifest_can_record_unverified_mapping_without_authority():
    manifest = _build(mapping="unverified")
    assert manifest["authority"] == "acceptance-candidate-only"
    assert manifest["plate_plan"]["automatic_materialization_authority"] is False
    assert all(
        plate["materialization"]["automatic_materialization_authority"] is False
        for plate in manifest["plates"]
    )


def test_production_manifest_rejects_unverified_mapping():
    with pytest.raises(OrderManifestError, match="Production authority requires validated"):
        _build(mapping="unverified", authority="production-authoritative")


def test_manifest_rejects_project_hash_that_differs_from_materialization():
    profile_plan = _printer_plan()
    records = _records(profile_plan)
    first = records[0]
    records[0] = PlateArtifactRecord(
        plate_index=first.plate_index,
        project_filename=first.project_filename,
        project_sha256="f" * 64,
        intermediate_filename=first.intermediate_filename,
        intermediate_sha256=first.intermediate_sha256,
        native_filename=first.native_filename,
        native_sha256=first.native_sha256,
        issue_summary=first.issue_summary,
        materialization=first.materialization,
    )
    with pytest.raises(OrderManifestError, match="does not match the materialization evidence"):
        build_order_manifest(
            source_filename="part.stl",
            source_sha256=SOURCE_SHA,
            requested_quantity=3,
            orientation_deg={"x": 10, "y": 5, "z": 0},
            printer_profile="elegoo-mars-2",
            resin_profile="elegoo-water-washable-grey",
            quality_profile="balanced-0p05-medium",
            printer_plate_plan=profile_plan,
            plate_artifacts=records,
            prusaslicer_version="2.9.6",
            prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
            uvtools_version="6.2.0",
            authority="acceptance-candidate-only",
        )


def test_manifest_requires_exact_plate_coverage_and_quantity():
    profile_plan = _printer_plan()
    with pytest.raises(OrderManifestError, match="every planned physical plate"):
        _build(plate_artifacts=_records(profile_plan)[:1], printer_plate_plan=profile_plan)
    with pytest.raises(OrderManifestError, match="does not match"):
        _build(requested_quantity=4)


def test_manifest_rejects_invalid_paths_and_orientation():
    profile_plan = _printer_plan()
    records = _records(profile_plan)
    first = records[0]
    records[0] = PlateArtifactRecord(
        plate_index=first.plate_index,
        project_filename="../plate-2.3mf",
        project_sha256=first.project_sha256,
        intermediate_filename=first.intermediate_filename,
        intermediate_sha256=first.intermediate_sha256,
        native_filename=first.native_filename,
        native_sha256=first.native_sha256,
        issue_summary=first.issue_summary,
        materialization=first.materialization,
    )
    with pytest.raises(OrderManifestError, match="simple retained artifact filename"):
        _build(plate_artifacts=records, printer_plate_plan=profile_plan)
    with pytest.raises(OrderManifestError, match="finite numeric degrees"):
        _build(orientation_deg={"x": math.inf, "y": 0, "z": 0})
