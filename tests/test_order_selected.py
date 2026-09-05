import pytest

import app.order_selected as order_selected
from app.materialization import MaterializedPlateEvidence
from app.materialization_selected import SelectedMaterializedPlateEvidence
from app.orientation_plate import SelectedOrientationPlatePlan
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances


SOURCE_SHA = "d" * 64
SELECTED_PROJECT_SHA = "a" * 64
SELECTED_CONFIG_SHA = "9" * 64
SELECTED_INTERMEDIATE_SHA = "b" * 64
SELECTED_NATIVE_SHA = "c" * 64
PLATE_PROJECT_SHA = "e" * 64
PLATE_INTERMEDIATE_SHA = "f" * 64
PLATE_NATIVE_SHA = "1" * 64


def _selected_plan() -> SelectedOrientationPlatePlan:
    printer_plan = PrinterPlatePlan(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping="unverified",
        plan=plan_rectangular_instances(
            footprint_width_mm=30,
            footprint_depth_mm=30,
            quantity=1,
            plate_width_mm=80,
            plate_depth_mm=129,
            spacing_mm=5,
            edge_margin_mm=3,
        ),
    )
    return SelectedOrientationPlatePlan(
        orientation_deg=(15.0, 0.0, 0.0),
        source_sha256=SOURCE_SHA,
        review_project_sha256=SELECTED_PROJECT_SHA,
        effective_config_sha256=SELECTED_CONFIG_SHA,
        intermediate_sha256=SELECTED_INTERMEDIATE_SHA,
        native_sha256=SELECTED_NATIVE_SHA,
        pretranslation_envelope=Envelope2D(100, 130, -20, 10),
        printer_plate_plan=printer_plan,
    )


def _selected_plate_record(
    *,
    selected_native_sha: str = SELECTED_NATIVE_SHA,
    selected_config_sha: str = SELECTED_CONFIG_SHA,
):
    lower = MaterializedPlateEvidence(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping="unverified",
        plate_index=1,
        project_sha256=PLATE_PROJECT_SHA,
        translations=(),
        observations=(),
        automatic_materialization_authority=False,
    )
    selected_materialization = SelectedMaterializedPlateEvidence(
        source_sha256=SOURCE_SHA,
        selected_orientation_deg=(15.0, 0.0, 0.0),
        selected_review_3mf_sha256=SELECTED_PROJECT_SHA,
        selected_effective_config_sha256=selected_config_sha,
        selected_intermediate_sl1_sha256=SELECTED_INTERMEDIATE_SHA,
        selected_printer_native_sha256=selected_native_sha,
        materialized_plate=lower,
    )
    return order_selected.SelectedPlateArtifactRecord(
        plate_index=1,
        project_filename="plate-1.3mf",
        intermediate_filename="plate-1.sl1",
        intermediate_sha256=PLATE_INTERMEDIATE_SHA,
        native_filename="plate-1.ctb",
        native_sha256=PLATE_NATIVE_SHA,
        issue_summary={"islands": 0},
        materialization=selected_materialization,
    )


def test_selected_order_derives_orientation_and_converts_only_bound_plate_evidence(monkeypatch):
    captured = {}

    def fake_build_order_manifest(**kwargs):
        captured.update(kwargs)
        record = kwargs["plate_artifacts"][0]
        return {
            "schema": "workpiece-resin-order-manifest-v2",
            "orientation_deg": dict(kwargs["orientation_deg"]),
            "plates": [{"plate_index": record.plate_index}],
            "review_rule": "base materialization rule.",
        }

    monkeypatch.setattr(order_selected, "build_order_manifest", fake_build_order_manifest)
    selected = _selected_plan()
    record = _selected_plate_record()
    manifest = order_selected.build_selected_orientation_order_manifest(
        source_filename="part.stl",
        source_sha256=SOURCE_SHA,
        requested_quantity=1,
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        selected_orientation_plan=selected,
        plate_artifacts=(record,),
        prusaslicer_version="2.9.6",
        prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
        uvtools_version="6.2.0",
        authority="acceptance-candidate-only",
    )

    assert captured["orientation_deg"] == {"x": 15.0, "y": 0.0, "z": 0.0}
    assert captured["printer_plate_plan"] is selected.printer_plate_plan
    lower = captured["plate_artifacts"][0]
    assert lower.project_sha256 == PLATE_PROJECT_SHA
    assert lower.materialization is record.materialization.materialized_plate
    assert manifest["schema"] == "workpiece-resin-order-manifest-v3"
    assert manifest["selected_orientation_plan"]["source_sha256"] == SOURCE_SHA
    assert manifest["plates"][0]["selected_materialization"]["selected_sliced_artifacts"] == {
        "review_3mf_sha256": SELECTED_PROJECT_SHA,
        "effective_config_sha256": SELECTED_CONFIG_SHA,
        "intermediate_sl1_sha256": SELECTED_INTERMEDIATE_SHA,
        "printer_native_sha256": SELECTED_NATIVE_SHA,
    }
    assert "effective config" in manifest["review_rule"]
    assert "unrelated materialized plate" in manifest["review_rule"]


def test_selected_order_rejects_source_that_differs_from_orientation_validation(monkeypatch):
    def should_not_run(**kwargs):
        raise AssertionError("lower-level order builder must not run for mismatched source")

    monkeypatch.setattr(order_selected, "build_order_manifest", should_not_run)
    with pytest.raises(order_selected.SelectedOrientationOrderError, match="does not match"):
        order_selected.build_selected_orientation_order_manifest(
            source_filename="other.stl",
            source_sha256="2" * 64,
            requested_quantity=1,
            printer_profile="elegoo-mars-2",
            resin_profile="elegoo-water-washable-grey",
            quality_profile="balanced-0p05-medium",
            selected_orientation_plan=_selected_plan(),
            plate_artifacts=(_selected_plate_record(),),
            prusaslicer_version="2.9.6",
            prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
            uvtools_version="6.2.0",
            authority="acceptance-candidate-only",
        )


@pytest.mark.parametrize(
    "record",
    (
        _selected_plate_record(selected_native_sha="3" * 64),
        _selected_plate_record(selected_config_sha="4" * 64),
    ),
)
def test_selected_order_rejects_plate_from_different_sliced_recipe(monkeypatch, record):
    def should_not_run(**kwargs):
        raise AssertionError("lower-level order builder must not run for mismatched winner")

    monkeypatch.setattr(order_selected, "build_order_manifest", should_not_run)
    with pytest.raises(order_selected.SelectedOrientationOrderError, match="exact selected sliced artifact chain"):
        order_selected.build_selected_orientation_order_manifest(
            source_filename="part.stl",
            source_sha256=SOURCE_SHA,
            requested_quantity=1,
            printer_profile="elegoo-mars-2",
            resin_profile="elegoo-water-washable-grey",
            quality_profile="balanced-0p05-medium",
            selected_orientation_plan=_selected_plan(),
            plate_artifacts=(record,),
            prusaslicer_version="2.9.6",
            prusaslicer_commit="b028299c770b8380ee81c921a2867d522f288123",
            uvtools_version="6.2.0",
            authority="acceptance-candidate-only",
        )
