import math

import pytest

from app.uvtools_metrics import (
    UVtoolsMetricError,
    base_property_command,
    layer_property_command,
    parse_base_native_properties,
    parse_layer_native_properties,
    parse_native_artifact_metrics,
)


BASE = """Opening file part.ctb: Done in 0.01s
-------------------------
LayerCount: 3
PrintHeight: 0.15
BoundingRectangleMillimeters: {X=1.25,Y=2.5,Width=20.5,Height=30.25}
-------------------------
Total properties: 3
"""

LAYERS = """Opening file part.ctb: Done in 0.01s
-------------------------
# Layer: 0
Area: 100.25
Volume: 5.013
-------------------------
# Layer: 1
Area: 140.5
Volume: 7.025
-------------------------
# Layer: 2
Area: 80.125
Volume: 4.006
-------------------------
Total properties: 6
"""


def test_parses_exact_native_metrics_from_base_and_layer_properties():
    metrics = parse_native_artifact_metrics(BASE, LAYERS)
    assert metrics.layer_count == 3
    assert metrics.max_layer_area_mm2 == 140.5
    assert metrics.material_volume_mm3 == 16.044
    assert metrics.footprint_area_mm2 == round(20.5 * 30.25, 6)
    assert metrics.z_height_mm == 0.15


def test_base_parser_requires_complete_positive_geometry():
    count, height, footprint = parse_base_native_properties(BASE)
    assert (count, height, footprint) == (3, 0.15, 20.5 * 30.25)

    with pytest.raises(UVtoolsMetricError, match="missing"):
        parse_base_native_properties(BASE.replace("PrintHeight: 0.15\n", ""))
    with pytest.raises(UVtoolsMetricError, match="positive"):
        parse_base_native_properties(BASE.replace("Width=20.5", "Width=0"))
    with pytest.raises(UVtoolsMetricError, match="finite"):
        parse_base_native_properties(BASE.replace("PrintHeight: 0.15", f"PrintHeight: {math.inf}"))


def test_layer_parser_requires_exact_layer_coverage_and_properties():
    assert parse_layer_native_properties(LAYERS, expected_layer_count=3) == (140.5, 16.044)

    with pytest.raises(UVtoolsMetricError, match="cover every layer"):
        parse_layer_native_properties(LAYERS.replace("# Layer: 2", "# Layer: 3"), expected_layer_count=3)
    with pytest.raises(UVtoolsMetricError, match="missing: Volume"):
        parse_layer_native_properties(LAYERS.replace("Volume: 7.025\n", ""), expected_layer_count=3)
    with pytest.raises(UVtoolsMetricError, match="Duplicate"):
        parse_layer_native_properties(LAYERS.replace("Area: 100.25", "Area: 100.25\nArea: 100.25"), expected_layer_count=3)


def test_commands_are_bounded_and_request_only_authoritative_properties():
    assert base_property_command("/opt/uvtools/UVtoolsCmd", "/tmp/part.ctb") == (
        "/opt/uvtools/UVtoolsCmd",
        "print-properties",
        "/tmp/part.ctb",
        "-n",
        "LayerCount",
        "PrintHeight",
        "BoundingRectangleMillimeters",
        "--no-progress",
    )
    assert layer_property_command("UVtoolsCmd", "part.ctb", layer_count=3) == (
        "UVtoolsCmd",
        "print-properties",
        "part.ctb",
        "-r",
        "0:2",
        "-n",
        "Area",
        "Volume",
        "--no-progress",
    )
    with pytest.raises(UVtoolsMetricError):
        layer_property_command("UVtoolsCmd", "part.ctb", layer_count=0)
