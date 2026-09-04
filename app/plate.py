from __future__ import annotations

import math
from dataclasses import dataclass

from app.profiles import ProfileRegistry


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

    @property
    def placed_footprint_width_mm(self) -> float:
        return self.instance_footprint_depth_mm if self.rotation_z_deg == 90 else self.instance_footprint_width_mm

    @property
    def placed_footprint_depth_mm(self) -> float:
        return self.instance_footprint_width_mm if self.rotation_z_deg == 90 else self.instance_footprint_depth_mm


@dataclass(frozen=True)
class PrinterPlatePlan:
    printer_profile_id: str
    manufacturing_envelope_coordinate_mapping: str
    plan: PlatePlan


def _positive_finite(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise PlatePlanError(f"{name} must be a positive finite number.")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise PlatePlanError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(value) or value <= 0:
        raise PlatePlanError(f"{name} must be a positive finite number.")
    return value


def _nonnegative_finite(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise PlatePlanError(f"{name} must be a non-negative finite number.")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise PlatePlanError(f"{name} must be a non-negative finite number.") from exc
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

    ``plate_width_mm`` and ``plate_depth_mm`` must describe the validated printable
    manufacturing envelope, not merely the LCD/display dimensions.

    Coordinates are target centres of the final supported-part XY envelope, measured
    in millimetres from the plate's lower-left origin. They are not raw mesh-origin
    translations: slicer integration must derive the real object transform from the
    oriented/supported envelope and verify the resulting bounds after applying it.

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


def plan_printer_profile_instances(
    *,
    registry: ProfileRegistry,
    printer_profile_id: str,
    footprint_width_mm: float,
    footprint_depth_mm: float,
    quantity: int,
    spacing_mm: float = 5.0,
    edge_margin_mm: float = 3.0,
    allow_rotate_90: bool = True,
) -> PrinterPlatePlan:
    """Plan against the printer registry's manufacturing envelope.

    This is the integration entry point. It deliberately does not accept raw plate
    dimensions, so callers cannot accidentally substitute LCD/display dimensions.
    The envelope's coordinate mapping may remain unverified for candidate planning;
    physical 3MF placement must still fail closed until that mapping is validated.
    """
    envelope = registry.printer_manufacturing_envelope(printer_profile_id)
    plan = plan_rectangular_instances(
        footprint_width_mm=footprint_width_mm,
        footprint_depth_mm=footprint_depth_mm,
        quantity=quantity,
        plate_width_mm=envelope.width_mm,
        plate_depth_mm=envelope.depth_mm,
        spacing_mm=spacing_mm,
        edge_margin_mm=edge_margin_mm,
        allow_rotate_90=allow_rotate_90,
    )
    return PrinterPlatePlan(
        printer_profile_id=printer_profile_id,
        manufacturing_envelope_coordinate_mapping=envelope.coordinate_mapping,
        plan=plan,
    )


def plate_plan_manifest(plan: PlatePlan) -> dict:
    return {
        "schema": "workpiece-resin-plate-plan-v1",
        "strategy": "deterministic-row-major",
        "artifact_rule": "one physical plate -> one review project -> one printer-native file",
        "coordinate_system": "millimetres from lower-left plate origin; placements are target envelope centres",
        "placement_semantics": (
            "x_mm/y_mm are target centres of the final supported XY envelope, not raw mesh-origin translations; "
            "the slicer integration must derive and verify the actual object transform"
        ),
        "plate_envelope_rule": (
            "plate dimensions must describe a validated printable manufacturing envelope, not raw LCD/display dimensions"
        ),
        "plate": {
            "width_mm": plan.plate_width_mm,
            "depth_mm": plan.plate_depth_mm,
            "edge_margin_mm": plan.edge_margin_mm,
        },
        "spacing_mm": plan.spacing_mm,
        "source_instance_footprint_mm": {
            "width": plan.instance_footprint_width_mm,
            "depth": plan.instance_footprint_depth_mm,
            "semantics": "final supported XY envelope before the optional common 90-degree plate rotation",
        },
        "placed_instance_footprint_mm": {
            "width": plan.placed_footprint_width_mm,
            "depth": plan.placed_footprint_depth_mm,
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


def printer_plate_plan_manifest(profile_plan: PrinterPlatePlan) -> dict:
    manifest = plate_plan_manifest(profile_plan.plan)
    manifest["printer_profile_id"] = profile_plan.printer_profile_id
    manifest["manufacturing_envelope_coordinate_mapping"] = (
        profile_plan.manufacturing_envelope_coordinate_mapping
    )
    manifest["automatic_materialization_authority"] = (
        profile_plan.manufacturing_envelope_coordinate_mapping == "validated"
    )
    return manifest
