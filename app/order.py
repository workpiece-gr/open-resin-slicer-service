from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .materialization import MaterializedPlateEvidence, materialized_plate_manifest
from .plate import PrinterPlatePlan, printer_plate_plan_manifest


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OrderManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PlateArtifactRecord:
    plate_index: int
    project_filename: str
    project_sha256: str
    intermediate_filename: str
    intermediate_sha256: str
    native_filename: str
    native_sha256: str
    issue_summary: Mapping[str, int]
    materialization: MaterializedPlateEvidence


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise OrderManifestError(f"{name} must be a 64-character SHA-256 hex digest.")
    return normalized


def _filename(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized or "/" in normalized or "\\" in normalized:
        raise OrderManifestError(f"{name} must be a simple retained artifact filename.")
    return normalized


def _required_text(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise OrderManifestError(f"{name} is required.")
    return normalized


def _issues(value: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, count in sorted(value.items()):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise OrderManifestError("issue counts must be non-negative integers.")
        result[str(key)] = count
    return result


def build_order_manifest(
    *,
    source_filename: str,
    source_sha256: str,
    requested_quantity: int,
    orientation_deg: Mapping[str, float],
    printer_profile: str,
    resin_profile: str,
    quality_profile: str,
    printer_plate_plan: PrinterPlatePlan,
    plate_artifacts: Sequence[PlateArtifactRecord],
    prusaslicer_version: str,
    prusaslicer_commit: str,
    uvtools_version: str,
    authority: str,
) -> dict:
    """Bind one source request to every exact, validated physical-plate artifact."""
    plan = printer_plate_plan.plan
    if isinstance(requested_quantity, bool) or not isinstance(requested_quantity, int) or requested_quantity < 1:
        raise OrderManifestError("requested_quantity must be a positive integer.")
    if requested_quantity != plan.quantity:
        raise OrderManifestError("requested_quantity does not match the deterministic plate plan.")

    source_name = _filename("source_filename", source_filename)
    source_hash = _sha256("source_sha256", source_sha256)
    printer_id = _required_text("printer_profile", printer_profile)
    resin_id = _required_text("resin_profile", resin_profile)
    quality_id = _required_text("quality_profile", quality_profile)
    if printer_id != printer_plate_plan.printer_profile_id:
        raise OrderManifestError("printer_profile does not match the printer-backed plate plan.")

    authority_value = str(authority).strip()
    if authority_value not in {"acceptance-candidate-only", "production-authoritative"}:
        raise OrderManifestError("authority must be an accepted Workpiece resin authority value.")

    if set(orientation_deg) != {"x", "y", "z"}:
        raise OrderManifestError("orientation_deg must contain exactly x, y, and z.")
    orientation: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        value = orientation_deg[axis]
        if isinstance(value, bool):
            raise OrderManifestError("orientation values must be finite numeric degrees.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise OrderManifestError("orientation values must be finite numeric degrees.") from exc
        if not math.isfinite(numeric):
            raise OrderManifestError("orientation values must be finite numeric degrees.")
        orientation[axis] = numeric

    expected_indices = list(range(1, plan.plate_count + 1))
    records_by_index: dict[int, PlateArtifactRecord] = {}
    for record in plate_artifacts:
        if isinstance(record.plate_index, bool) or not isinstance(record.plate_index, int):
            raise OrderManifestError("plate_index must be an integer.")
        if record.plate_index in records_by_index:
            raise OrderManifestError(f"Duplicate artifact record for plate {record.plate_index}.")
        records_by_index[record.plate_index] = record
    if sorted(records_by_index) != expected_indices:
        raise OrderManifestError("Plate artifact records must cover every planned physical plate exactly once.")

    plates = []
    for plate in plan.plates:
        record = records_by_index[plate.plate_index]
        project_hash = _sha256("project_sha256", record.project_sha256)
        materialization = record.materialization
        if materialization.plate_index != plate.plate_index:
            raise OrderManifestError("Materialization evidence plate_index does not match its artifact record.")
        if materialization.printer_profile_id != printer_id:
            raise OrderManifestError("Materialization evidence printer profile does not match the order.")
        if materialization.project_sha256 != project_hash:
            raise OrderManifestError("Review 3MF hash does not match the materialization evidence.")
        expected_instances = [placement.instance_index for placement in plate.placements]
        observed_instances = sorted(item.instance_index for item in materialization.observations)
        if observed_instances != expected_instances:
            raise OrderManifestError("Materialization evidence does not cover the exact planned instances for its plate.")
        if authority_value == "production-authoritative" and not materialization.automatic_materialization_authority:
            raise OrderManifestError(
                "Production authority requires validated manufacturing-envelope coordinate mapping on every plate."
            )

        plates.append(
            {
                "plate_index": plate.plate_index,
                "instance_indices": expected_instances,
                "files": {
                    "review_3mf": {
                        "name": _filename("project_filename", record.project_filename),
                        "sha256": project_hash,
                    },
                    "intermediate_sl1": {
                        "name": _filename("intermediate_filename", record.intermediate_filename),
                        "sha256": _sha256("intermediate_sha256", record.intermediate_sha256),
                    },
                    "printer_native": {
                        "name": _filename("native_filename", record.native_filename),
                        "sha256": _sha256("native_sha256", record.native_sha256),
                    },
                },
                "issues": _issues(record.issue_summary),
                "materialization": materialized_plate_manifest(materialization),
            }
        )

    return {
        "schema": "workpiece-resin-order-manifest-v2",
        "authority": authority_value,
        "source": {"name": source_name, "sha256": source_hash},
        "requested_quantity": requested_quantity,
        "orientation_deg": orientation,
        "profiles": {
            "printer": printer_id,
            "resin": resin_id,
            "quality": quality_id,
        },
        "engine": {
            "prusaslicer": {
                "version": _required_text("prusaslicer_version", prusaslicer_version),
                "commit": _required_text("prusaslicer_commit", prusaslicer_commit),
            },
            "uvtools": {"version": _required_text("uvtools_version", uvtools_version)},
        },
        "plate_plan": printer_plate_plan_manifest(printer_plate_plan),
        "plates": plates,
        "review_rule": (
            "Every printer-native plate file is valid only for the exact retained review 3MF hash and validated materialized-envelope evidence bound to that plate. "
            "If any retained review 3MF is edited or support/pad geometry changes, regenerate that plate's materialization evidence, downstream artifacts and order manifest."
        ),
    }
