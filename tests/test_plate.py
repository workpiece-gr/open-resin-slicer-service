import pytest

from app.plate import PlatePlanError, plan_rectangular_instances, plate_plan_manifest


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
    assert rotated.capacity_per_plate == 2
    assert [(p.x_mm, p.y_mm, p.rotation_z_deg) for p in rotated.plates[0].placements] == [
        (17.5, 27.5, 90),
        (47.5, 27.5, 90),
    ]

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
