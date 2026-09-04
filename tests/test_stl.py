import struct

import pytest

from app.stl import StlValidationError, validate_stl_bytes


def triangle_binary(vertices=((0, 0, 0), (1, 0, 0), (0, 1, 0))):
    header = b"workpiece".ljust(80, b"\0")
    values = (0.0, 0.0, 1.0, *vertices[0], *vertices[1], *vertices[2])
    return header + struct.pack("<I", 1) + struct.pack("<12fH", *values, 0)


def test_valid_binary_stl():
    assert validate_stl_bytes(triangle_binary()) == {"kind": "binary", "triangles": 1}


def test_rejects_truncated_binary_stl():
    with pytest.raises(StlValidationError):
        validate_stl_bytes(triangle_binary()[:-2])


def test_rejects_degenerate_binary_stl():
    with pytest.raises(StlValidationError, match="degenerate"):
        validate_stl_bytes(triangle_binary(((0, 0, 0), (1, 0, 0), (2, 0, 0))))


def test_valid_ascii_stl():
    data = b"""solid x
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
endsolid x
"""
    assert validate_stl_bytes(data) == {"kind": "ascii", "triangles": 1}
