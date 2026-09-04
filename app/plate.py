from __future__ import annotations

import math
from dataclasses import dataclass


MAX_INSTANCES = 1000
MAX_PLATES = 250


class PlatePlanError(ValueError):
    pass


@dataclass(frozen=True)
class Placement:
    instance_index: int
    x_mm: float
    y_mm: float
    rotation_z_deg: int


@dataclass(frozen=True)
class Plate:
    plate_index: int
    placements: tuple[Placement, ...]


@dataclass(frozen=True)
class PlatePlan:
    plate_width_mm: float
    plate_depth_mm: float
    edge_margin_mm: float
    spacing_mm: float
    instance_footprint_width_mm: float
    instance_footprint_depth_mm: float
    rotation_z_deg: int
    columns: int
    rows: int
    capacity_per_plate: int
    plates: tuple[Plate, ...]

    @property
    def quantity(self) -> int:
        return sum(len(plate.placements) for plate in self.plates)

    @property
    def plate_count(self) -> int:
        return len(self.plates)


def _positive_finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise PlatePlanError(f"{name} must be a positive finite number.")
    return value


def _nonnegative_finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise PlatePlanError(f"{name} must be a non-negative finite number.")
    return value


def _grid_capacity(
    footprint_width_mm: float,
    footprint_depth_mm: float,
    plate_width_mm: float,
    plate_depth_mm: float,
    spacing_mm: float,
    edge_margin_mm: float,
) -> tuple[int, int, int]:
    usable_width = plate_width_mm - (2 * edge_margin_mm)
    usable_depth = plate_depth_mm - (2 * edge_margin_mm)
    if usable_width <= 0 or usable_depth <= 0:
        return 0, 0, 0
    epsilon = 1e-9
    columns = max(0, math.floor((usable_width + spacing_mm + epsilon) / (footprint_width_mm + spacing_mm)))
    rows = max(0, math.floor((usable_depth + spacing_mm + epsilon) / (footprint_depth_mm + spacing_mm)))
    return columns * rows, columns, rows


def _coordinate(value: float) -> float:
    return round(value, 6)


def plan_rectangular_instances(
    *,
    footprint_width_mm: float,
    footprint_depth_mm: float,
    quantity: int,
    plate_width_mm: float,
    plate_depth_mm: float,
    spacing_mm: float = 5.0,
    edge_margin_mm: float = 3.0,
    allow_rotate_90: bool = True,
) -> PlatePlan:
    """Create a deterministic physical-plate plan from a supported-part XY envelope.

    Coordinates are instance centers in millimetres from the plate's lower-left origin.
    The planner uses a fixed row-major grid and applies the same Z rotation to every
    instance. A 90 degree rotation is chosen only when it strictly increases capacity.
    """
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not (1 <= quantity <= MAX_INSTANCES):
        raise PlatePlanError(f"quantity must be an integer between 1 and {MAX_INSTANCES}.")

    footprint_width_mm = _positive_finite("footprint_width_mm", footprint_width_mm)
    footprint_depth_mm = _positive_finite("footprint_depth_mm", footprint_depth_mm)
    plate_width_mm = _positive_finite("plate_width_mm", plate_width_mm)
    plate_depth_mm = _positive_finite("plate_depth_mm", plate_depth_mm)
    spacing_mm = _nonnegative_finite("spacing_mm", spacing_mm)
    edge_margin_mm = _nonnegative_finite("edge_margin_mm", edge_margin_mm)

    capacity, columns, rows = _grid_capacity(
        footprint_width_mm, footprint_depth_mm, plate_width_mm, plate_depth_mm, spacing_mm, edge_margin_mm
    )
    rotation_z_deg = 0
    chosen_width = footprint_width_mm
    chosen_depth = footprint_depth_mm

    if allow_rotate_90 and not math.isclose(footprint_width_mm, footprint_depth_mm):
        rotated = _grid_capacity(
            footprint_depth_mm, footprint_width_mm, plate_width_mm, plate_depth_mm, spacing_mm, edge_margin_mm
        )
        if rotated[0] > capacity:
            capacity, columns, rows = rotated
            rotation_z_deg = 90
            chosen_width, chosen_depth = footprint_depth_mm, footprint_width_mm

    if capacity <= 0:
        raise PlatePlanError("The supported-part footprint does not fit the usable build plate.")

    plate_count = math.ceil(quantity / capacity)
    if plate_count > MAX_PLATES:
        raise PlatePlanError(f"The request requires more than {MAX_PLATES} physical plates.")

    plates: list[Plate] = []
    next_instance = 1
    for plate_index in range(1, plate_count + 1):
        placements: list[Placement] = []
        for row in range(rows):
            y_mm = edge_margin_mm + (chosen_depth / 2) + row * (chosen_depth + spacing_mm)
            for column in range(columns):
                if next_instance > quantity:
                    break
                x_mm = edge_margin_mm + (chosen_width / 2) + column * (chosen_width + spacing_mm)
                placements.append(
                    Placement(
                        instance_index=next_instance,
                        x_mm=_coordinate(x_mm),
                        y_mm=_coordinate(y_mm),
                        rotation_z_deg=rotation_z_deg,
                    )
                )
                next_instance += 1
            if next_instance > quantity:
                break
        plates.append(Plate(plate_index=plate_index, placements=tuple(placements)))

    return PlatePlan(
        plate_width_mm=plate_width_mm,
        plate_depth_mm=plate_depth_mm,
        edge_margin_mm=edge_margin_mm,
        spacing_mm=spacing_mm,
        instance_footprint_width_mm=footprint_width_mm,
        instance_footprint_depth_mm=footprint_depth_mm,
        rotation_z_deg=rotation_z_deg,
        columns=columns,
        rows=rows,
        capacity_per_plate=capacity,
        plates=tuple(plates),
    )


def plate_plan_manifest(plan: PlatePlan) -> dict:
    return {
        "schema": "workpiece-resin-plate-plan-v1",
        "strategy": "deterministic-row-major",
        "artifact_rule": "one physical plate -> one review project -> one printer-native file",
        "coordinate_system": "millimetres from lower-left plate origin; placements are instance centres",
        "plate": {
            "width_mm": plan.plate_width_mm,
            "depth_mm": plan.plate_depth_mm,
            "edge_margin_mm": plan.edge_margin_mm,
        },
        "spacing_mm": plan.spacing_mm,
        "source_instance_footprint_mm": {
            "width": plan.instance_footprint_width_mm,
            "depth": plan.instance_footprint_depth_mm,
        },
        "layout": {
            "rotation_z_deg": plan.rotation_z_deg,
            "columns": plan.columns,
            "rows": plan.rows,
            "capacity_per_plate": plan.capacity_per_plate,
            "quantity": plan.quantity,
            "plate_count": plan.plate_count,
        },
        "plates": [
            {
                "plate_index": plate.plate_index,
                "placements": [
                    {
                        "instance_index": placement.instance_index,
                        "x_mm": placement.x_mm,
                        "y_mm": placement.y_mm,
                        "rotation_z_deg": placement.rotation_z_deg,
                    }
                    for placement in plate.placements
                ],
            }
            for plate in plan.plates
        ],
    }
