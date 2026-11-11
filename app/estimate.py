"""Lean per-part resin measurement for instant quotes.

`POST /v1/estimate` returns measurements only (layers, volumes, time, issues). It
never prices and never retains artifacts. It reuses the production engine
(`engine.slice_native`), the STL validator, the profile registry and the pinned
UVtools metric parsers; it adds nothing to the production-authority chain.
"""
from __future__ import annotations

import math
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from .engine import (
    PRUSA_SLICER_COMMIT,
    PRUSA_SLICER_VERSION,
    UVTOOLS_VERSION,
    EngineError,
    NativeArtifact,
    Orientation,
    critical_issue_count,
)
from .orientation_adapter import _extract_native_metrics
from .profiles import Profile, ProfileError, ProfileRegistry
from .stl import StlValidationError, validate_stl_bytes
from .uvtools_metrics import NativeArtifactMetrics

ESTIMATE_SCHEMA = "workpiece-resin-estimate-v1"
ESTIMATE_AUTHORITY = "estimate-only"


class EstimateError(Exception):
    """Estimate failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status

    def http(self, headers: dict[str, str] | None = None) -> HTTPException:
        return HTTPException(
            status_code=self.status,
            detail={"code": self.code, "message": str(self)},
            headers=headers,
        )


@dataclass(frozen=True)
class MeshStats:
    dims_mm: tuple[float, float, float]  # after the proposed orientation
    volume_mm3: float  # closed-mesh integral; orientation-invariant


@dataclass(frozen=True)
class CycleProfile:
    layer_height_mm: float
    normal_exposure_s: float
    initial_exposure_s: float
    initial_layers: int
    lift_mm: float
    lift_speed_mm_min: float
    retract_speed_mm_min: float
    rest_after_retract_s: float

    @property
    def motion_s(self) -> float:
        return self.lift_mm * 60.0 / self.lift_speed_mm_min + self.lift_mm * 60.0 / self.retract_speed_mm_min + self.rest_after_retract_s

    @property
    def normal_cycle_s(self) -> float:
        return self.normal_exposure_s + self.motion_s

    @property
    def bottom_cycle_s(self) -> float:
        return self.initial_exposure_s + self.motion_s


@dataclass(frozen=True)
class EstimateInput:
    printer: Profile
    resin: Profile
    quality: Profile
    orientation: Orientation
    mesh: MeshStats
    envelope_mm: tuple[float, float, float]  # width, depth, max print height


def _triangles(data: bytes):
    """Yield (v1, v2, v3) for a binary or ASCII STL already accepted by validate_stl_bytes."""
    if len(data) >= 84 and 84 + struct.unpack_from("<I", data, 80)[0] * 50 == len(data):
        for values in struct.iter_unpack("<12fH", data[84:]):
            yield values[3:6], values[6:9], values[9:12]
        return
    found = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data.decode("ascii", errors="replace"), flags=re.I)
    for i in range(0, len(found) - 2, 3):
        yield tuple(tuple(float(c) for c in vertex) for vertex in found[i:i + 3])


def _rotation_matrix(orientation: Orientation) -> list[list[float]] | None:
    """Rz * Ry * Rx, the order the retained-recipe command applies --rotate-x, --rotate-y, --rotate."""
    if not any((orientation.x, orientation.y, orientation.z)):
        return None
    ax, ay, az = (math.radians(v) for v in (orientation.x, orientation.y, orientation.z))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def mesh_stats(data: bytes, orientation: Orientation) -> MeshStats:
    matrix = _rotation_matrix(orientation)
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    six_volume = 0.0
    for a, b, c in _triangles(data):
        six_volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        for v in (a, b, c):
            if matrix is not None:
                v = tuple(row[0] * v[0] + row[1] * v[1] + row[2] * v[2] for row in matrix)
            for i in range(3):
                if v[i] < lo[i]:
                    lo[i] = v[i]
                if v[i] > hi[i]:
                    hi[i] = v[i]
    if not all(math.isfinite(x) for x in (*lo, *hi)):
        raise EstimateError("INVALID_STL", "STL contains no triangles.")
    dims = tuple(round(hi[i] - lo[i], 3) for i in range(3))
    return MeshStats(dims_mm=dims, volume_mm3=round(abs(six_volume) / 6.0, 3))


def prepare_estimate(
    data: bytes,
    *,
    registry: ProfileRegistry,
    printer_id: str,
    resin_id: str,
    quality_id: str,
    orientation: Orientation,
) -> EstimateInput:
    """Everything cheap that must pass before the heavy slice: STL, profiles, orientation, envelope."""
    try:
        validate_stl_bytes(data)
    except StlValidationError as exc:
        raise EstimateError("INVALID_STL", str(exc)) from exc
    try:
        printer, resin, quality = registry.resolve_candidate(printer_id, resin_id, quality_id)
        envelope = registry.printer_manufacturing_envelope(printer.id)
        height = float(printer.metadata["max_print_height_mm"])
    except (ProfileError, KeyError, TypeError, ValueError) as exc:
        raise EstimateError("UNSUPPORTED_PROFILE", f"Unsupported printer/resin/quality tuple: {exc}") from exc
    try:
        orientation.validate()
    except EngineError as exc:
        raise EstimateError("INVALID_ORIENTATION", str(exc)) from exc
    mesh = mesh_stats(data, orientation)
    limits = (envelope.width_mm, envelope.depth_mm, height)
    if any(mesh.dims_mm[i] > limits[i] + 1e-6 for i in range(3)):
        raise EstimateError(
            "OUTSIDE_ENVELOPE",
            f"Model {list(mesh.dims_mm)} mm exceeds the {printer.id} envelope {list(limits)} mm in this orientation.",
        )
    return EstimateInput(printer, resin, quality, orientation, mesh, limits)


def parse_ini(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", ";")) and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def cycle_profile(resin: Profile, effective_config: bytes) -> CycleProfile:
    """Exposure/layer values from PrusaSlicer's resolved config; motion from the resin profile metadata."""
    config = parse_ini(effective_config.decode("utf-8", errors="replace"))
    motion = resin.metadata.get("motion_contract") or {}
    try:
        return CycleProfile(
            layer_height_mm=float(config["layer_height"]),
            normal_exposure_s=float(config["exposure_time"]),
            initial_exposure_s=float(config["initial_exposure_time"]),
            initial_layers=int(config["faded_layers"]),
            lift_mm=float(motion["lift_distance_mm"]),
            lift_speed_mm_min=float(motion["lift_speed_mm_min"]),
            retract_speed_mm_min=float(motion["retract_speed_mm_min"]),
            rest_after_retract_s=float(motion["rest_after_retract_s"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EstimateError("UNSUPPORTED_PROFILE", f"Profile lacks a value needed for the time formula: {exc}") from exc


def measure_native(artifact: NativeArtifact, *, uvtools_cmd: str, uvtools_timeout: int) -> NativeArtifactMetrics:
    with tempfile.TemporaryDirectory(prefix="workpiece-estimate-") as temp:
        path = Path(temp) / f"estimate{Path(artifact.filename).suffix}"
        path.write_bytes(artifact.bytes)
        return _extract_native_metrics(path, uvtools_cmd=uvtools_cmd, uvtools_timeout=uvtools_timeout)


def engine_failure(exc: BaseException) -> EstimateError:
    if isinstance(exc.__cause__, subprocess.TimeoutExpired):
        return EstimateError("SLICE_TIMEOUT", str(exc), status=504)
    return EstimateError("SLICE_FAILED", str(exc))


def build_estimate(
    prepared: EstimateInput,
    artifact: NativeArtifact,
    metrics: NativeArtifactMetrics,
    *,
    toolchain_image_ref: str | None,
    service_commit: str | None,
) -> dict:
    cycle = cycle_profile(prepared.resin, artifact.effective_config_bytes)
    layers = metrics.layer_count
    bottom = min(cycle.initial_layers, layers)
    print_time = bottom * cycle.bottom_cycle_s + (layers - bottom) * cycle.normal_cycle_s
    total_ml = metrics.material_volume_mm3 / 1000.0
    model_ml = prepared.mesh.volume_mm3 / 1000.0
    rect = metrics.bounding_rectangle
    footprint = (rect.width_mm, rect.height_mm, metrics.z_height_mm)
    fits = all(footprint[i] <= prepared.envelope_mm[i] + 1e-6 for i in range(3))

    warnings = [
        "Exposure values are calibration seeds; print_time_seconds is layer_count x profile cycle, not measured on the printer.",
        "support_volume_ml = UVtools cured volume - closed-mesh model volume; it includes the pad and is approximate for non-watertight meshes.",
    ]
    if not fits:
        warnings.append("Supported print exceeds the manufacturing envelope; manual review required.")
    if critical_issue_count(artifact.issue_summary) > 0:
        warnings.append("UVtools reported critical print issues; manual review required.")
    digest = toolchain_image_ref.partition("@")[2] if toolchain_image_ref and "@" in toolchain_image_ref else None
    return {
        "schema": ESTIMATE_SCHEMA,
        "authority": ESTIMATE_AUTHORITY,
        "engine": {
            "prusaslicer": PRUSA_SLICER_VERSION,
            "prusaslicerCommit": PRUSA_SLICER_COMMIT,
            "uvtools": UVTOOLS_VERSION,
            "toolchainDigest": digest,
            "serviceCommit": service_commit or None,
        },
        "profile": {
            "printer": prepared.printer.id,
            "resin": prepared.resin.id,
            "quality": prepared.quality.id,
            "layerHeightMm": cycle.layer_height_mm,
            "normalExposureS": cycle.normal_exposure_s,
            "initialExposureS": cycle.initial_exposure_s,
            "initialLayers": cycle.initial_layers,
            "liftMm": cycle.lift_mm,
            "liftSpeedMmMin": cycle.lift_speed_mm_min,
            "retractSpeedMmMin": cycle.retract_speed_mm_min,
            "restAfterRetractS": cycle.rest_after_retract_s,
        },
        "summary": {
            "layer_count": layers,
            "bottom_layer_count": bottom,
            "print_time_seconds": round(print_time, 1),
            "model_volume_ml": round(model_ml, 3),
            "support_volume_ml": round(max(total_ml - model_ml, 0.0), 3),
            "total_resin_ml": round(total_ml, 3),
            "bounding_box_mm": list(prepared.mesh.dims_mm),
            "print_footprint_mm": {"width": footprint[0], "depth": footprint[1], "height": footprint[2]},
            "fits_envelope": fits,
            "orientation_deg": [prepared.orientation.x, prepared.orientation.y, prepared.orientation.z],
        },
        "time_breakdown": {
            "bottom_layer_cycle_s": round(cycle.bottom_cycle_s, 4),
            "normal_layer_cycle_s": round(cycle.normal_cycle_s, 4),
            "fixed_overhead_s": 0,
        },
        "issues": dict(artifact.issue_summary),
        "warnings": warnings,
        "hashes": {
            "source_sha256": artifact.source_sha256,
            "effective_config_sha256": artifact.effective_config_sha256,
            "native_sha256": artifact.native_sha256,
        },
    }
