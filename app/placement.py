from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from app.plate import PlatePlan


class PlacementError(ValueError):
    pass


@dataclass(frozen=True)
class Envelope2D:
    min_x_mm: float
    max_x_mm: float
    min_y_mm: float
    max_y_mm: float

    def __post_init__(self) -> None:
        values = (self.min_x_mm, self.max_x_mm, self.min_y_mm, self.max_y_mm)
        if any(isinstance(value, bool) for value in values):
            raise PlacementError("Envelope bounds must be finite numbers.")
        try:
            numeric = tuple(float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise PlacementError("Envelope bounds must be finite numbers.") from exc
        if not all(math.isfinite(value) for value in numeric):
            raise PlacementError("Envelope bounds must be finite numbers.")
        if numeric[1] <= numeric[0] or numeric[3] <= numeric[2]:
            raise PlacementError("Envelope maximums must be strictly greater than minimums.")
        object.__setattr__(self, "min_x_mm", numeric[0])
        object.__setattr__(self, "max_x_mm", numeric[1])
        object.__setattr__(self, "min_y_mm", numeric[2])
        object.__setattr__(self, "max_y_mm", numeric[3])

    @property
    def width_mm(self) -> float:
        return self.max_x_mm - self.min_x_mm

    @property
    def depth_mm(self) -> float:
        return self.max_y_mm - self.min_y_mm

    @property
    def center_x_mm(self) -> float:
        return (self.min_x_mm + self.max_x_mm) / 2

    @property
    def center_y_mm(self) -> float:
        return (self.min_y_mm + self.max_y_mm) / 2

    def translated(self, dx_mm: float, dy_mm: float) -> "Envelope2D":
        return Envelope2D(
            min_x_mm=self.min_x_mm + dx_mm,
            max_x_mm=self.max_x_mm + dx_mm,
            min_y_mm=self.min_y_mm + dy_mm,
            max_y_mm=self.max_y_mm + dy_mm,
        )


@dataclass(frozen=True)
class InstanceTranslation:
    instance_index: int
    target_x_mm: float
    target_y_mm: float
    translate_x_mm: float
    translate_y_mm: float
    rotation_z_deg: int


def _close(a: float, b: float, tolerance_mm: float) -> bool:
    return abs(a - b) <= tolerance_mm


def expected_instance_envelope(
    pretranslation_envelope: Envelope2D,
    translation: InstanceTranslation,
) -> Envelope2D:
    """Predict the slot envelope under Workpiece's explicit common-rotation contract.

    A common 90-degree plate rotation is defined around the exact pretranslation
    supported/padded envelope centre, then that centre is translated to the target
    planner centre. This definition avoids depending on any hidden PrusaSlicer pivot.
    The real materializer must compose its 3MF instance transform to implement this
    world-space operation and must still re-extract the exact final envelope afterwards.
    """
    if translation.rotation_z_deg == 0:
        return pretranslation_envelope.translated(
            translation.translate_x_mm,
            translation.translate_y_mm,
        )
    if translation.rotation_z_deg != 90:
        raise PlacementError("Only common 0 or 90 degree plate rotations are supported.")
    half_width = pretranslation_envelope.depth_mm / 2
    half_depth = pretranslation_envelope.width_mm / 2
    return Envelope2D(
        min_x_mm=translation.target_x_mm - half_width,
        max_x_mm=translation.target_x_mm + half_width,
        min_y_mm=translation.target_y_mm - half_depth,
        max_y_mm=translation.target_y_mm + half_depth,
    )


def derive_plate_translations(
    plan: PlatePlan,
    *,
    plate_index: int,
    pretranslation_envelope: Envelope2D,
    tolerance_mm: float = 1e-6,
) -> tuple[InstanceTranslation, ...]:
    """Derive XY translations from the actual pre-translation supported envelope.

    ``pretranslation_envelope`` represents the selected supported/padded artifact before
    the plate planner's optional common Z rotation. If the plan selects 90 degrees, the
    materializer must rotate around this envelope's centre before translating that centre
    to ``target_x_mm``/``target_y_mm``. This function intentionally makes no assumption
    about STL origin or PrusaSlicer rotation pivot.
    """
    if isinstance(plate_index, bool) or not isinstance(plate_index, int):
        raise PlacementError("plate_index must be an integer.")
    if isinstance(tolerance_mm, bool):
        raise PlacementError("tolerance_mm must be a non-negative finite number.")
    try:
        tolerance_mm = float(tolerance_mm)
    except (TypeError, ValueError) as exc:
        raise PlacementError("tolerance_mm must be a non-negative finite number.") from exc
    if not math.isfinite(tolerance_mm) or tolerance_mm < 0:
        raise PlacementError("tolerance_mm must be a non-negative finite number.")

    try:
        plate = next(item for item in plan.plates if item.plate_index == plate_index)
    except StopIteration as exc:
        raise PlacementError(f"Unknown plate_index {plate_index}.") from exc

    if not _close(pretranslation_envelope.width_mm, plan.instance_footprint_width_mm, tolerance_mm):
        raise PlacementError(
            "Pre-translation envelope width differs from the source footprint used for plate planning; replan required."
        )
    if not _close(pretranslation_envelope.depth_mm, plan.instance_footprint_depth_mm, tolerance_mm):
        raise PlacementError(
            "Pre-translation envelope depth differs from the source footprint used for plate planning; replan required."
        )

    return tuple(
        InstanceTranslation(
            instance_index=placement.instance_index,
            target_x_mm=placement.x_mm,
            target_y_mm=placement.y_mm,
            translate_x_mm=placement.x_mm - pretranslation_envelope.center_x_mm,
            translate_y_mm=placement.y_mm - pretranslation_envelope.center_y_mm,
            rotation_z_deg=placement.rotation_z_deg,
        )
        for placement in plate.placements
    )


def validate_materialized_plate(
    plan: PlatePlan,
    *,
    plate_index: int,
    materialized_envelopes: Mapping[int, Envelope2D],
    tolerance_mm: float = 1e-6,
) -> None:
    """Fail closed unless final materialized envelopes still fit the planned slots.

    This validation is intended to run after supports, pads and transforms have been
    materialized in the exact per-plate 3MF. Each instance must remain inside its
    planned footprint slot, inside the manufacturing envelope margins, and separated
    from all other final envelopes by at least the requested spacing.
    """
    if isinstance(plate_index, bool) or not isinstance(plate_index, int):
        raise PlacementError("plate_index must be an integer.")
    if isinstance(tolerance_mm, bool):
        raise PlacementError("tolerance_mm must be a non-negative finite number.")
    try:
        tolerance_mm = float(tolerance_mm)
    except (TypeError, ValueError) as exc:
        raise PlacementError("tolerance_mm must be a non-negative finite number.") from exc
    if not math.isfinite(tolerance_mm) or tolerance_mm < 0:
        raise PlacementError("tolerance_mm must be a non-negative finite number.")

    try:
        plate = next(item for item in plan.plates if item.plate_index == plate_index)
    except StopIteration as exc:
        raise PlacementError(f"Unknown plate_index {plate_index}.") from exc

    expected_indices = {placement.instance_index for placement in plate.placements}
    actual_indices = set(materialized_envelopes)
    if actual_indices != expected_indices:
        missing = sorted(expected_indices - actual_indices)
        extra = sorted(actual_indices - expected_indices)
        raise PlacementError(
            f"Materialized instance set does not match plate plan; missing={missing}, extra={extra}."
        )

    half_width = plan.placed_footprint_width_mm / 2
    half_depth = plan.placed_footprint_depth_mm / 2
    min_plate_x = plan.edge_margin_mm
    max_plate_x = plan.plate_width_mm - plan.edge_margin_mm
    min_plate_y = plan.edge_margin_mm
    max_plate_y = plan.plate_depth_mm - plan.edge_margin_mm

    ordered: list[tuple[int, Envelope2D]] = []
    for placement in plate.placements:
        envelope = materialized_envelopes[placement.instance_index]
        slot_min_x = placement.x_mm - half_width
        slot_max_x = placement.x_mm + half_width
        slot_min_y = placement.y_mm - half_depth
        slot_max_y = placement.y_mm + half_depth

        if (
            envelope.min_x_mm < slot_min_x - tolerance_mm
            or envelope.max_x_mm > slot_max_x + tolerance_mm
            or envelope.min_y_mm < slot_min_y - tolerance_mm
            or envelope.max_y_mm > slot_max_y + tolerance_mm
        ):
            raise PlacementError(
                f"Materialized envelope for instance {placement.instance_index} escaped its planned footprint slot; replan required."
            )
        if (
            envelope.min_x_mm < min_plate_x - tolerance_mm
            or envelope.max_x_mm > max_plate_x + tolerance_mm
            or envelope.min_y_mm < min_plate_y - tolerance_mm
            or envelope.max_y_mm > max_plate_y + tolerance_mm
        ):
            raise PlacementError(
                f"Materialized envelope for instance {placement.instance_index} violates the manufacturing-envelope margin."
            )
        ordered.append((placement.instance_index, envelope))

    for index, (a_index, a) in enumerate(ordered):
        for b_index, b in ordered[index + 1:]:
            separated = (
                a.max_x_mm + plan.spacing_mm <= b.min_x_mm + tolerance_mm
                or b.max_x_mm + plan.spacing_mm <= a.min_x_mm + tolerance_mm
                or a.max_y_mm + plan.spacing_mm <= b.min_y_mm + tolerance_mm
                or b.max_y_mm + plan.spacing_mm <= a.min_y_mm + tolerance_mm
            )
            if not separated:
                raise PlacementError(
                    f"Materialized envelopes for instances {a_index} and {b_index} violate required spacing."
                )
