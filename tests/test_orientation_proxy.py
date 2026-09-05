import math
import struct

import pytest

from app.orientation_candidates import OrientationSpec
from app.orientation_proxy import (
    OrientationProxyError,
    analyze_geometry_proxy,
    parse_proxy_triangles,
    rotate_point_prusa_cli,
)


def _binary_stl(triangles):
    data = bytearray(84 + 50 * len(triangles))
    struct.pack_into("<I", data, 80, len(triangles))
    for index, triangle in enumerate(triangles):
        offset = 84 + index * 50
        flat = [0.0, 0.0, 1.0] + [value for point in triangle for value in point]
        struct.pack_into("<12fH", data, offset, *flat, 0)
    return bytes(data)


def _cube(size=10.0):
    s = size
    p = {
        "000": (0, 0, 0), "100": (s, 0, 0), "110": (s, s, 0), "010": (0, s, 0),
        "001": (0, 0, s), "101": (s, 0, s), "111": (s, s, s), "011": (0, s, s),
    }
    return [
        (p["000"], p["010"], p["110"]), (p["000"], p["110"], p["100"]),
        (p["001"], p["101"], p["111"]), (p["001"], p["111"], p["011"]),
        (p["000"], p["100"], p["101"]), (p["000"], p["101"], p["001"]),
        (p["010"], p["011"], p["111"]), (p["010"], p["111"], p["110"]),
        (p["000"], p["001"], p["011"]), (p["000"], p["011"], p["010"]),
        (p["100"], p["110"], p["111"]), (p["100"], p["111"], p["101"]),
    ]


def test_binary_parser_and_cube_proxy_area_are_deterministic():
    triangles = parse_proxy_triangles(_binary_stl(_cube()))
    first = analyze_geometry_proxy(triangles, OrientationSpec(0, 0, 0), max_sample_layers=32)
    second = analyze_geometry_proxy(triangles, OrientationSpec(0, 0, 0), max_sample_layers=32)
    assert first == second
    assert first.triangle_count == 12
    assert first.z_height_mm == 10.0
    assert first.xy_width_mm == 10.0
    assert first.xy_depth_mm == 10.0
    assert first.max_sampled_layer_area_mm2 == pytest.approx(100.0, abs=1e-5)
    assert first.downward_projected_area_mm2 == pytest.approx(100.0, abs=1e-5)
    assert first.open_contour_sample_count == 0
    assert first.reliable_for_auto_screening is True


def test_tilt_changes_cube_envelope_and_proxy_cross_section():
    triangles = tuple(_cube())
    tilted = analyze_geometry_proxy(triangles, OrientationSpec(45, 0, 0), max_sample_layers=128)
    assert tilted.z_height_mm == pytest.approx(10 * math.sqrt(2), abs=1e-5)
    assert tilted.xy_depth_mm == pytest.approx(10 * math.sqrt(2), abs=1e-5)
    assert tilted.max_sampled_layer_area_mm2 > 100


def test_prusa_cli_rotation_order_is_z_then_x_then_y():
    point = rotate_point_prusa_cli((1.0, 0.0, 0.0), OrientationSpec(90, 90, 90))
    # Z90: (0,1,0); X90: (0,0,1); Y90: (1,0,0)
    assert point == pytest.approx((1.0, 0.0, 0.0), abs=1e-9)


def test_ascii_parser_supports_valid_candidate_meshes():
    data = b"""solid one\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 1\nendloop\nendfacet\nendsolid one\n"""
    triangles = parse_proxy_triangles(data)
    assert triangles == (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 1.0)),)


def test_open_mesh_is_not_marked_reliable_for_auto_screening():
    triangle = (((0.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 10.0, 10.0)),)
    metrics = analyze_geometry_proxy(triangle, OrientationSpec(0, 0, 0), max_sample_layers=8)
    assert metrics.open_contour_sample_count > 0
    assert metrics.reliable_for_auto_screening is False


def test_proxy_triangle_limit_fails_closed_without_sampling_mesh_topology():
    data = _binary_stl(_cube())
    with pytest.raises(OrientationProxyError, match="limited to 10 triangles"):
        parse_proxy_triangles(data, max_triangles=10)


def test_invalid_proxy_configuration_fails_closed():
    with pytest.raises(OrientationProxyError, match="layer_height_mm"):
        analyze_geometry_proxy(tuple(_cube()), OrientationSpec(0, 0, 0), layer_height_mm=0)
    with pytest.raises(OrientationProxyError, match="max_sample_layers"):
        analyze_geometry_proxy(tuple(_cube()), OrientationSpec(0, 0, 0), max_sample_layers=True)
