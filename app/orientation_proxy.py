from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterable

from .orientation import OrientationPlanError
from .orientation_candidates import OrientationSpec
from .stl import StlValidationError, validate_stl_bytes


MAX_PROXY_TRIANGLES = 100_000
DEFAULT_LAYER_HEIGHT_MM = 0.05
DEFAULT_MAX_SAMPLE_LAYERS = 128

Point3 = tuple[float, float, float]
Triangle = tuple[Point3, Point3, Point3]
Point2 = tuple[float, float]
Segment2 = tuple[Point2, Point2]


class OrientationProxyError(ValueError):
    pass


@dataclass(frozen=True)
class GeometryProxyMetrics:
    triangle_count: int
    sampled_layer_count: int
    full_layer_count: int
    layer_sampling_stride: int
    max_sampled_layer_area_mm2: float
    z_height_mm: float
    xy_width_mm: float
    xy_depth_mm: float
    downward_projected_area_mm2: float
    downward_support_moment_mm3: float
    open_contour_sample_count: int

    @property
    def reliable_for_auto_screening(self) -> bool:
        return self.open_contour_sample_count == 0


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationProxyError(f"{name} must be a positive finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationProxyError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(result) or result <= 0:
        raise OrientationProxyError(f"{name} must be a positive finite number.")
    return result


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrientationProxyError(f"{name} must be a positive integer.")
    return value


def parse_proxy_triangles(data: bytes, *, max_triangles: int = MAX_PROXY_TRIANGLES) -> tuple[Triangle, ...]:
    max_triangles = _positive_int("max_triangles", max_triangles)
    try:
        metadata = validate_stl_bytes(data)
    except StlValidationError as exc:
        raise OrientationProxyError(str(exc)) from exc
    triangle_count = int(metadata["triangles"])
    if triangle_count > max_triangles:
        raise OrientationProxyError(
            f"Geometry-proxy orientation is limited to {max_triangles} triangles; use sliced/manual orientation for this mesh."
        )

    triangles: list[Triangle] = []
    if metadata["kind"] == "binary":
        for offset in range(84, len(data), 50):
            values = struct.unpack_from("<12f", data, offset)
            triangles.append((
                (float(values[3]), float(values[4]), float(values[5])),
                (float(values[6]), float(values[7]), float(values[8])),
                (float(values[9]), float(values[10]), float(values[11])),
            ))
    else:
        vertices: list[Point3] = []
        for raw_line in data.decode("ascii").splitlines():
            parts = raw_line.strip().split()
            if parts and parts[0].lower() == "vertex" and len(parts) == 4:
                try:
                    point = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError as exc:
                    raise OrientationProxyError("ASCII STL contains an invalid vertex.") from exc
                if not all(math.isfinite(value) for value in point):
                    raise OrientationProxyError("ASCII STL contains non-finite geometry.")
                vertices.append(point)
        if len(vertices) != triangle_count * 3:
            raise OrientationProxyError("ASCII STL vertex parsing disagrees with structural validation.")
        triangles = [tuple(vertices[index:index + 3]) for index in range(0, len(vertices), 3)]  # type: ignore[list-item]

    if len(triangles) != triangle_count:
        raise OrientationProxyError("STL triangle parsing disagrees with structural validation.")
    return tuple(triangles)


def _rotate_z(point: Point3, angle: float) -> Point3:
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = point
    return (x * c - y * s, x * s + y * c, z)


def _rotate_x(point: Point3, angle: float) -> Point3:
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = point
    return (x, y * c - z * s, y * s + z * c)


def _rotate_y(point: Point3, angle: float) -> Point3:
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = point
    return (x * c + z * s, y, -x * s + z * c)


def _rotate_point_radians(point: Point3, z_angle: float, x_angle: float, y_angle: float) -> Point3:
    result = _rotate_z(point, z_angle)
    result = _rotate_x(result, x_angle)
    return _rotate_y(result, y_angle)


def rotate_point_prusa_cli(point: Point3, orientation: OrientationSpec) -> Point3:
    """Mirror pinned PrusaSlicer CLI transform order: Z, then X, then Y."""
    try:
        orientation.validate()
    except OrientationPlanError as exc:
        raise OrientationProxyError(str(exc)) from exc
    return _rotate_point_radians(
        point,
        math.radians(float(orientation.z_deg)),
        math.radians(float(orientation.x_deg)),
        math.radians(float(orientation.y_deg)),
    )


def _rotate_triangles(triangles: Iterable[Triangle], orientation: OrientationSpec) -> tuple[Triangle, ...]:
    try:
        orientation.validate()
    except OrientationPlanError as exc:
        raise OrientationProxyError(str(exc)) from exc
    z_angle = math.radians(float(orientation.z_deg))
    x_angle = math.radians(float(orientation.x_deg))
    y_angle = math.radians(float(orientation.y_deg))
    return tuple(
        tuple(_rotate_point_radians(point, z_angle, x_angle, y_angle) for point in triangle)  # type: ignore[arg-type]
        for triangle in triangles
    )


def _bounds(triangles: tuple[Triangle, ...]) -> tuple[float, float, float, float, float, float]:
    if not triangles:
        raise OrientationProxyError("Proxy geometry contains no triangles.")
    first = triangles[0][0]
    min_x = max_x = first[0]
    min_y = max_y = first[1]
    min_z = max_z = first[2]
    for triangle in triangles:
        for x, y, z in triangle:
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            min_z = min(min_z, z)
            max_z = max(max_z, z)
    return min_x, max_x, min_y, max_y, min_z, max_z


def _triangle_downward_signals(triangle: Triangle, min_z: float) -> tuple[float, float]:
    a, b, c = triangle
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm <= 1e-15:
        return 0.0, 0.0
    normal_z = nz / norm
    if normal_z >= 0:
        return 0.0, 0.0
    area = norm / 2.0
    projected = area * (-normal_z)
    centroid_z = (a[2] + b[2] + c[2]) / 3.0
    clearance = max(0.0, centroid_z - min_z)
    return projected, projected * clearance


def _slice_segments_at_z(triangles: tuple[Triangle, ...], z: float) -> list[Segment2]:
    segments: list[Segment2] = []

    def intersect(a: Point3, b: Point3) -> Point2 | None:
        az, bz = a[2], b[2]
        if not ((az <= z < bz) or (bz <= z < az)):
            return None
        fraction = (z - az) / (bz - az)
        return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)

    for triangle in triangles:
        hits: list[Point2] = []
        for first, second in ((0, 1), (1, 2), (2, 0)):
            point = intersect(triangle[first], triangle[second])
            if point is not None and not any(math.hypot(point[0] - old[0], point[1] - old[1]) < 1e-9 for old in hits):
                hits.append(point)
        if len(hits) == 2:
            segments.append((hits[0], hits[1]))
    return segments


def _stitch_loops(segments: list[Segment2], tolerance: float) -> tuple[list[list[Point2]], int]:
    if not segments:
        return [], 0
    used = [False] * len(segments)
    cell_size = max(tolerance, 1e-9)
    buckets: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def cell(point: Point2) -> tuple[int, int]:
        return (round(point[0] / cell_size), round(point[1] / cell_size))

    for index, segment in enumerate(segments):
        for end, point in enumerate(segment):
            buckets.setdefault(cell(point), []).append((index, end))

    def find_next(point: Point2) -> tuple[int, int] | None:
        cx, cy = cell(point)
        best: tuple[int, int] | None = None
        best_distance = math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index, end in buckets.get((cx + dx, cy + dy), ()):
                    if used[index]:
                        continue
                    candidate = segments[index][end]
                    distance = math.hypot(candidate[0] - point[0], candidate[1] - point[1])
                    if distance <= tolerance * 2.5 and distance < best_distance:
                        best = (index, end)
                        best_distance = distance
        return best

    loops: list[list[Point2]] = []
    open_chains = 0
    for seed, segment in enumerate(segments):
        if used[seed]:
            continue
        used[seed] = True
        loop = [segment[0], segment[1]]
        start = loop[0]
        current = loop[1]
        closed = False
        for _ in range(len(segments)):
            if len(loop) > 2 and math.hypot(current[0] - start[0], current[1] - start[1]) <= tolerance * 2.5:
                closed = True
                break
            next_segment = find_next(current)
            if next_segment is None:
                break
            index, matched_end = next_segment
            used[index] = True
            point = segments[index][1 - matched_end]
            loop.append(point)
            current = point
        if closed or (len(loop) > 2 and math.hypot(current[0] - start[0], current[1] - start[1]) <= tolerance * 3):
            loops.append(loop)
        else:
            open_chains += 1
    return loops, open_chains


def _polygon_area(loop: list[Point2]) -> float:
    return 0.5 * sum(
        loop[index][0] * loop[(index + 1) % len(loop)][1]
        - loop[(index + 1) % len(loop)][0] * loop[index][1]
        for index in range(len(loop))
    )


def _gross_contour_area(loops: list[list[Point2]]) -> float:
    return sum(abs(_polygon_area(loop)) for loop in loops)


def _sample_layer_indices(full_layer_count: int, max_sample_layers: int) -> tuple[int, ...]:
    if full_layer_count <= max_sample_layers:
        return tuple(range(full_layer_count))
    if max_sample_layers == 1:
        return (full_layer_count // 2,)
    return tuple(sorted({
        round(index * (full_layer_count - 1) / (max_sample_layers - 1))
        for index in range(max_sample_layers)
    }))


def analyze_geometry_proxy(
    triangles: tuple[Triangle, ...],
    orientation: OrientationSpec,
    *,
    layer_height_mm: float = DEFAULT_LAYER_HEIGHT_MM,
    max_sample_layers: int = DEFAULT_MAX_SAMPLE_LAYERS,
) -> GeometryProxyMetrics:
    if not triangles:
        raise OrientationProxyError("At least one triangle is required for orientation proxy analysis.")
    layer_height = _positive("layer_height_mm", layer_height_mm)
    max_samples = _positive_int("max_sample_layers", max_sample_layers)
    rotated = _rotate_triangles(triangles, orientation)
    min_x, max_x, min_y, max_y, min_z, max_z = _bounds(rotated)
    z_height = max_z - min_z
    if z_height <= 1e-9:
        raise OrientationProxyError("Orientation collapses to zero Z height.")

    full_layer_count = max(1, math.ceil(z_height / layer_height))
    indices = _sample_layer_indices(full_layer_count, max_samples)
    stride = max(1, math.ceil(full_layer_count / len(indices)))
    tolerance = max(1e-6, max(max_x - min_x, max_y - min_y) * 1e-6)
    max_area = 0.0
    open_samples = 0
    for index in indices:
        z = min(min_z + z_height - 1e-9, min_z + (index + 0.5) * layer_height)
        segments = _slice_segments_at_z(rotated, z)
        loops, open_chains = _stitch_loops(segments, tolerance)
        if open_chains:
            open_samples += 1
        max_area = max(max_area, _gross_contour_area(loops))

    downward_area = 0.0
    downward_moment = 0.0
    for triangle in rotated:
        projected, moment = _triangle_downward_signals(triangle, min_z)
        downward_area += projected
        downward_moment += moment

    return GeometryProxyMetrics(
        triangle_count=len(rotated),
        sampled_layer_count=len(indices),
        full_layer_count=full_layer_count,
        layer_sampling_stride=stride,
        max_sampled_layer_area_mm2=round(max_area, 6),
        z_height_mm=round(z_height, 6),
        xy_width_mm=round(max_x - min_x, 6),
        xy_depth_mm=round(max_y - min_y, 6),
        downward_projected_area_mm2=round(downward_area, 6),
        downward_support_moment_mm3=round(downward_moment, 6),
        open_contour_sample_count=open_samples,
    )
