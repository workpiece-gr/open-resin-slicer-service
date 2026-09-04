import math

import pytest

from app.order import OrderManifestError, PlateArtifactRecord, build_order_manifest
from app.plate import plan_rectangular_instances


SOURCE_SHA = "d" * 64
PROJECT_SHA = "a" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


def _plan():
    return plan_rectangular_instances(
        footprint_width_mm=30,
        footprint_depth_mm=30,
        quantity=3,
        plate_width_mm=80,
        plate_depth_mm=70,
        spacing_mm=5,
        edge_margin_mm=5,
    )


def _records():
    return [
        PlateArtifactRecord(
            plate_index=2,
            project_filename="plate-2.3mf",
            project_sha256=PROJECT_SHA,
            intermediate_filename="plate-2.sl1",
            intermediate_sha256=INTERMEDIATE_SHA,
            native_filename="plate-2.ctb",
            native_sha256=NATIVE_SHA,
            issue_summary={"islands": 0},
        ),
        PlateArtifactRecord(
            plate_index=1,
            project_filename="plate-1.3mf",
            project_sha256=PROJECT_SHA,
            intermediate_filename="plate-1.sl1",
            intermediate_sha256=INTERMEDIATE_SHA,
            native_filename="plate-1.ctb",
            native_sha256=NATIVE_SHA,
            issue_summary={"islands": 1},
        ),
    ]


def _build(**overrides):
    args = {
        "source_filename": "part.stl",
        "source_sha256": SOURCE_SHA,
        "requested_quantity": 3,
        "orientation_deg": {"x": 10, "y": 5, "z": 0},
        "printer_profile": "elegoo-mars-2",
        "resin_profile": "elegoo-water-washable-grey",
        "quality_profile": "balanced-0p05-medium",
        "plate_plan": _plan(),
        "plate_artifacts": _records(),
        "prusaslicer_version": "2.9.6",
        "prusaslicer_commit": "b028299c770b8380ee81c921a2867d522f288123",
        "uvtools_version": "6.2.0",
        "authority": "acceptance-candidate-only",
    }
    args.update(overrides)
    return build_order_manifest(**args)


def test_manifest_orders_artifacts_by_physical_plate_and_binds_instances():
    manifest = _build()
    assert manifest["schema"] == "workpiece-resin-order-manifest-v1"
    assert manifest["requested_quantity"] == 3
    assert manifest["source"]["sha256"] == SOURCE_SHA
    assert [item["plate_index"] for item in manifest["plates"]] == [1, 2]
    assert [item["instance_indices"] for item in manifest["plates"]] == [[1, 2], [3]]
    assert manifest["plate_plan"]["layout"]["plate_count"] == 2


def test_manifest_requires_exact_physical_plate_coverage():
    with pytest.raises(OrderManifestError, match="every planned physical plate"):
        _build(plate_artifacts=_records()[:1])


def test_manifest_rejects_duplicate_physical_plate_records():
    duplicate = [_records()[0], _records()[0]]
    with pytest.raises(OrderManifestError, match="Duplicate"):
        _build(plate_artifacts=duplicate)


def test_manifest_rejects_quantity_drift():
    with pytest.raises(OrderManifestError, match="does not match"):
        _build(requested_quantity=4)


def test_manifest_rejects_invalid_hashes_and_paths():
    records = _records()
    records[0] = PlateArtifactRecord(
        plate_index=2,
        project_filename="../plate-2.3mf",
        project_sha256=PROJECT_SHA,
        intermediate_filename="plate-2.sl1",
        intermediate_sha256=INTERMEDIATE_SHA,
        native_filename="plate-2.ctb",
        native_sha256=NATIVE_SHA,
        issue_summary={},
    )
    with pytest.raises(OrderManifestError, match="simple retained artifact filename"):
        _build(plate_artifacts=records)

    records = _records()
    records[0] = PlateArtifactRecord(
        plate_index=2,
        project_filename="plate-2.3mf",
        project_sha256="bad",
        intermediate_filename="plate-2.sl1",
        intermediate_sha256=INTERMEDIATE_SHA,
        native_filename="plate-2.ctb",
        native_sha256=NATIVE_SHA,
        issue_summary={},
    )
    with pytest.raises(OrderManifestError, match="SHA-256"):
        _build(plate_artifacts=records)


def test_manifest_rejects_invalid_issue_counts_and_orientation():
    records = _records()
    records[0] = PlateArtifactRecord(
        plate_index=2,
        project_filename="plate-2.3mf",
        project_sha256=PROJECT_SHA,
        intermediate_filename="plate-2.sl1",
        intermediate_sha256=INTERMEDIATE_SHA,
        native_filename="plate-2.ctb",
        native_sha256=NATIVE_SHA,
        issue_summary={"islands": -1},
    )
    with pytest.raises(OrderManifestError, match="non-negative integers"):
        _build(plate_artifacts=records)
    with pytest.raises(OrderManifestError, match="finite numeric degrees"):
        _build(orientation_deg={"x": math.inf, "y": 0, "z": 0})
