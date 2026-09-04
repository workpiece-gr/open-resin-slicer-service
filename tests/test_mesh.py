import struct

import pytest

from app.mesh import iter_stl_triangles, measure_stl_geometry, mesh_geometry_manifest
from app.stl import StlValidationError


def _binary_stl(triangles):
    payload = bytearray(b"workpiece" + b"\0" * (80 - len("workpiece")))
    payload.extend(struct.pack("<I", len(triangles)))
    for v1, v2, v3 in triangles:
        payload.extend(struct.pack("<12fH", 0, 0, 1, *v1, *v2, *v3, 0))
    return bytes(payload)


def _box_triangles(minimum=(10, 20, 30), maximum=(12, 23, 34)):
    x0, y0, z0 = minimum
    x1, y1, z1 = maximum
    v = {
        "000": (x0, y0, z0), "100": (x1, y0, z0), "010": (x0, y1, z0), "110": (x1, y1, z0),
        "001": (x0, y0, z1), "101": (x1, y0, z1), "011": (x0, y1, z1), "111": (x1, y1, z1),
    }
    return [
        (v["000"], v["110"], v["100"]), (v["000"], v["010"], v["110"]),
        (v["001"], v["101"], v["111"]), (v["001"], v["111"], v["011"]),
        (v["000"], v["100"], v["101"]), (v["000"], v["101"], v["001"]),
        (v["010"], v["011"], v["111"]), (v["010"], v["111"], v["110"]),
        (v["000"], v["001"], v["011"]), (v["000"], v["011"], v["010"]),
        (v["100"], v["110"], v["111"]), (v["100"], v["111"], v["101"]),
    ]


def test_binary_measurement_preserves_arbitrary_source_origin():
    geometry = measure_stl_geometry(_binary_stl(_box_triangles()))
    assert geometry.kind == "binary"
    assert geometry.triangle_count == 12
    assert geometry.min_xyz == (10.0, 20.0, 30.0)
    assert geometry.max_xyz == (12.0, 23.0, 34.0)
    assert geometry.size_xyz == (2.0, 3.0, 4.0)
    assert geometry.bounds_center_xyz == (11.0, 21.5, 32.0)
    assert geometry.surface_area_mm2 == 52.0


def test_ascii_measurement_parses_scientific_notation_and_area():
    data = b"""solid t
facet normal 0 0 1
 outer loop
  vertex 1e0 2 3
  vertex 3 2 3
  vertex 1 5 3
 endloop
endfacet
endsolid t
"""
    geometry = measure_stl_geometry(data)
    assert geometry.kind == "ascii"
    assert geometry.min_xyz == (1.0, 2.0, 3.0)
    assert geometry.max_xyz == (3.0, 5.0, 3.0)
    assert geometry.surface_area_mm2 == 3.0


def test_ascii_nonfinite_number_created_by_float_overflow_is_rejected():
    data = b"""solid t
facet normal 0 0 1
 outer loop
  vertex 1e999 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid t
"""
    with pytest.raises(StlValidationError, match="non-finite"):
        measure_stl_geometry(data)


def test_streaming_iterator_yields_exact_validated_triangle_count():
    data = _binary_stl(_box_triangles())
    assert len(list(iter_stl_triangles(data))) == 12


def test_triangle_limit_is_preserved_during_measurement():
    data = _binary_stl(_box_triangles())
    with pytest.raises(StlValidationError):
        measure_stl_geometry(data, max_triangles=10)


def test_manifest_keeps_source_space_semantics_explicit():
    manifest = mesh_geometry_manifest(measure_stl_geometry(_binary_stl(_box_triangles())))
    assert manifest["schema"] == "workpiece-stl-geometry-v1"
    assert manifest["units_assumed"] == "mm"
    assert manifest["bounds"]["center_xyz"] == [11.0, 21.5, 32.0]
    assert manifest["authority"] == "source-space-measurement-only"
