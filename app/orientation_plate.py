from __future__ import annotations

import re
from dataclasses import dataclass

from .coordinate_mapping import CoordinateMappingError
from .native_envelope import NativeEnvelopeError, NativeEnvelopeEvidence
from .orientation_sliced import SlicedOrientationValidation
from .placement import Envelope2D
from .plate import (
    PrinterPlatePlan,
    plan_printer_profile_instances,
    printer_plate_plan_manifest,
)
from .profiles import ProfileError, ProfileRegistry


ORIENTATION_PLATE_SCHEMA = "workpiece-resin-orientation-plate-plan-v3"
MANUFACTURING_ENVELOPE_COORDINATE_SPACE = "workpiece-manufacturing-envelope-millimetres"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OrientationPlatePlanError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise OrientationPlatePlanError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


@dataclass(frozen=True)
class SelectedOrientationPlatePlan:
    orientation_deg: tuple[float, float, float]
    source_sha256: str
    review_project_sha256: str
    effective_config_sha256: str
    intermediate_sha256: str
    native_sha256: str
    pretranslation_envelope: Envelope2D
    printer_plate_plan: PrinterPlatePlan
    pretranslation_coordinate_space: str = "legacy-unverified"
    native_display_envelope: Envelope2D | None = None


def plan_selected_sliced_orientation(
    *,
    registry: ProfileRegistry,
    printer_profile_id: str,
    sliced_validation: SlicedOrientationValidation,
    native_envelope: NativeEnvelopeEvidence,
    quantity: int,
    spacing_mm: float = 5.0,
    edge_margin_mm: float = 3.0,
    allow_rotate_90: bool = True,
) -> SelectedOrientationPlatePlan:
    """Pack the exact selected CTB's supported/padded envelope conservatively.

    The CTB rectangle is always retained verbatim in display coordinates. If the printer
    profile has a physically validated manufacturing-to-display transform, its four
    rectangle corners are mapped back into manufacturing coordinates and their
    axis-aligned bounds drive packing. Otherwise only display-space width/depth may be
    used for acceptance-candidate planning and automatic materialization remains blocked.
    """
    selected = sliced_validation.selected_evidence
    if selected is None:
        raise OrientationPlatePlanError(
            "Sliced orientation validation has no selected finalist; plate planning requires manual review."
        )

    try:
        native_envelope.validate()
    except NativeEnvelopeError as exc:
        raise OrientationPlatePlanError(str(exc)) from exc

    if native_envelope.printer_profile_id != printer_profile_id:
        raise OrientationPlatePlanError(
            "Native envelope printer profile does not match the requested printer-backed plate plan."
        )

    selected_native_hash = _sha256("selected native_sha256", selected.native_sha256)
    envelope_native_hash = _sha256(
        "native envelope printer_native_sha256",
        native_envelope.printer_native_sha256,
    )
    if envelope_native_hash != selected_native_hash:
        raise OrientationPlatePlanError(
            "Native envelope is not bound to the exact selected printer-native artifact."
        )

    source_hash = _sha256("source_sha256", sliced_validation.source_sha256)
    selected_source_hash = _sha256("selected source_sha256", selected.source_sha256)
    if source_hash != selected_source_hash:
        raise OrientationPlatePlanError(
            "Selected sliced evidence source hash does not match its validation bundle."
        )

    native_display_envelope = native_envelope.envelope
    packing_envelope = native_display_envelope
    packing_coordinate_space = native_envelope.coordinate_space
    try:
        printer_envelope = registry.printer_manufacturing_envelope(printer_profile_id)
        if printer_envelope.coordinate_mapping == "validated":
            transform = registry.printer_manufacturing_display_transform(printer_profile_id)
            bounds = transform.display_bounds_to_manufacturing_bounds(
                min_display_x_mm=native_display_envelope.min_x_mm,
                max_display_x_mm=native_display_envelope.max_x_mm,
                min_display_y_mm=native_display_envelope.min_y_mm,
                max_display_y_mm=native_display_envelope.max_y_mm,
            )
            packing_envelope = Envelope2D(
                min_x_mm=bounds[0],
                max_x_mm=bounds[1],
                min_y_mm=bounds[2],
                max_y_mm=bounds[3],
            )
            packing_coordinate_space = MANUFACTURING_ENVELOPE_COORDINATE_SPACE
    except (ProfileError, CoordinateMappingError) as exc:
        raise OrientationPlatePlanError(str(exc)) from exc

    profile_plan = plan_printer_profile_instances(
        registry=registry,
        printer_profile_id=printer_profile_id,
        footprint_width_mm=packing_envelope.width_mm,
        footprint_depth_mm=packing_envelope.depth_mm,
        quantity=quantity,
        spacing_mm=spacing_mm,
        edge_margin_mm=edge_margin_mm,
        allow_rotate_90=allow_rotate_90,
    )
    return SelectedOrientationPlatePlan(
        orientation_deg=selected.canonical_key,
        source_sha256=source_hash,
        review_project_sha256=_sha256(
            "selected review_project_sha256", selected.review_project_sha256
        ),
        effective_config_sha256=_sha256(
            "selected effective_config_sha256", selected.effective_config_sha256
        ),
        intermediate_sha256=_sha256(
            "selected intermediate_sha256", selected.intermediate_sha256
        ),
        native_sha256=selected_native_hash,
        pretranslation_envelope=packing_envelope,
        printer_plate_plan=profile_plan,
        pretranslation_coordinate_space=packing_coordinate_space,
        native_display_envelope=native_display_envelope,
    )


def orientation_plate_plan_manifest(result: SelectedOrientationPlatePlan) -> dict:
    envelope = result.pretranslation_envelope
    plate_manifest = printer_plate_plan_manifest(result.printer_plate_plan)
    coordinate_ready = (
        result.pretranslation_coordinate_space == MANUFACTURING_ENVELOPE_COORDINATE_SPACE
    )
    native_display = result.native_display_envelope
    return {
        "schema": ORIENTATION_PLATE_SCHEMA,
        "automatic_materialization_authority": bool(
            plate_manifest["automatic_materialization_authority"] and coordinate_ready
        ),
        "source_sha256": _sha256("source_sha256", result.source_sha256),
        "selected_orientation_deg": {
            "x": result.orientation_deg[0],
            "y": result.orientation_deg[1],
            "z": result.orientation_deg[2],
        },
        "selected_review_3mf_sha256": _sha256(
            "review_project_sha256", result.review_project_sha256
        ),
        "selected_sliced_artifacts": {
            "review_3mf_sha256": _sha256(
                "review_project_sha256", result.review_project_sha256
            ),
            "effective_config_sha256": _sha256(
                "effective_config_sha256", result.effective_config_sha256
            ),
            "intermediate_sl1_sha256": _sha256(
                "intermediate_sha256", result.intermediate_sha256
            ),
            "printer_native_sha256": _sha256("native_sha256", result.native_sha256),
        },
        "native_display_envelope_mm": (
            None
            if native_display is None
            else {
                "min_x": native_display.min_x_mm,
                "max_x": native_display.max_x_mm,
                "min_y": native_display.min_y_mm,
                "max_y": native_display.max_y_mm,
                "width": native_display.width_mm,
                "depth": native_display.depth_mm,
                "coordinate_space": "uvtools-native-display-millimetres",
                "source": "exact-selected-printer-native-bounding-rectangle",
            }
        ),
        "supported_pretranslation_envelope_mm": {
            "min_x": envelope.min_x_mm,
            "max_x": envelope.max_x_mm,
            "min_y": envelope.min_y_mm,
            "max_y": envelope.max_y_mm,
            "width": envelope.width_mm,
            "depth": envelope.depth_mm,
            "coordinate_space": result.pretranslation_coordinate_space,
            "source": (
                "exact-selected-printer-native-bounding-rectangle-mapped-by-validated-transform"
                if coordinate_ready
                else "exact-selected-printer-native-bounding-rectangle"
            ),
        },
        "plate_plan": plate_manifest,
        "review_rule": (
            "The exact selected printer-native bounding rectangle is retained in UVtools display coordinates. "
            "When a physically validated manufacturing-to-display transform exists, its mapped manufacturing-axis bounds drive packing and may authorize deterministic placement; otherwise only native width/depth support acceptance-candidate packing and physical placement stays blocked."
        ),
    }
