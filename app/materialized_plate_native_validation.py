from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from .materialization_selected import SelectedPlateProjectMaterialization
from .materialized_plate_execution import SelectedMaterializedPlateNativeExecution
from .placement import Envelope2D
from .profiles import Profile


WHOLE_PLATE_NATIVE_SCHEMA = "workpiece-resin-whole-plate-native-envelope-v1"


class MaterializedPlateNativeValidationError(ValueError):
    pass


def _positive_profile_number(printer: Profile, key: str) -> float:
    raw = printer.metadata.get(key)
    if isinstance(raw, bool):
        raise MaterializedPlateNativeValidationError(
            f"Printer profile {printer.id} requires positive finite {key}."
        )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise MaterializedPlateNativeValidationError(
            f"Printer profile {printer.id} requires positive finite {key}."
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise MaterializedPlateNativeValidationError(
            f"Printer profile {printer.id} requires positive finite {key}."
        )
    return value


def _positive_profile_int(printer: Profile, key: str) -> int:
    raw = printer.metadata.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise MaterializedPlateNativeValidationError(
            f"Printer profile {printer.id} requires positive integer {key}."
        )
    return raw


def _expected_display_envelope(
    materialization: SelectedPlateProjectMaterialization,
) -> Envelope2D:
    source = materialization.source_display_envelope
    placements = materialization.display_placements
    if not placements:
        raise MaterializedPlateNativeValidationError(
            "Selected materialized plate has no retained display placements for native-envelope validation."
        )
    expected_indices = tuple(item.instance_index for item in materialization.spec.plate_spec.translations)
    placement_indices = tuple(item.instance_index for item in placements)
    if placement_indices != expected_indices:
        raise MaterializedPlateNativeValidationError(
            "Retained display placements do not match the exact selected physical plate instance indices."
        )
    if materialization.project.instance_indices != expected_indices:
        raise MaterializedPlateNativeValidationError(
            "Materialized 3MF instance indices do not match the exact selected physical plate plan."
        )

    envelopes: list[Envelope2D] = []
    for placement in placements:
        if placement.rotation_z_deg == 0:
            width = source.width_mm
            depth = source.depth_mm
        elif placement.rotation_z_deg in {-90, 90}:
            width = source.depth_mm
            depth = source.width_mm
        else:
            raise MaterializedPlateNativeValidationError(
                "Retained display placement rotation must be -90, 0, or 90 degrees."
            )
        envelopes.append(
            Envelope2D(
                min_x_mm=placement.target_display_x_mm - width / 2.0,
                max_x_mm=placement.target_display_x_mm + width / 2.0,
                min_y_mm=placement.target_display_y_mm - depth / 2.0,
                max_y_mm=placement.target_display_y_mm + depth / 2.0,
            )
        )
    return Envelope2D(
        min_x_mm=min(item.min_x_mm for item in envelopes),
        max_x_mm=max(item.max_x_mm for item in envelopes),
        min_y_mm=min(item.min_y_mm for item in envelopes),
        max_y_mm=max(item.max_y_mm for item in envelopes),
    )


@dataclass(frozen=True)
class WholePlateNativeEnvelopeEvidence:
    printer_profile_id: str
    plate_index: int
    materialized_project_sha256: str
    printer_native_sha256: str
    expected_display_envelope: Envelope2D
    observed_display_envelope: Envelope2D
    tolerance_x_mm: float
    tolerance_y_mm: float


def validate_whole_plate_native_envelope(
    materialization: SelectedPlateProjectMaterialization,
    execution: SelectedMaterializedPlateNativeExecution,
    *,
    printer: Profile,
) -> WholePlateNativeEnvelopeEvidence:
    """Validate the final native CTB/GOO whole-print rectangle against selected placement.

    The expected envelope is derived exclusively from the exact selected single-instance
    CTB display envelope plus the display placements used to rewrite the exact per-plate
    3MF. The observed envelope comes from pinned UVtools metrics on the final native plate.
    Two display pixels per axis are allowed for rasterization/quantization at transformed
    edges. This is a whole-plate consistency gate only and deliberately does not fabricate
    the per-instance 3MF envelope observations required by ``finalize_materialized_plate``.
    """
    spec = materialization.spec
    plate_spec = spec.plate_spec
    if printer.id != plate_spec.printer_profile_id:
        raise MaterializedPlateNativeValidationError(
            "Printer profile does not match the selected materialized plate."
        )
    if execution.plate_index != plate_spec.plate_index:
        raise MaterializedPlateNativeValidationError(
            "Native plate execution index does not match the selected materialized plate."
        )
    if execution.materialized_project_sha256 != materialization.project.sha256:
        raise MaterializedPlateNativeValidationError(
            "Native plate execution is not bound to the exact materialized 3MF."
        )
    artifact = execution.artifact
    if artifact.project_sha256 != materialization.project.sha256:
        raise MaterializedPlateNativeValidationError(
            "Native artifact project receipt is not bound to the exact materialized 3MF."
        )
    if artifact.printer_profile_id != printer.id:
        raise MaterializedPlateNativeValidationError(
            "Native artifact printer profile does not match the selected materialized plate."
        )
    if not artifact.native_bytes or hashlib.sha256(artifact.native_bytes).hexdigest() != artifact.native_sha256:
        raise MaterializedPlateNativeValidationError(
            "Final printer-native bytes do not match their exact SHA-256 receipt."
        )

    expected = _expected_display_envelope(materialization)
    rectangle = artifact.native_metrics.bounding_rectangle
    observed = Envelope2D(
        min_x_mm=rectangle.x_mm,
        max_x_mm=rectangle.max_x_mm,
        min_y_mm=rectangle.y_mm,
        max_y_mm=rectangle.max_y_mm,
    )

    display_width = _positive_profile_number(printer, "display_width_mm")
    display_height = _positive_profile_number(printer, "display_height_mm")
    pixels_x = _positive_profile_int(printer, "display_pixels_x")
    pixels_y = _positive_profile_int(printer, "display_pixels_y")
    tolerance_x = 2.0 * display_width / pixels_x
    tolerance_y = 2.0 * display_height / pixels_y

    for name, envelope in (("expected", expected), ("observed", observed)):
        if (
            envelope.min_x_mm < -tolerance_x
            or envelope.max_x_mm > display_width + tolerance_x
            or envelope.min_y_mm < -tolerance_y
            or envelope.max_y_mm > display_height + tolerance_y
        ):
            raise MaterializedPlateNativeValidationError(
                f"{name.capitalize()} whole-plate native envelope exceeds the printer display."
            )

    edge_checks = (
        ("min_x", expected.min_x_mm, observed.min_x_mm, tolerance_x),
        ("max_x", expected.max_x_mm, observed.max_x_mm, tolerance_x),
        ("min_y", expected.min_y_mm, observed.min_y_mm, tolerance_y),
        ("max_y", expected.max_y_mm, observed.max_y_mm, tolerance_y),
    )
    mismatches = [
        f"{name}: expected={expected_value:.6f}, observed={observed_value:.6f}, tolerance={tolerance:.6f}"
        for name, expected_value, observed_value, tolerance in edge_checks
        if abs(expected_value - observed_value) > tolerance + 1e-9
    ]
    if mismatches:
        raise MaterializedPlateNativeValidationError(
            "Final printer-native whole-plate bounding rectangle does not match the selected materialized placements: "
            + "; ".join(mismatches)
        )

    return WholePlateNativeEnvelopeEvidence(
        printer_profile_id=printer.id,
        plate_index=plate_spec.plate_index,
        materialized_project_sha256=materialization.project.sha256,
        printer_native_sha256=artifact.native_sha256,
        expected_display_envelope=expected,
        observed_display_envelope=observed,
        tolerance_x_mm=tolerance_x,
        tolerance_y_mm=tolerance_y,
    )


def whole_plate_native_envelope_manifest(
    evidence: WholePlateNativeEnvelopeEvidence,
) -> dict:
    expected = evidence.expected_display_envelope
    observed = evidence.observed_display_envelope
    return {
        "schema": WHOLE_PLATE_NATIVE_SCHEMA,
        "printer_profile_id": evidence.printer_profile_id,
        "plate_index": evidence.plate_index,
        "materialized_project_sha256": evidence.materialized_project_sha256,
        "printer_native_sha256": evidence.printer_native_sha256,
        "expected_display_envelope_mm": {
            "min_x": expected.min_x_mm,
            "max_x": expected.max_x_mm,
            "min_y": expected.min_y_mm,
            "max_y": expected.max_y_mm,
        },
        "observed_display_envelope_mm": {
            "min_x": observed.min_x_mm,
            "max_x": observed.max_x_mm,
            "min_y": observed.min_y_mm,
            "max_y": observed.max_y_mm,
        },
        "raster_tolerance_mm": {
            "x": evidence.tolerance_x_mm,
            "y": evidence.tolerance_y_mm,
        },
        "whole_plate_native_validation_passed": True,
        "per_instance_materialized_project_validation_satisfied": False,
        "validation_rule": (
            "Pinned UVtools final-native whole-print bounds must match the envelope derived from the exact selected single-instance CTB bounds and exact materialized display placements within two physical display pixels per axis. This evidence is supplementary and does not replace per-instance envelope re-extraction from the materialized 3MF."
        ),
    }
