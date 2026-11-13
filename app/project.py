"""Order project: one STL + a quantity -> per-plate PrusaSlicer 3MF + CTB + project.json.

`POST /v1/project` is the resin counterpart of the FDM project route. After an order
the website calls it once per ordered part; the workshop opens every plate project in
PrusaSlicer and reviews it before printing. The bar is therefore a good, editable
project with the right instances on as few plates as possible, not a proof.

Steps (all existing building blocks):

1. orientation: geometry-proxy screening, then real slices of the finalists ranked on
   UVtools metrics (``orientation_adapter``); if every finalist is flagged, the best
   proxy orientation is used and the project carries a warning;
2. plate plan: row-major grid of the supported footprint on the printer's conservative
   manufacturing envelope, 90 degree plate rotation when it packs more (``plate``);
3. materialize: clone the selected review 3MF's single build item once per instance at
   the planned bed positions (``prusa_3mf_instances``);
4. slice each plate with the retained effective config and ``--dont-arrange``, convert
   to CTB and inspect/measure it with UVtools (``materialized_plate_slice``);
5. ZIP: ``<stem>-plate-NN.3mf``, ``<stem>-plate-NN.ctb``, UVtools issue text per plate,
   the effective config, and ``project.json``.

Coordinates: the plan is laid out on the 80 x 129 mm envelope centred in PrusaSlicer's
82.62 x 130.56 mm bed, in bed millimetres from the lower-left corner. The review 3MF was
exported with ``--center`` at the bed centre, and the supported/padded footprint is
assumed to be centred on that point too (its width/depth come from UVtools; its exact
offset does not, because the bed-to-CTB mirror mapping is still unverified). The 5 mm
spacing and 3 mm margin absorb modest asymmetry; the reviewer sees the real layout in
PrusaSlicer, and ``scripts/calibrate_mars2_mapping.py`` measures the mapping once.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .engine import (
    PRUSA_SLICER_COMMIT,
    PRUSA_SLICER_VERSION,
    UVTOOLS_VERSION,
    Orientation,
    critical_issue_count,
)
from .estimate import cycle_profile, formula_print_time_seconds, mesh_stats
from .issue_triage import support_lift_layers, triage_issue_summary
from .materialized_plate_slice import (
    MaterializedPlateNativeArtifact,
    MaterializedPlateSliceError,
    slice_materialized_plate_native,
)
from .orientation import OrientationPlanError
from .orientation_adapter import (
    SELECTION_DEVIATED,
    SELECTION_SLICED_WINNER,
    SlicedFinalistAdapterError,
    execute_real_sliced_finalists,
)
from .orientation_execute import SlicedFinalistExecutionError
from .orientation_pipeline import build_proxy_orientation_plan
from .orientation_proxy import OrientationProxyError
from .orientation_screen import DEFAULT_PROXY_FINALISTS, OrientationScreenError
from .orientation_sliced import SlicedOrientationValidationError
from .bed_frame import BedFrameError, bed_envelope_from_native_rectangle as _bed_envelope, printer_flag
from .placement import Envelope2D
from .plate import PlatePlanError, plan_printer_profile_instances
from .profiles import ManufacturingEnvelope, Profile, ProfileError, ProfileRegistry
from .prusa_3mf_instances import (
    DisplayInstancePlacement,
    ThreeMFMaterializationError,
    materialize_prusa_project_instances,
)
from .stl import StlValidationError, validate_stl_bytes

PROJECT_SCHEMA = "workpiece-resin-project-v1"
DEFAULT_SPACING_MM = 5.0
DEFAULT_EDGE_MARGIN_MM = 3.0

_ORIENTATION_ERRORS = (
    OrientationPlanError,
    OrientationProxyError,
    OrientationScreenError,
    SlicedFinalistExecutionError,
    SlicedOrientationValidationError,
)


class ProjectError(Exception):
    """Project failure with a stable machine-readable code (same shape as /v1/estimate)."""

    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ProjectInput:
    source_stl: bytes
    source_filename: str
    stem: str
    requested_quantity: int
    printer: Profile
    resin: Profile
    quality: Profile
    envelope: ManufacturingEnvelope
    bed_width_mm: float
    bed_depth_mm: float

    @property
    def envelope_offset_mm(self) -> tuple[float, float]:
        """Lower-left corner of the manufacturing envelope in bed millimetres (centred)."""
        return (
            (self.bed_width_mm - self.envelope.width_mm) / 2.0,
            (self.bed_depth_mm - self.envelope.depth_mm) / 2.0,
        )


@dataclass(frozen=True)
class OrderProject:
    zip_bytes: bytes
    zip_filename: str
    manifest: dict


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_stem(name: str) -> str:
    filename = str(name).strip().replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-._") or "workpiece"


def _timed_out(exc: BaseException) -> bool:
    while exc is not None:
        if isinstance(exc, subprocess.TimeoutExpired):
            return True
        exc = exc.__cause__
    return False


def _engine_error(exc: BaseException) -> ProjectError:
    if _timed_out(exc):
        return ProjectError("SLICE_TIMEOUT", str(exc), status=504)
    return ProjectError("SLICE_FAILED", str(exc))


def prepare_order_project(
    data: bytes,
    *,
    original_name: str,
    requested_quantity: int,
    registry: ProfileRegistry,
    printer_id: str,
    resin_id: str,
    quality_id: str,
) -> ProjectInput:
    """Everything cheap that must pass before the heavy slices: STL, profiles, bed geometry."""
    if isinstance(requested_quantity, bool) or not isinstance(requested_quantity, int) or requested_quantity < 1:
        raise ProjectError("INVALID_QUANTITY", "requested_quantity must be a positive integer.")
    try:
        validate_stl_bytes(data)
    except StlValidationError as exc:
        raise ProjectError("INVALID_STL", str(exc)) from exc
    try:
        printer, resin, quality = registry.resolve_candidate(printer_id, resin_id, quality_id)
        envelope = registry.printer_manufacturing_envelope(printer.id)
        bed_width = float(printer.metadata["display_width_mm"])
        bed_depth = float(printer.metadata["display_height_mm"])
    except (ProfileError, KeyError, TypeError, ValueError) as exc:
        raise ProjectError("UNSUPPORTED_PROFILE", f"Unsupported printer/resin/quality tuple: {exc}") from exc
    if envelope.width_mm > bed_width + 1e-9 or envelope.depth_mm > bed_depth + 1e-9:
        raise ProjectError("UNSUPPORTED_PROFILE", f"{printer.id} manufacturing envelope exceeds its bed.")
    return ProjectInput(
        source_stl=data,
        source_filename=_safe_stem(original_name) + ".stl",
        stem=_safe_stem(original_name),
        requested_quantity=requested_quantity,
        printer=printer,
        resin=resin,
        quality=quality,
        envelope=envelope,
        bed_width_mm=bed_width,
        bed_depth_mm=bed_depth,
    )


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes, *, compress: bool = True) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def _file_record(name: str, data: bytes) -> dict:
    return {"name": name, "sha256": _sha(data), "bytes": len(data)}


def _printer_flag(printer: Profile, key: str) -> bool:
    try:
        return printer_flag(printer, key)
    except BedFrameError as exc:
        raise ProjectError("UNSUPPORTED_PROFILE", str(exc)) from exc


def bed_envelope_from_native_rectangle(rectangle, *, bed_width_mm: float, bed_depth_mm: float, mirror_x: bool, mirror_y: bool) -> Envelope2D:
    """See ``bed_frame.bed_envelope_from_native_rectangle`` (the measured raster-to-bed conversion); unsupported mirrors are a project error."""
    try:
        return _bed_envelope(rectangle, bed_width_mm=bed_width_mm, bed_depth_mm=bed_depth_mm, mirror_x=mirror_x, mirror_y=mirror_y)
    except BedFrameError as exc:
        raise ProjectError("UNSUPPORTED_PROFILE", str(exc)) from exc


def _require_footprints_inside_bed(plate_index, placements, rectangle, prepared: ProjectInput) -> None:
    """Never hand PrusaSlicer a plate it will silently skip: every instance's footprint must lie inside the bed."""
    for item in placements:
        width, depth = (rectangle.height_mm, rectangle.width_mm) if item.rotation_z_deg % 180 == 90 else (rectangle.width_mm, rectangle.height_mm)
        low_x, high_x = item.target_display_x_mm - width / 2.0, item.target_display_x_mm + width / 2.0
        low_y, high_y = item.target_display_y_mm - depth / 2.0, item.target_display_y_mm + depth / 2.0
        if low_x < -1e-6 or low_y < -1e-6 or high_x > prepared.bed_width_mm + 1e-6 or high_y > prepared.bed_depth_mm + 1e-6:
            raise ProjectError(
                "MATERIALIZE_FAILED",
                f"Plate {plate_index} instance {item.instance_index}: footprint x {low_x:.2f}..{high_x:.2f}, y {low_y:.2f}..{high_y:.2f} mm "
                f"is outside the {prepared.bed_width_mm} x {prepared.bed_depth_mm} mm bed.",
            )


def estimate_plate_plan(
    *,
    printer_id: str,
    envelope: ManufacturingEnvelope,
    footprint_width_mm: float,
    footprint_depth_mm: float,
    quantity: int,
    print_time_seconds: float,
) -> dict:
    """The plate plan of an estimate: the same ``plan_order_plates`` the project build uses, as JSON.

    Every plate holds identical parts, so every plate exposes the same number of layers and takes the one-part
    ``print_time_seconds`` (MSLA exposes the whole plate per layer); the order takes plate_count times that.
    """
    try:
        plan = plan_order_plates(
            printer_id=printer_id, envelope=envelope, footprint_width_mm=footprint_width_mm,
            footprint_depth_mm=footprint_depth_mm, quantity=quantity,
        )
    except PlatePlanError as exc:
        return {"status": "does_not_fit", "quantity": quantity, "reason": str(exc)}
    return {
        "status": "ok",
        "quantity": quantity,
        "parts_per_plate": plan.capacity_per_plate,
        "plate_count": plan.plate_count,
        "plates": [{"plate_index": plate.plate_index, "instances": len(plate.placements)} for plate in plan.plates],
        "footprint_mm": {"width": footprint_width_mm, "depth": footprint_depth_mm},
        "spacing_mm": plan.spacing_mm,
        "edge_margin_mm": plan.edge_margin_mm,
        "rotation_z_deg": plan.rotation_z_deg,
        "plate_print_seconds": round(print_time_seconds, 1),
        "total_print_seconds": round(print_time_seconds * plan.plate_count, 1),
    }


def build_order_project(
    prepared: ProjectInput,
    *,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    finalist_limit: int = DEFAULT_PROXY_FINALISTS,
    spacing_mm: float = DEFAULT_SPACING_MM,
    edge_margin_mm: float = DEFAULT_EDGE_MARGIN_MM,
    service_commit: str | None = None,
) -> OrderProject:
    source = prepared.source_stl
    printer, resin, quality = prepared.printer, prepared.resin, prepared.quality
    warnings: list[str] = []

    # 1. orientation
    try:
        proxy_plan = build_proxy_orientation_plan(source, finalist_limit=finalist_limit)
        sliced = execute_real_sliced_finalists(
            proxy_plan=proxy_plan,
            source_stl=source,
            original_name=prepared.source_filename,
            printer=printer,
            resin=resin,
            quality=quality,
            prusa_bin=prusa_bin,
            uvtools_cmd=uvtools_cmd,
            slice_timeout=slice_timeout,
            uvtools_timeout=uvtools_timeout,
            fallback_to_proxy_best=True,
        )
    except _ORIENTATION_ERRORS as exc:
        raise ProjectError("ORIENTATION_FAILED", str(exc)) from exc
    except SlicedFinalistAdapterError as exc:
        raise _engine_error(exc) from exc
    if sliced.selected_result is None or sliced.selected_native_rectangle is None:
        raise ProjectError("ORIENTATION_FAILED", "No orientation finalist could be sliced.")
    selected = sliced.selected_result.artifact
    rectangle = sliced.selected_native_rectangle
    orientation = selected.orientation
    if sliced.selection == SELECTION_DEVIATED:
        warnings.append(sliced.deviation_reason or "The build orientation differs from the proxy-best orientation the instant quote measured.")
    elif sliced.selection != SELECTION_SLICED_WINNER:
        warnings.append(
            "Every sliced orientation finalist still had critical UVtools issues; the one with the fewest "
            "was used. Review supports and islands before printing."
        )

    # 2. plate plan on the manufacturing envelope (envelope coordinates, lower-left origin)
    try:
        plan = plan_order_plates(
            printer_id=printer.id,
            envelope=prepared.envelope,
            footprint_width_mm=rectangle.width_mm,
            footprint_depth_mm=rectangle.height_mm,
            quantity=prepared.requested_quantity,
            spacing_mm=spacing_mm,
            edge_margin_mm=edge_margin_mm,
        )
    except PlatePlanError as exc:
        raise ProjectError(
            "DOES_NOT_FIT",
            f"Supported footprint {rectangle.width_mm} x {rectangle.height_mm} mm in orientation "
            f"{[orientation.x, orientation.y, orientation.z]}: {exc}",
        ) from exc

    offset_x, offset_y = prepared.envelope_offset_mm
    source_envelope = bed_envelope_from_native_rectangle(
        rectangle,
        bed_width_mm=prepared.bed_width_mm,
        bed_depth_mm=prepared.bed_depth_mm,
        mirror_x=_printer_flag(printer, "display_mirror_x"),
        mirror_y=_printer_flag(printer, "display_mirror_y"),
    )
    cycle = cycle_profile(resin, selected.effective_config_bytes)
    mesh = mesh_stats(source, Orientation(orientation.x, orientation.y, orientation.z))
    native_suffix = str(printer.metadata.get("native_format", "ctb")).lower().lstrip(".")

    # 3./4. materialize and slice every plate
    files: dict[str, bytes] = {}
    plates: list[dict] = []
    for plate in plan.plates:
        placements = tuple(
            DisplayInstancePlacement(
                instance_index=item.instance_index,
                target_display_x_mm=round(item.x_mm + offset_x, 6),
                target_display_y_mm=round(item.y_mm + offset_y, 6),
                rotation_z_deg=item.rotation_z_deg,
            )
            for item in plate.placements
        )
        _require_footprints_inside_bed(plate.plate_index, placements, rectangle, prepared)
        try:
            project = materialize_prusa_project_instances(
                selected.project_bytes,
                source_project_sha256=selected.project_sha256,
                source_display_envelope=source_envelope,
                placements=placements,
            )
        except ThreeMFMaterializationError as exc:
            raise ProjectError("MATERIALIZE_FAILED", f"Plate {plate.plate_index}: {exc}") from exc
        try:
            artifact: MaterializedPlateNativeArtifact = slice_materialized_plate_native(
                project_bytes=project.bytes,
                project_sha256=project.sha256,
                effective_config_bytes=selected.effective_config_bytes,
                effective_config_sha256=selected.effective_config_sha256,
                printer=printer,
                prusa_bin=prusa_bin,
                uvtools_cmd=uvtools_cmd,
                slice_timeout=slice_timeout,
                uvtools_timeout=uvtools_timeout,
                reject_critical=False,
            )
        except MaterializedPlateSliceError as exc:
            raise _engine_error(exc) from exc

        tag = f"{prepared.stem}-plate-{plate.plate_index:02d}"
        project_name = f"{tag}.3mf"
        native_name = f"{tag}.{native_suffix}"
        issues_name = f"{tag}-uvtools-issues.txt"
        issue_text = artifact.issue_text.encode("utf-8", errors="replace")
        files[project_name] = project.bytes
        files[native_name] = artifact.native_bytes
        files[issues_name] = issue_text

        metrics = artifact.native_metrics
        plate_issues = triage_issue_summary(
            artifact.issue_summary, artifact.issue_text,
            lift_layers=support_lift_layers(metrics.z_height_mm, mesh.dims_mm[2], cycle.layer_height_mm),
        )
        critical = critical_issue_count(plate_issues)
        if critical:
            warnings.append(f"Plate {plate.plate_index}: UVtools reported {critical} critical issue(s); review before printing.")
        observed = metrics.bounding_rectangle
        plates.append(
            {
                "plate_index": plate.plate_index,
                "files": {
                    "project_3mf": _file_record(project_name, project.bytes),
                    "printer_native": _file_record(native_name, artifact.native_bytes),
                    "uvtools_issues": _file_record(issues_name, issue_text),
                },
                "instance_count": len(placements),
                "instances": [
                    {
                        "instance_index": item.instance_index,
                        "x_mm": item.target_display_x_mm,
                        "y_mm": item.target_display_y_mm,
                        "rotation_z_deg": item.rotation_z_deg,
                    }
                    for item in placements
                ],
                "layer_count": metrics.layer_count,
                "print_time_seconds": round(formula_print_time_seconds(cycle, metrics.layer_count), 1),
                "resin_ml": round(metrics.material_volume_mm3 / 1000.0, 3),
                "z_height_mm": metrics.z_height_mm,
                "issues": plate_issues,
                "observed_footprint_mm": {
                    "x": observed.x_mm,
                    "y": observed.y_mm,
                    "width": observed.width_mm,
                    "depth": observed.height_mm,
                    "coordinate_space": "uvtools-display-millimetres",
                },
            }
        )

    effective_name = f"{prepared.stem}-effective.ini"
    files[effective_name] = selected.effective_config_bytes

    warnings.append(
        "print_time_seconds is layer_count x profile cycle with exposure seeds that are not yet calibrated on the printer."
    )
    warnings.append(
        "Instance positions assume the supported footprint is centred on the model centre and the envelope is centred "
        "in the PrusaSlicer bed; the bed-to-CTB mapping is unverified. Open each plate 3MF in PrusaSlicer and check the layout."
    )

    manifest = {
        "schema": PROJECT_SCHEMA,
        "source": {
            "filename": prepared.source_filename,
            "sha256": _sha(source),
            "requested_quantity": prepared.requested_quantity,
        },
        "engine": {
            "prusaslicer": PRUSA_SLICER_VERSION,
            "prusaslicerCommit": PRUSA_SLICER_COMMIT,
            "uvtools": UVTOOLS_VERSION,
            "serviceCommit": service_commit or None,
        },
        "profile": {
            "printer": printer.id,
            "resin": resin.id,
            "quality": quality.id,
            "layerHeightMm": cycle.layer_height_mm,
            "normalExposureS": cycle.normal_exposure_s,
            "initialExposureS": cycle.initial_exposure_s,
            "initialLayers": cycle.initial_layers,
            "liftMm": cycle.lift_mm,
            "liftSpeedMmMin": cycle.lift_speed_mm_min,
            "retractSpeedMmMin": cycle.retract_speed_mm_min,
            "restAfterRetractS": cycle.rest_after_retract_s,
        },
        "orientation": {
            "deg": {"x": orientation.x, "y": orientation.y, "z": orientation.z},
            "selection": sliced.selection,
            "finalists_sliced": sliced.executed_finalist_count,
            **({"reason": sliced.deviation_reason} if sliced.deviation_reason else {}),
        },
        "part": {
            "model_volume_ml": round(mesh.volume_mm3 / 1000.0, 3),
            "bounding_box_mm": list(mesh.dims_mm),
            "supported_footprint_mm": {"width": rectangle.width_mm, "depth": rectangle.height_mm},
            "z_height_mm": sliced.selected_result.measurements.z_height_mm,
            "review_3mf_sha256": selected.project_sha256,
        },
        "layout": {
            "bed_mm": {"width": prepared.bed_width_mm, "depth": prepared.bed_depth_mm},
            "envelope_mm": {
                "width": prepared.envelope.width_mm,
                "depth": prepared.envelope.depth_mm,
                "offset_x": round(offset_x, 6),
                "offset_y": round(offset_y, 6),
            },
            "spacing_mm": plan.spacing_mm,
            "edge_margin_mm": plan.edge_margin_mm,
            "rotation_z_deg": plan.rotation_z_deg,
            "columns": plan.columns,
            "rows": plan.rows,
            "capacity_per_plate": plan.capacity_per_plate,
            "plate_count": plan.plate_count,
            "coordinate_rule": (
                "x_mm/y_mm are instance footprint centres in PrusaSlicer bed millimetres from the lower-left "
                "corner; rotation_z_deg is applied about the footprint centre before translation."
            ),
        },
        "plates": plates,
        "totals": {
            "instances": sum(item["instance_count"] for item in plates),
            "plates": len(plates),
            "print_time_seconds": round(sum(item["print_time_seconds"] for item in plates), 1),
            "resin_ml": round(sum(item["resin_ml"] for item in plates), 3),
        },
        "files": {"effective_config": _file_record(effective_name, selected.effective_config_bytes)},
        "warnings": warnings,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=True) as zf:
        for name, data in files.items():
            _zip_write(zf, name, data, compress=not name.endswith((".3mf", f".{native_suffix}")))
        _zip_write(zf, "project.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    return OrderProject(
        zip_bytes=buffer.getvalue(),
        zip_filename=f"{prepared.stem}-workpiece-resin-project.zip",
        manifest=manifest,
    )


class _FixedEnvelopeRegistry:
    """Hand ``plan_printer_profile_instances`` an envelope that was already resolved."""

    def __init__(self, printer_id: str, envelope: ManufacturingEnvelope):
        self._printer_id = printer_id
        self._envelope = envelope

    def printer_manufacturing_envelope(self, printer_id: str) -> ManufacturingEnvelope:
        if printer_id != self._printer_id:
            raise PlatePlanError(f"Unexpected printer profile {printer_id}.")
        return self._envelope


def plan_order_plates(
    *,
    printer_id: str,
    envelope: ManufacturingEnvelope,
    footprint_width_mm: float,
    footprint_depth_mm: float,
    quantity: int,
    spacing_mm: float = DEFAULT_SPACING_MM,
    edge_margin_mm: float = DEFAULT_EDGE_MARGIN_MM,
):
    """THE plate plan: how many copies fit on a plate and how many plates an order needs.

    ``/v1/project`` builds its plates from this function and ``/v1/estimate`` (with a quantity) reports its
    result, so the quote and the build can never disagree on the plate count. Raises ``PlatePlanError`` when
    one copy does not fit.
    """
    return plan_printer_profile_instances(
        registry=_FixedEnvelopeRegistry(printer_id, envelope),
        printer_profile_id=printer_id,
        footprint_width_mm=footprint_width_mm,
        footprint_depth_mm=footprint_depth_mm,
        quantity=quantity,
        spacing_mm=spacing_mm,
        edge_margin_mm=edge_margin_mm,
        allow_rotate_90=True,
    ).plan
