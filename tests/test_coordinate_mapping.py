import pytest

from app.coordinate_mapping import CoordinateMappingError, ManufacturingDisplayTransform


def test_mapping_round_trips_manufacturing_and_display_coordinates():
    mapping = ManufacturingDisplayTransform(
        origin_display_x_mm=2.0,
        origin_display_y_mm=3.0,
        x_axis_display_x=1.0,
        x_axis_display_y=0.0,
        y_axis_display_x=0.0,
        y_axis_display_y=1.0,
    )
    assert mapping.to_display(10, 20) == (12.0, 23.0)
    assert mapping.to_manufacturing(12, 23) == (10.0, 20.0)
    assert mapping.display_bounds_to_manufacturing_bounds(
        min_display_x_mm=12,
        max_display_x_mm=42,
        min_display_y_mm=23,
        max_display_y_mm=43,
    ) == (10.0, 40.0, 20.0, 40.0)
    assert mapping.display_rotation_for_manufacturing(90) == 90


def test_mapping_supports_axis_swap_and_reflection_without_assuming_centering():
    mapping = ManufacturingDisplayTransform(
        origin_display_x_mm=129.0,
        origin_display_y_mm=0.0,
        x_axis_display_x=0.0,
        x_axis_display_y=1.0,
        y_axis_display_x=-1.0,
        y_axis_display_y=0.0,
    )
    mapping.validate_envelope_inside_display(
        width_mm=80,
        depth_mm=129,
        display_width_mm=130,
        display_height_mm=82,
    )
    assert mapping.to_display(0, 0) == (129.0, 0.0)
    assert mapping.to_display(80, 129) == (0.0, 80.0)
    assert mapping.to_manufacturing(0, 80) == (80.0, 129.0)
    assert mapping.display_bounds_to_manufacturing_bounds(
        min_display_x_mm=90,
        max_display_x_mm=120,
        min_display_y_mm=10,
        max_display_y_mm=30,
    ) == (10.0, 30.0, 9.0, 39.0)
    assert mapping.determinant == 1.0
    assert mapping.display_rotation_for_manufacturing(90) == 90


def test_reflected_mapping_reverses_physical_rotation_sign():
    mapping = ManufacturingDisplayTransform(0, 80, 1, 0, 0, -1)
    assert mapping.determinant == -1.0
    assert mapping.display_rotation_for_manufacturing(0) == 0
    assert mapping.display_rotation_for_manufacturing(90) == -90
    with pytest.raises(CoordinateMappingError, match="0 or 90"):
        mapping.display_rotation_for_manufacturing(-90)


def test_mapping_rejects_scaled_or_nonorthogonal_axes():
    with pytest.raises(CoordinateMappingError, match="unit vector"):
        ManufacturingDisplayTransform(0, 0, 2, 0, 0, 1)
    with pytest.raises(CoordinateMappingError, match="perpendicular"):
        ManufacturingDisplayTransform(0, 0, 1, 0, 1, 0)


def test_validated_envelope_must_fit_entirely_inside_display():
    mapping = ManufacturingDisplayTransform(5, 5, 1, 0, 0, 1)
    with pytest.raises(CoordinateMappingError, match="outside the printer display"):
        mapping.validate_envelope_inside_display(
            width_mm=80,
            depth_mm=129,
            display_width_mm=82,
            display_height_mm=130,
        )


def test_mapping_parser_requires_exact_schema():
    with pytest.raises(CoordinateMappingError, match="missing"):
        ManufacturingDisplayTransform.from_mapping({"origin_display_x_mm": 0})

    value = {
        "origin_display_x_mm": 1,
        "origin_display_y_mm": 2,
        "x_axis_display_x": 1,
        "x_axis_display_y": 0,
        "y_axis_display_x": 0,
        "y_axis_display_y": -1,
    }
    assert ManufacturingDisplayTransform.from_mapping(value).manifest() == {
        key: float(number) for key, number in value.items()
    }
