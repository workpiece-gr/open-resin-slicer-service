from __future__ import annotations

import math
import struct


class StlValidationError(ValueError):
    pass


def validate_stl_bytes(data: bytes, *, max_triangles: int = 2_000_000) -> dict[str, int | str]:
    """Bounded structural STL validation before invoking external slicers."""
    if len(data) < 15:
        raise StlValidationError("STL is too small to be valid.")

    # Binary STL: 80-byte header + uint32 count + 50 bytes per triangle.
    if len(data) >= 84:
        triangle_count = struct.unpack_from("<I", data, 80)[0]
        expected = 84 + triangle_count * 50
        if triangle_count and triangle_count <= max_triangles and expected == len(data):
            for offset in range(84, len(data), 50):
                values = struct.unpack_from("<12f", data, offset)
                if not all(math.isfinite(v) for v in values):
                    raise StlValidationError("Binary STL contains non-finite geometry.")
                v1, v2, v3 = values[3:6], values[6:9], values[9:12]
                ax, ay, az = (v2[i] - v1[i] for i in range(3))
                bx, by, bz = (v3[i] - v1[i] for i in range(3))
                cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
                if cx == 0.0 and cy == 0.0 and cz == 0.0:
                    raise StlValidationError("Binary STL contains a degenerate triangle.")
            return {"kind": "binary", "triangles": triangle_count}

    # Conservative ASCII acceptance. Full geometry validation remains delegated to the engine.
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StlValidationError("STL is neither a structurally valid binary STL nor ASCII STL.") from exc
    stripped = text.lstrip()
    if not stripped.lower().startswith("solid"):
        raise StlValidationError("ASCII STL must begin with 'solid'.")
    lower = stripped.lower()
    facets = lower.count("facet normal")
    vertices = lower.count("vertex")
    if facets < 1 or vertices != facets * 3:
        raise StlValidationError("ASCII STL has incomplete facet/vertex structure.")
    if facets > max_triangles:
        raise StlValidationError("STL exceeds the triangle limit.")
    # Reject obvious non-finite tokens before handing off to PrusaSlicer.
    tokens = lower.replace("\n", " ").split()
    if any(token in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"} for token in tokens):
        raise StlValidationError("ASCII STL contains non-finite geometry.")
    return {"kind": "ascii", "triangles": facets}
