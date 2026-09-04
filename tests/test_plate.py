import math
from pathlib import Path

import pytest

from app.plate import (
    MAX_PLATES,
    PlatePlanError,
    plan_printer_profile_instances,
    plan_rectangular_instances,
    plate_plan_manifest,
    printer_plate_plan_manifest,
)
from app.profiles import ProfileRegistry


def _assert_all_placements_within_margins(plan):
    half_width = plan.placed_footprint_width_mm / 2
    half_depth = plan.placed_footprint_depth_mm / 2
    tolerance = 1e-9
    for plate in plan.plates:
        for placement in plate.placements:
            assert placement.x_mm - half_width >= plan.edge_margin_mm - tolerance
            assert placement.y_mm - half_depth >= plan.edge_margin_mm - tolerance
            assert placement.x_mm + half_width <= plan.plate_width_mm - plan.edge_margin_mm + tolerance
            assert placement.y_mm + half_depth <= plan.plate_depth_mm - plan.edge_margin_mm + tolerance


def test_row_major_multi_plate_plan_is_deterministic():
    kwargs = dict(
        footprint_width_mm=30,
        footprint_depth_mm=30,
        quantity=5,
        plate_width_mm=80,
        plate_depth_mm=70,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    plan = plan_rectangular_instances(**kwargs)
    assert plan == plan_rectangular_instances(**kwargs)
    assert plan.capacity_per_plate == 2
    assert plan.plate_count == 3
    assert [len(item.placements) for item in plan.plates] == [2, 2, 1]
    assert [(item.x_mm, item.y_mm) for item in plan.plates[0].placements] == [(20.0, 20.0), (55.0, 20.0)]
    assert [p.instance_index for part in plan.plates for p in part.placements] == [1, 2, 3, 4, 5]
    _assert_all_placements_within_margins(plan)


def test_rotation_is_used_only_when_it_strictly_improves_capacity():
    rotated = plan_rectangular_instances(
        footprint_width_mm=45,
        footprint_depth_mm=25,
        quantity=2,
        plate_width_mm=80,
        plate_depth_mm=60,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    assert rotated.rotation_z_deg == 90
    assert rotated.placed_footprint_width_mm == 25
    assert rotated.placed_footprint_depth_mm == 45
    assert rotated.capacity_per_plate == 2
    assert [(p.x_mm, p.y_mm, p.rotation_z_deg) for p in rotated.plates[0].placements] == [
        (17.5, 27.5, 90),
        (47.5, 27.5, 90),
    ]
    _assert_all_placements_within_margins(rotated)

    tied = plan_rectangular_instances(
        footprint_width_mm=20,
        footprint_depth_mm=30,
        quantity=1,
        plate_width_mm=80,
        plate_depth_mm=80,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    assert tied.rotation_z_deg == 0


def test_exact_fit_respects_validated_manufacturing_envelope():
    plan = plan_rectangular_instances(
        footprint_width_mm=74,
        footprint_depth_mm=123,
        quantity=1,
        plate_width_mm=80,
        plate_depth_mm=129,
        spacing_mm=0,
        edge_margin_mm=3,
        allow_rotate_90=False,
    )
    assert plan.capacity_per_plate == 1
    assert [(p.x_mm, p.y_mm) for p in plan.plates[0].placements] == [(40.0, 64.5)]
    _assert_all_placements_within_margins(plan)


def test_profile_entry_point_uses_mars2_manufacturing_envelope_not_display():
    root = Path(__file__).resolve().parents[1] / "profiles"
    registry = ProfileRegistry(root)
    profile_plan = plan_printer_profile_instances(
        registry=registry,
        printer_profile_id="elegoo-mars-2",
        footprint_width_mm=74,
        footprint_depth_mm=123,
        quantity=1,
        spacing_mm=0,
        edge_margin_mm=3,
        allow_rotate_90=False,
    )
    assert profile_plan.plan.plate_width_mm == 80.0
    assert profile_plan.plan.plate_depth_mm == 129.0
    assert profile_plan.manufacturing_envelope_coordinate_mapping == "unverified"
    manifest = printer_plate_plan_manifest(profile_plan)
    assert manifest["printer_profile_id"] == "elegoo-mars-2"
    assert manifest["plate"]["width_mm"] == 80.0
    assert manifest["plate"]["depth_mm"] == 129.0
    assert manifest["automatic_materialization_authority"] is False


def test_plan_rejects_parts_that_do_not_fit_the_usable_plate():
    with pytest.raises(PlatePlanError, match="does not fit"):
        plan_rectangular_instances(
            footprint_width_mm=100,
            footprint_depth_mm=100,
            quantity=1,
            plate_width_mm=80,
            plate_depth_mm=70,
        )


def test_plan_rejects_invalid_quantity_and_dimensions():
    with pytest.raises(PlatePlanError, match="quantity"):
        plan_rectangular_instances(
            footprint_width_mm=10,
            footprint_depth_mm=10,
            quantity=0,
            plate_width_mm=80,
            plate_depth_mm=70,
        )
    with pytest.raises(PlatePlanError, match="spacing_mm"):
        plan_rectangular_instances(
            footprint_width_mm=10,
            footprint_depth_mm=10,
            quantity=1,
            plate_width_mm=80,
            plate_depth_mm=70,
            spacing_mm=-1,
        )
    with pytest.raises(PlatePlanError, match="footprint_width_mm"):
        plan_rectangular_instances(
            footprint_width_mm=math.inf,
            footprint_depth_mm=10,
            quantity=1,
            plate_width_mm=80,
            plate_depth_mm=70,
        )
    with pytest.raises(PlatePlanError, match="plate_width_mm"):
        plan_rectangular_instances(
            footprint_width_mm=10,
            footprint_depth_mm=10,
            quantity=1,
            plate_width_mm=True,
            plate_depth_mm=70,
        )


def test_plan_rejects_requests_over_physical_plate_limit():
    with pytest.raises(PlatePlanError, match=f"more than {MAX_PLATES}"):
        plan_rectangular_instances(
            footprint_width_mm=80,
            footprint_depth_mm=129,
            quantity=MAX_PLATES + 1,
            plate_width_mm=80,
            plate_depth_mm=129,
            spacing_mm=0,
            edge_margin_mm=0,
            allow_rotate_90=False,
        )


def test_manifest_preserves_physical_plate_authority_contract():
    plan = plan_rectangular_instances(
        footprint_width_mm=30,
        footprint_depth_mm=30,
        quantity=5,
        plate_width_mm=80,
        plate_depth_mm=70,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    manifest = plate_plan_manifest(plan)
    assert manifest["schema"] == "workpiece-resin-plate-plan-v1"
    assert manifest["strategy"] == "deterministic-row-major"
    assert manifest["artifact_rule"] == "one physical plate -> one review project -> one printer-native file"
    assert "target centres" in manifest["placement_semantics"]
    assert "not raw mesh-origin translations" in manifest["placement_semantics"]
    assert "validated printable manufacturing envelope" in manifest["plate_envelope_rule"]
    assert manifest["placed_instance_footprint_mm"] == {"width": 30.0, "depth": 30.0}
    assert manifest["layout"] == {
        "rotation_z_deg": 0,
        "columns": 2,
        "rows": 1,
        "capacity_per_plate": 2,
        "quantity": 5,
        "plate_count": 3,
    }
    assert [item["plate_index"] for item in manifest["plates"]] == [1, 2, 3]
    assert [len(item["placements"]) for item in manifest["plates"]] == [2, 2, 1]
