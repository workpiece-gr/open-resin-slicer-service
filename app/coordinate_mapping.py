from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class CoordinateMappingError(ValueError):
    pass


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise CoordinateMappingError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CoordinateMappingError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise CoordinateMappingError(f"{name} must be a finite number.")
    return number


@dataclass(frozen=True)
class ManufacturingDisplayTransform:
    """Rigid 2D transform from manufacturing-envelope coordinates to display millimetres."""

    origin_display_x_mm: float
    origin_display_y_mm: float
    x_axis_display_x: float
    x_axis_display_y: float
    y_axis_display_x: float
    y_axis_display_y: float

    def __post_init__(self) -> None:
        values = (
            _finite("origin_display_x_mm", self.origin_display_x_mm),
            _finite("origin_display_y_mm", self.origin_display_y_mm),
            _finite("x_axis_display_x", self.x_axis_display_x),
            _finite("x_axis_display_y", self.x_axis_display_y),
            _finite("y_axis_display_x", self.y_axis_display_x),
            _finite("y_axis_display_y", self.y_axis_display_y),
        )
        ox, oy, xx, xy, yx, yy = values
        x_norm = math.hypot(xx, xy)
        y_norm = math.hypot(yx, yy)
        dot = xx * yx + xy * yy
        determinant = xx * yy - xy * yx
        tolerance = 1e-6
        if not math.isclose(x_norm, 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise CoordinateMappingError("Manufacturing X axis must be a unit vector in display coordinates.")
        if not math.isclose(y_norm, 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise CoordinateMappingError("Manufacturing Y axis must be a unit vector in display coordinates.")
        if not math.isclose(dot, 0.0, rel_tol=0.0, abs_tol=tolerance):
            raise CoordinateMappingError("Manufacturing display axes must be perpendicular.")
        if not math.isclose(abs(determinant), 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise CoordinateMappingError("Manufacturing display transform must preserve millimetre scale.")
        object.__setattr__(self, "origin_display_x_mm", ox)
        object.__setattr__(self, "origin_display_y_mm", oy)
        object.__setattr__(self, "x_axis_display_x", xx)
        object.__setattr__(self, "x_axis_display_y", xy)
        object.__setattr__(self, "y_axis_display_x", yx)
        object.__setattr__(self, "y_axis_display_y", yy)

    @classmethod
    def from_mapping(cls, value: object) -> "ManufacturingDisplayTransform":
        if not isinstance(value, Mapping):
            raise CoordinateMappingError("manufacturing_to_display_transform must be an object.")
        required = {
            "origin_display_x_mm",
            "origin_display_y_mm",
            "x_axis_display_x",
            "x_axis_display_y",
            "y_axis_display_x",
            "y_axis_display_y",
        }
        if set(value) != required:
            missing = sorted(required - set(value))
            extra = sorted(set(value) - required)
            raise CoordinateMappingError(
                "manufacturing_to_display_transform must contain exactly the validated rigid-transform fields; "
                f"missing={missing}, extra={extra}."
            )
        return cls(**{name: value[name] for name in required})

    @property
    def determinant(self) -> float:
        return (
            self.x_axis_display_x * self.y_axis_display_y
            - self.x_axis_display_y * self.y_axis_display_x
        )

    def to_display(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        x = _finite("manufacturing x_mm", x_mm)
        y = _finite("manufacturing y_mm", y_mm)
        return (
            self.origin_display_x_mm + x * self.x_axis_display_x + y * self.y_axis_display_x,
            self.origin_display_y_mm + x * self.x_axis_display_y + y * self.y_axis_display_y,
        )

    def to_manufacturing(self, display_x_mm: float, display_y_mm: float) -> tuple[float, float]:
        dx = _finite("display x_mm", display_x_mm) - self.origin_display_x_mm
        dy = _finite("display y_mm", display_y_mm) - self.origin_display_y_mm
        # The linear portion is orthonormal, so its inverse is its transpose.
        return (
            dx * self.x_axis_display_x + dy * self.x_axis_display_y,
            dx * self.y_axis_display_x + dy * self.y_axis_display_y,
        )

    def display_bounds_to_manufacturing_bounds(
        self,
        *,
        min_display_x_mm: float,
        max_display_x_mm: float,
        min_display_y_mm: float,
        max_display_y_mm: float,
    ) -> tuple[float, float, float, float]:
        """Return the conservative manufacturing-axis bounds of a display-space rectangle."""
        min_x = _finite("min_display_x_mm", min_display_x_mm)
        max_x = _finite("max_display_x_mm", max_display_x_mm)
        min_y = _finite("min_display_y_mm", min_display_y_mm)
        max_y = _finite("max_display_y_mm", max_display_y_mm)
        if max_x <= min_x or max_y <= min_y:
            raise CoordinateMappingError("Display bounds must have strictly positive width and depth.")
        corners = tuple(
            self.to_manufacturing(x, y)
            for x, y in (
                (min_x, min_y),
                (max_x, min_y),
                (min_x, max_y),
                (max_x, max_y),
            )
        )
        xs = [item[0] for item in corners]
        ys = [item[1] for item in corners]
        return (min(xs), max(xs), min(ys), max(ys))

    def display_rotation_for_manufacturing(self, rotation_z_deg: int) -> int:
        """Map Workpiece's physical 0/+90 degree rotation into display coordinates.

        A reflected coordinate transform reverses handedness, so manufacturing +90 degrees
        is display -90 degrees. Proper rotations preserve the sign.
        """
        if isinstance(rotation_z_deg, bool) or rotation_z_deg not in {0, 90}:
            raise CoordinateMappingError("Manufacturing plate rotation must be 0 or 90 degrees.")
        if rotation_z_deg == 0:
            return 0
        return 90 if self.determinant > 0 else -90

    def validate_envelope_inside_display(
        self,
        *,
        width_mm: float,
        depth_mm: float,
        display_width_mm: float,
        display_height_mm: float,
        tolerance_mm: float = 1e-6,
    ) -> None:
        width = _finite("manufacturing envelope width_mm", width_mm)
        depth = _finite("manufacturing envelope depth_mm", depth_mm)
        display_width = _finite("display width_mm", display_width_mm)
        display_height = _finite("display height_mm", display_height_mm)
        if width <= 0 or depth <= 0 or display_width <= 0 or display_height <= 0:
            raise CoordinateMappingError("Manufacturing and display dimensions must be positive.")
        for x, y in ((0.0, 0.0), (width, 0.0), (0.0, depth), (width, depth)):
            display_x, display_y = self.to_display(x, y)
            if (
                display_x < -tolerance_mm
                or display_x > display_width + tolerance_mm
                or display_y < -tolerance_mm
                or display_y > display_height + tolerance_mm
            ):
                raise CoordinateMappingError(
                    "Validated manufacturing-envelope transform maps a physical envelope corner outside the printer display."
                )

    def manifest(self) -> dict[str, float]:
        return {
            "origin_display_x_mm": self.origin_display_x_mm,
            "origin_display_y_mm": self.origin_display_y_mm,
            "x_axis_display_x": self.x_axis_display_x,
            "x_axis_display_y": self.x_axis_display_y,
            "y_axis_display_x": self.y_axis_display_x,
            "y_axis_display_y": self.y_axis_display_y,
        }
