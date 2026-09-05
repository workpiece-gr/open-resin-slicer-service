import pytest

import app.order_selected as order_selected
from app.orientation_plate import SelectedOrientationPlatePlan
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances


SOURCE_SHA = "d" * 64
PROJECT_SHA = "a" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


def _selected_plan() -> SelectedOrientationPlatePlan:
    printer_plan = PrinterPlatePlan(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping="unverified",
        plan=plan_rectangular_instances(
            footprint_width_mm=30,
            footprint_depth_mm=30,
            quantity=3,
            plate_width_mm=80,
            plate_depth_mm=129,
            spacing_mm=5,
            edge_margin_mm=3,
        ),
    )
    return SelectedOrientationPlatePlan(
        orientation_deg=(15.0, 0.0, 0.0),
        source_sha256=SOURCE_SHA,
        review_project_sha256=PROJECT_SHA,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        pretranslation_envelope=Envelope2D(100, 130, -20, 10),
        printer_plate_plan=printer_plan,
    )


def test_selected_order_derives_orientation_and_plate_plan_instead_of_accepting_free_angles(monkeypatch):
    captured = {}

    def fake_build_order_manifest(**kwargs):
        captured.update(kwargs)
        return {
            "schema": "workpiece-resin-order-manifest-v2",
            "orientation_deg": dict(kwargs["orientation_deg"]),
            "review_rule": "base materialization rule.",
        }

    monkeypatch.setattr(order_selected, "build_order_manifest", fake_build_order_manifest)
    selected = _selected_plan()
    manifest = order_selected.build_selected_orientation_order_manifest(
        source_filename="part.stl",
        source_sha256=SOURCE_SHA,
        requested_quantity=3,
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        selected_orientation_plan=selected,
        plate_artifacts=(),
        prusaslicer_version="2.9.6",
        prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
        uvtools_version="6.2.0",
        authority="acceptance-candidate-only",
    )

    assert captured["orientation_deg"] == {"x": 15.0, "y": 0.0, "z": 0.0}
    assert captured["printer_plate_plan"] is selected.printer_plate_plan
    assert manifest["schema"] == "workpiece-resin-order-manifest-v3"
    assert manifest["selected_orientation_plan"]["source_sha256"] == SOURCE_SHA
    assert manifest["selected_orientation_plan"]["selected_sliced_artifacts"] == {
        "review_3mf_sha256": PROJECT_SHA,
        "intermediate_sl1_sha256": INTERMEDIATE_SHA,
        "printer_native_sha256": NATIVE_SHA,
    }
    assert "callers cannot substitute independent orientation angles" in manifest["review_rule"]


def test_selected_order_rejects_source_that_differs_from_orientation_validation(monkeypatch):
    def should_not_run(**kwargs):
        raise AssertionError("lower-level order builder must not run for mismatched source")

    monkeypatch.setattr(order_selected, "build_order_manifest", should_not_run)
    with pytest.raises(order_selected.SelectedOrientationOrderError, match="does not match"):
        order_selected.build_selected_orientation_order_manifest(
            source_filename="other.stl",
            source_sha256="e" * 64,
            requested_quantity=3,
            printer_profile="elegoo-mars-2",
            resin_profile="elegoo-water-washable-grey",
            quality_profile="balanced-0p05-medium",
            selected_orientation_plan=_selected_plan(),
            plate_artifacts=(),
            prusaslicer_version="2.9.6",
            prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
            uvtools_version="6.2.0",
            authority="acceptance-candidate-only",
        )
