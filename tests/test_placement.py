import pytest

from app.placement import (
    Envelope2D,
    PlacementError,
    derive_plate_translations,
    validate_materialized_plate,
)
from app.plate import plan_rectangular_instances


def _two_instance_plan():
    return plan_rectangular_instances(
        footprint_width_mm=30,
        footprint_depth_mm=30,
        quantity=2,
        plate_width_mm=80,
        plate_depth_mm=70,
        spacing_mm=5,
        edge_margin_mm=5,
        allow_rotate_90=False,
    )


def test_translation_targets_envelope_center_not_model_origin():
    plan = _two_instance_plan()
    # Deliberately arbitrary source/project origin: centre is (115, -5), not (0, 0).
    source = Envelope2D(min_x_mm=100, max_x_mm=130, min_y_mm=-20, max_y_mm=10)
    transforms = derive_plate_translations(
        plan,
        plate_index=1,
        pretranslation_envelope=source,
    )
    first = transforms[0]
    assert (first.target_x_mm, first.target_y_mm) == (20.0, 20.0)
    assert (first.translate_x_mm, first.translate_y_mm) == (-95.0, 25.0)
    placed = source.translated(first.translate_x_mm, first.translate_y_mm)
    assert (placed.min_x_mm, placed.max_x_mm, placed.min_y_mm, placed.max_y_mm) == (5.0, 35.0, 5.0, 35.0)


def test_translation_rejects_footprint_drift_before_materialization():
    plan = _two_instance_plan()
    with pytest.raises(PlacementError, match="width differs"):
        derive_plate_translations(
            plan,
            plate_index=1,
            pretranslation_envelope=Envelope2D(0, 31, 0, 30),
        )


def test_final_materialized_envelopes_pass_when_they_match_slots():
    plan = _two_instance_plan()
    validate_materialized_plate(
        plan,
        plate_index=1,
        materialized_envelopes={
            1: Envelope2D(5, 35, 5, 35),
            2: Envelope2D(40, 70, 5, 35),
        },
    )


def test_final_materialization_fails_if_support_or_pad_escapes_planned_slot():
    plan = _two_instance_plan()
    with pytest.raises(PlacementError, match="escaped its planned footprint slot"):
        validate_materialized_plate(
            plan,
            plate_index=1,
            materialized_envelopes={
                1: Envelope2D(5, 36, 5, 35),
                2: Envelope2D(40, 70, 5, 35),
            },
        )


def test_final_materialization_fails_on_missing_or_extra_instances():
    plan = _two_instance_plan()
    with pytest.raises(PlacementError, match="missing=\\[2\\]"):
        validate_materialized_plate(
            plan,
            plate_index=1,
            materialized_envelopes={
                1: Envelope2D(5, 35, 5, 35),
            },
        )


def test_envelope_rejects_invalid_bounds():
    with pytest.raises(PlacementError, match="strictly greater"):
        Envelope2D(10, 10, 0, 1)
    with pytest.raises(PlacementError, match="finite"):
        Envelope2D(0, float("inf"), 0, 1)
