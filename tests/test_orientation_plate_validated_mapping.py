import json

from app.native_envelope import native_envelope_from_rectangle
from app.orientation import OrientationDecision, ScoredOrientation
from app.orientation_candidates import OrientationSpec
from app.orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    orientation_plate_plan_manifest,
    plan_selected_sliced_orientation,
)
from app.orientation_sliced import SlicedFinalistEvidence, SlicedOrientationValidation
from app.profiles import ProfileRegistry
from app.uvtools_metrics import NativeBoundingRectangle


SOURCE_SHA = "d" * 64
PROJECT_SHA = "a" * 64
CONFIG_SHA = "9" * 64
INTERMEDIATE_SHA = "b" * 64
NATIVE_SHA = "c" * 64


def _registry(tmp_path) -> ProfileRegistry:
    printers = tmp_path / "printers"
    printers.mkdir()
    (printers / "printer-a.ini").write_text("printer_technology = SLA\n", encoding="utf-8")
    (printers / "printer-a.json").write_text(
        json.dumps(
            {
                "id": "printer-a",
                "candidate_ready": True,
                "production_ready": False,
                "config": "printers/printer-a.ini",
                "display_width_mm": 130,
                "display_height_mm": 82,
                "manufacturing_envelope_width_mm": 80,
                "manufacturing_envelope_depth_mm": 129,
                "manufacturing_envelope_coordinate_mapping": "validated",
                "manufacturing_to_display_transform": {
                    "origin_display_x_mm": 129,
                    "origin_display_y_mm": 0,
                    "x_axis_display_x": 0,
                    "x_axis_display_y": 1,
                    "y_axis_display_x": -1,
                    "y_axis_display_y": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    return ProfileRegistry(tmp_path)


def _validation() -> SlicedOrientationValidation:
    spec = OrientationSpec(15, 0)
    evidence = SlicedFinalistEvidence(
        spec=spec,
        source_sha256=SOURCE_SHA,
        review_project_sha256=PROJECT_SHA,
        effective_config_sha256=CONFIG_SHA,
        intermediate_sha256=INTERMEDIATE_SHA,
        native_sha256=NATIVE_SHA,
        max_layer_area_mm2=100,
        material_volume_mm3=1000,
        footprint_area_mm2=500,
        z_height_mm=20,
    )
    candidate = spec.with_metrics(evidence.metrics)
    selected = ScoredOrientation(
        candidate=candidate,
        blocked_reasons=(),
        score=0.0,
        score_components={},
    )
    return SlicedOrientationValidation(
        source_sha256=SOURCE_SHA,
        evidence=(evidence,),
        decision=OrientationDecision(
            selected=selected,
            ranked=(selected,),
            require_sliced_validation=True,
        ),
    )


def test_validated_axis_swap_maps_ctb_bounds_before_plate_packing(tmp_path):
    registry = _registry(tmp_path)
    native = native_envelope_from_rectangle(
        printer_profile_id="printer-a",
        printer_native_sha256=NATIVE_SHA,
        rectangle=NativeBoundingRectangle(
            x_mm=90,
            y_mm=10,
            width_mm=30,
            height_mm=20,
        ),
    )
    result = plan_selected_sliced_orientation(
        registry=registry,
        printer_profile_id="printer-a",
        sliced_validation=_validation(),
        native_envelope=native,
        quantity=1,
        allow_rotate_90=False,
    )

    assert result.native_display_envelope.width_mm == 30
    assert result.native_display_envelope.depth_mm == 20
    assert result.pretranslation_coordinate_space == MANUFACTURING_ENVELOPE_COORDINATE_SPACE
    assert result.pretranslation_envelope.min_x_mm == 10
    assert result.pretranslation_envelope.max_x_mm == 30
    assert result.pretranslation_envelope.min_y_mm == 9
    assert result.pretranslation_envelope.max_y_mm == 39
    assert result.printer_plate_plan.plan.instance_footprint_width_mm == 20
    assert result.printer_plate_plan.plan.instance_footprint_depth_mm == 30
    assert result.manufacturing_to_display_transform == registry.printer_manufacturing_display_transform("printer-a")
    manifest = orientation_plate_plan_manifest(result)
    assert manifest["automatic_materialization_authority"] is True
    assert manifest["manufacturing_to_display_transform"] is not None
