from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .materialization_selected import SelectedPlateProjectMaterialization
from .materialized_plate_slice import (
    MaterializedPlateNativeArtifact,
    MaterializedPlateSliceError,
    slice_materialized_plate_native,
)
from .profiles import Profile


SELECTED_PLATE_NATIVE_SCHEMA = "workpiece-resin-selected-plate-native-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SelectedPlateExecutionError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_receipt(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SelectedPlateExecutionError(
            f"{name} must be a lowercase 64-character SHA-256 digest."
        )
    return normalized


@dataclass(frozen=True)
class SelectedMaterializedPlateNativeExecution:
    source_sha256: str
    selected_orientation_deg: tuple[float, float, float]
    selected_review_3mf_sha256: str
    selected_effective_config_sha256: str
    selected_intermediate_sl1_sha256: str
    selected_printer_native_sha256: str
    plate_index: int
    materialized_project_sha256: str
    artifact: MaterializedPlateNativeArtifact


def execute_selected_materialized_plate_native(
    materialization: SelectedPlateProjectMaterialization,
    *,
    selected_effective_config_bytes: bytes,
    printer: Profile,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    reject_critical: bool,
) -> SelectedMaterializedPlateNativeExecution:
    """Slice the exact selected per-plate 3MF through the retained winner recipe.

    This wrapper is the provenance gate between selected plate materialization and the
    generic exact-project slicer. It verifies the materialized project bytes/hash, exact
    planned instance indices, printer profile identity, and the selected winner's retained
    effective-config hash before any pinned engine is invoked.
    """
    spec = materialization.spec
    plate_spec = spec.plate_spec
    project = materialization.project

    if printer.id != plate_spec.printer_profile_id:
        raise SelectedPlateExecutionError(
            "Printer profile does not match the selected materialized plate plan."
        )
    if not project.bytes or _sha(project.bytes) != project.sha256:
        raise SelectedPlateExecutionError(
            "Materialized plate 3MF bytes do not match their exact project SHA-256 receipt."
        )
    expected_indices = tuple(item.instance_index for item in plate_spec.translations)
    if project.instance_indices != expected_indices:
        raise SelectedPlateExecutionError(
            "Materialized plate instance indices do not match the selected plate plan."
        )
    selected_config_hash = _sha_receipt(
        "selected_effective_config_sha256",
        spec.selected_effective_config_sha256,
    )
    if (
        not isinstance(selected_effective_config_bytes, bytes)
        or not selected_effective_config_bytes
        or _sha(selected_effective_config_bytes) != selected_config_hash
    ):
        raise SelectedPlateExecutionError(
            "Selected effective config bytes do not match the exact sliced-winner receipt."
        )

    try:
        artifact = slice_materialized_plate_native(
            project_bytes=project.bytes,
            project_sha256=project.sha256,
            effective_config_bytes=selected_effective_config_bytes,
            effective_config_sha256=selected_config_hash,
            printer=printer,
            prusa_bin=prusa_bin,
            uvtools_cmd=uvtools_cmd,
            slice_timeout=slice_timeout,
            uvtools_timeout=uvtools_timeout,
            reject_critical=reject_critical,
        )
    except MaterializedPlateSliceError as exc:
        raise SelectedPlateExecutionError(str(exc)) from exc

    if artifact.project_sha256 != project.sha256:
        raise SelectedPlateExecutionError(
            "Exact-project slicer returned a different materialized project receipt."
        )
    if artifact.effective_config_sha256 != selected_config_hash:
        raise SelectedPlateExecutionError(
            "Exact-project slicer returned a different selected effective-config receipt."
        )
    if artifact.printer_profile_id != printer.id:
        raise SelectedPlateExecutionError(
            "Exact-project slicer returned a different printer profile receipt."
        )

    return SelectedMaterializedPlateNativeExecution(
        source_sha256=_sha_receipt("source_sha256", spec.source_sha256),
        selected_orientation_deg=spec.selected_orientation_deg,
        selected_review_3mf_sha256=_sha_receipt(
            "selected_review_3mf_sha256", spec.selected_review_3mf_sha256
        ),
        selected_effective_config_sha256=selected_config_hash,
        selected_intermediate_sl1_sha256=_sha_receipt(
            "selected_intermediate_sl1_sha256", spec.selected_intermediate_sl1_sha256
        ),
        selected_printer_native_sha256=_sha_receipt(
            "selected_printer_native_sha256", spec.selected_printer_native_sha256
        ),
        plate_index=plate_spec.plate_index,
        materialized_project_sha256=project.sha256,
        artifact=artifact,
    )


def selected_materialized_plate_native_manifest(
    execution: SelectedMaterializedPlateNativeExecution,
) -> dict:
    artifact = execution.artifact
    return {
        "schema": SELECTED_PLATE_NATIVE_SCHEMA,
        "source_sha256": _sha_receipt("source_sha256", execution.source_sha256),
        "selected_orientation_deg": {
            "x": execution.selected_orientation_deg[0],
            "y": execution.selected_orientation_deg[1],
            "z": execution.selected_orientation_deg[2],
        },
        "selected_sliced_artifacts": {
            "review_3mf_sha256": _sha_receipt(
                "selected_review_3mf_sha256", execution.selected_review_3mf_sha256
            ),
            "effective_config_sha256": _sha_receipt(
                "selected_effective_config_sha256",
                execution.selected_effective_config_sha256,
            ),
            "intermediate_sl1_sha256": _sha_receipt(
                "selected_intermediate_sl1_sha256",
                execution.selected_intermediate_sl1_sha256,
            ),
            "printer_native_sha256": _sha_receipt(
                "selected_printer_native_sha256",
                execution.selected_printer_native_sha256,
            ),
        },
        "plate_index": execution.plate_index,
        "materialized_project_sha256": _sha_receipt(
            "materialized_project_sha256", execution.materialized_project_sha256
        ),
        "materialized_plate_slice": {
            "intermediate_sl1_sha256": _sha_receipt(
                "plate intermediate_sl1_sha256", artifact.intermediate_sha256
            ),
            "printer_native_sha256": _sha_receipt(
                "plate native_sha256", artifact.native_sha256
            ),
            "printer_profile_id": artifact.printer_profile_id,
            "issue_summary": dict(artifact.issue_summary),
        },
        "provenance_rule": (
            "The exact materialized per-plate 3MF is sliced with --dont-arrange using the exact effective config retained by the selected sliced-orientation winner; no STL rebuild, recentering, orientation reapplication, or profile re-resolution occurs on this path."
        ),
    }
