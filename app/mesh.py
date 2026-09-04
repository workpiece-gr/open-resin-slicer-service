from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator

from .stl import StlValidationError, validate_stl_bytes


Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]


@dataclass(frozen=True)
class MeshGeometry:
    kind: str
    triangle_count: int
    min_xyz: Vec3
    max_xyz: Vec3
    size_xyz: Vec3
    bounds_center_xyz: Vec3
    surface_area_mm2: float


def _triangle_area(v1: Vec3, v2: Vec3, v3: Vec3) -> float:
    ax, ay, az = (v2[i] - v1[i] for i in range(3))
    bx, by, bz = (v3[i] - v1[i] for i in range(3))
    cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _binary_triangles(data: bytes) -> Iterator[Triangle]:
    for offset in range(84, len(data), 50):
        values = struct.unpack_from("<12f", data, offset)
        yield (
            (float(values[3]), float(values[4]), float(values[5])),
            (float(values[6]), float(values[7]), float(values[8])),
            (float(values[9]), float(values[10]), float(values[11])),
        )


def _ascii_triangles(data: bytes) -> Iterator[Triangle]:
    vertices: list[Vec3] = []
    text = data.decode("ascii")
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if not parts or parts[0].lower() != "vertex":
            continue
        if len(parts) != 4:
            raise StlValidationError("ASCII STL vertex line must contain exactly three coordinates.")
        try:
            vertex = tuple(float(value) for value in parts[1:4])
        except ValueError as exc:
            raise StlValidationError("ASCII STL contains an invalid vertex coordinate.") from exc
        if len(vertex) != 3 or not all(math.isfinite(value) for value in vertex):
            raise StlValidationError("ASCII STL contains non-finite geometry.")
        vertices.append(vertex)
        if len(vertices) == 3:
            v1, v2, v3 = vertices
            if _triangle_area(v1, v2, v3) == 0.0:
                raise StlValidationError("ASCII STL contains a degenerate triangle.")
            yield v1, v2, v3
            vertices = []
    if vertices:
        raise StlValidationError("ASCII STL has incomplete triangle vertex data.")


def iter_stl_triangles(data: bytes, *, max_triangles: int = 2_000_000) -> Iterator[Triangle]:
    """Yield validated STL triangles without retaining the full mesh in memory."""
    info = validate_stl_bytes(data, max_triangles=max_triangles)
    if info["kind"] == "binary":
        yield from _binary_triangles(data)
    else:
        yield from _ascii_triangles(data)


def measure_stl_geometry(data: bytes, *, max_triangles: int = 2_000_000) -> MeshGeometry:
    """Measure source-space STL bounds and area; STL coordinates are treated as millimetres."""
    info = validate_stl_bytes(data, max_triangles=max_triangles)
    iterator = _binary_triangles(data) if info["kind"] == "binary" else _ascii_triangles(data)

    min_xyz = [math.inf, math.inf, math.inf]
    max_xyz = [-math.inf, -math.inf, -math.inf]
    surface_area = 0.0
    count = 0
    for triangle in iterator:
        count += 1
        v1, v2, v3 = triangle
        area = _triangle_area(v1, v2, v3)
        if not math.isfinite(area) or area <= 0:
            raise StlValidationError("STL contains invalid triangle geometry.")
        surface_area += area
        for vertex in triangle:
            for axis in range(3):
                min_xyz[axis] = min(min_xyz[axis], vertex[axis])
                max_xyz[axis] = max(max_xyz[axis], vertex[axis])

    if count != info["triangles"]:
        raise StlValidationError("STL triangle count changed during geometry extraction.")

    mins = tuple(round(value, 9) for value in min_xyz)
    maxs = tuple(round(value, 9) for value in max_xyz)
    sizes = tuple(round(maxs[i] - mins[i], 9) for i in range(3))
    center = tuple(round((mins[i] + maxs[i]) / 2.0, 9) for i in range(3))
    return MeshGeometry(
        kind=str(info["kind"]),
        triangle_count=count,
        min_xyz=mins,
        max_xyz=maxs,
        size_xyz=sizes,
        bounds_center_xyz=center,
        surface_area_mm2=round(surface_area, 9),
    )


def mesh_geometry_manifest(geometry: MeshGeometry) -> dict:
    return {
        "schema": "workpiece-stl-geometry-v1",
        "units_assumed": "mm",
        "kind": geometry.kind,
        "triangle_count": geometry.triangle_count,
        "bounds": {
            "min_xyz": list(geometry.min_xyz),
            "max_xyz": list(geometry.max_xyz),
            "size_xyz": list(geometry.size_xyz),
            "center_xyz": list(geometry.bounds_center_xyz),
        },
        "surface_area_mm2": geometry.surface_area_mm2,
        "authority": "source-space-measurement-only",
    }
