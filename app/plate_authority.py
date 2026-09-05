from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping

from .engine import critical_issue_count
from .materialized_3mf_instance_evidence import (
    SelectedMaterialized3MFInstanceEvidence,
    VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE,
    materialized_3mf_instance_evidence_manifest,
)
from .materialized_plate_execution import (
    SelectedMaterializedPlateNativeExecution,
    selected_materialized_plate_native_manifest,
)
from .materialized_plate_native_validation import (
    WholePlateNativeEnvelopeEvidence,
    whole_plate_native_envelope_manifest,
)
from .placement import Envelope2D
from .profiles import Profile


PLATE_AUTHORITY_SCHEMA = "workpiece-resin-plate-authority-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ISSUE_KEYS = {
    "islands",
    "overhangs",
    "resin_traps",
    "suction_cups",
    "touching_bounds",
    "empty_layers",
}


class PlateAuthorityError(ValueError):
    pass


@dataclass(frozen=True)
class SelectedPlateAuthorityEvidence:
    printer_profile_id: str
    plate_index: int
    source_sha256: str
    selected_review_3mf_sha256: str
    selected_effective_config_sha256: str
    selected_intermediate_sl1_sha256: str
    selected_printer_native_sha256: str
    materialized_project_sha256: str
    plate_intermediate_sha256: str
    plate_printer_native_sha256: str
    issue_summary: dict[str, int]
    instance_evidence: SelectedMaterialized3MFInstanceEvidence
    native_execution: SelectedMaterializedPlateNativeExecution
    whole_plate_native_evidence: WholePlateNativeEnvelopeEvidence


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise PlateAuthorityError(
            f"{name} must be a lowercase 64-character SHA-256 digest."
        )
    return normalized


def _validate_issue_summary(value: Mapping[str, int]) -> dict[str, int]:
    if set(value) != _REQUIRED_ISSUE_KEYS:
        raise PlateAuthorityError(
            "Plate native issue summary must contain the exact pinned UVtools resin issue categories."
        )
    result: dict[str, int] = {}
    for key in sorted(_REQUIRED_ISSUE_KEYS):
        count = value[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PlateAuthorityError(
                "Plate native issue counts must be non-negative integers."
            )
        result[key] = count
    return result


def _artifact_observed_envelope(
    execution: SelectedMaterializedPlateNativeExecution,
) -> Envelope2D:
    rectangle = execution.artifact.native_metrics.bounding_rectangle
    return Envelope2D(
        min_x_mm=rectangle.x_mm,
        max_x_mm=rectangle.max_x_mm,
        min_y_mm=rectangle.y_mm,
        max_y_mm=rectangle.max_y_mm,
    )


def validate_selected_plate_authority(
    instance_evidence: SelectedMaterialized3MFInstanceEvidence,
    native_execution: SelectedMaterializedPlateNativeExecution,
    whole_plate_native_evidence: WholePlateNativeEnvelopeEvidence,
    *,
    printer: Profile,
) -> SelectedPlateAuthorityEvidence:
    """Require the complete selected-plate evidence chain before production authority.

    This gate does not enable a printer, service route or deployment. It only establishes
    that one already-materialized physical plate has all evidence required by the current
    Workpiece resin contract: exact selected sliced provenance, deterministic full-project
    3MF reconstruction and parsed per-instance transforms, validated manufacturing/display
    mapping, exact retained-config native slicing with no rearrangement, zero critical
    pinned-UVtools issues, and final native whole-print bounds matching the selected plate.
    The printer profile itself must already be explicitly marked production-ready.
    """
    selected = instance_evidence.materialized_plate
    materialized = selected.materialized_plate
    build = instance_evidence.build_items
    artifact = native_execution.artifact

    if not printer.production_ready:
        raise PlateAuthorityError(
            "Printer profile is not production-ready; physical validation must be completed before plate production authority."
        )
    if str(printer.metadata.get("manufacturing_envelope_coordinate_mapping", "")).strip() != "validated":
        raise PlateAuthorityError(
            "Printer profile does not declare a validated manufacturing-envelope coordinate mapping."
        )
    if not materialized.automatic_materialization_authority:
        raise PlateAuthorityError(
            "Per-instance materialization evidence is not authoritative for automatic physical placement."
        )
    observation_sources = {item.source for item in materialized.observations}
    if observation_sources != {VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE}:
        raise PlateAuthorityError(
            "Selected automatic plate authority requires verified exact-3MF build-item instance evidence."
        )

    printer_ids = {
        printer.id,
        materialized.printer_profile_id,
        artifact.printer_profile_id,
        whole_plate_native_evidence.printer_profile_id,
    }
    if len(printer_ids) != 1:
        raise PlateAuthorityError(
            "Printer profile identity differs across plate materialization, native execution and final native validation."
        )
    plate_indices = {
        selected.plate_index,
        native_execution.plate_index,
        whole_plate_native_evidence.plate_index,
    }
    if len(plate_indices) != 1:
        raise PlateAuthorityError(
            "Plate index differs across per-instance, native execution and whole-plate evidence."
        )

    selected_chain = (
        _sha256("source_sha256", selected.source_sha256),
        _sha256("selected_review_3mf_sha256", selected.selected_review_3mf_sha256),
        _sha256(
            "selected_effective_config_sha256",
            selected.selected_effective_config_sha256,
        ),
        _sha256(
            "selected_intermediate_sl1_sha256",
            selected.selected_intermediate_sl1_sha256,
        ),
        _sha256(
            "selected_printer_native_sha256",
            selected.selected_printer_native_sha256,
        ),
    )
    execution_chain = (
        _sha256("execution source_sha256", native_execution.source_sha256),
        _sha256(
            "execution selected_review_3mf_sha256",
            native_execution.selected_review_3mf_sha256,
        ),
        _sha256(
            "execution selected_effective_config_sha256",
            native_execution.selected_effective_config_sha256,
        ),
        _sha256(
            "execution selected_intermediate_sl1_sha256",
            native_execution.selected_intermediate_sl1_sha256,
        ),
        _sha256(
            "execution selected_printer_native_sha256",
            native_execution.selected_printer_native_sha256,
        ),
    )
    if execution_chain != selected_chain:
        raise PlateAuthorityError(
            "Native plate execution does not derive from the exact selected sliced-orientation artifact chain."
        )
    if native_execution.selected_orientation_deg != selected.selected_orientation_deg:
        raise PlateAuthorityError(
            "Native plate execution orientation differs from the selected sliced winner."
        )
    if build.selected_review_project_sha256 != selected_chain[1]:
        raise PlateAuthorityError(
            "Per-instance build-item proof is not bound to the exact selected review 3MF."
        )
    if instance_evidence.selected_printer_native_sha256 != selected_chain[4]:
        raise PlateAuthorityError(
            "Per-instance source envelope evidence is not bound to the exact selected printer-native winner."
        )

    project_hash = _sha256("materialized project sha256", materialized.project_sha256)
    if (
        build.project_sha256 != project_hash
        or native_execution.materialized_project_sha256 != project_hash
        or artifact.project_sha256 != project_hash
        or whole_plate_native_evidence.materialized_project_sha256 != project_hash
    ):
        raise PlateAuthorityError(
            "Materialized 3MF hash differs across instance proof, native execution or whole-plate validation."
        )
    if not artifact.project_bytes or hashlib.sha256(artifact.project_bytes).hexdigest() != project_hash:
        raise PlateAuthorityError(
            "Native execution did not retain exact materialized 3MF bytes matching the project receipt."
        )
    if (
        not artifact.effective_config_bytes
        or hashlib.sha256(artifact.effective_config_bytes).hexdigest() != selected_chain[2]
        or artifact.effective_config_sha256 != selected_chain[2]
    ):
        raise PlateAuthorityError(
            "Native execution effective config is not the exact retained selected-winner recipe."
        )

    intermediate_hash = _sha256(
        "plate intermediate sha256",
        artifact.intermediate_sha256,
    )
    native_hash = _sha256("plate native sha256", artifact.native_sha256)
    if (
        not artifact.intermediate_bytes
        or hashlib.sha256(artifact.intermediate_bytes).hexdigest() != intermediate_hash
    ):
        raise PlateAuthorityError(
            "Plate intermediate SLA bytes do not match their exact SHA-256 receipt."
        )
    if not artifact.native_bytes or hashlib.sha256(artifact.native_bytes).hexdigest() != native_hash:
        raise PlateAuthorityError(
            "Plate printer-native bytes do not match their exact SHA-256 receipt."
        )
    if whole_plate_native_evidence.printer_native_sha256 != native_hash:
        raise PlateAuthorityError(
            "Whole-plate native envelope proof is not bound to the exact final printer-native file."
        )
    if _artifact_observed_envelope(native_execution) != whole_plate_native_evidence.observed_display_envelope:
        raise PlateAuthorityError(
            "Whole-plate native envelope evidence differs from the exact UVtools metrics retained on the final native artifact."
        )

    issues = _validate_issue_summary(artifact.issue_summary)
    if critical_issue_count(issues) != 0:
        raise PlateAuthorityError(
            "Final printer-native plate contains critical UVtools resin-print issues."
        )

    return SelectedPlateAuthorityEvidence(
        printer_profile_id=printer.id,
        plate_index=selected.plate_index,
        source_sha256=selected_chain[0],
        selected_review_3mf_sha256=selected_chain[1],
        selected_effective_config_sha256=selected_chain[2],
        selected_intermediate_sl1_sha256=selected_chain[3],
        selected_printer_native_sha256=selected_chain[4],
        materialized_project_sha256=project_hash,
        plate_intermediate_sha256=intermediate_hash,
        plate_printer_native_sha256=native_hash,
        issue_summary=issues,
        instance_evidence=instance_evidence,
        native_execution=native_execution,
        whole_plate_native_evidence=whole_plate_native_evidence,
    )


def selected_plate_authority_manifest(
    evidence: SelectedPlateAuthorityEvidence,
) -> dict:
    return {
        "schema": PLATE_AUTHORITY_SCHEMA,
        "printer_profile_id": evidence.printer_profile_id,
        "plate_index": evidence.plate_index,
        "source_sha256": evidence.source_sha256,
        "selected_sliced_artifacts": {
            "review_3mf_sha256": evidence.selected_review_3mf_sha256,
            "effective_config_sha256": evidence.selected_effective_config_sha256,
            "intermediate_sl1_sha256": evidence.selected_intermediate_sl1_sha256,
            "printer_native_sha256": evidence.selected_printer_native_sha256,
        },
        "materialized_plate_artifacts": {
            "review_3mf_sha256": evidence.materialized_project_sha256,
            "intermediate_sl1_sha256": evidence.plate_intermediate_sha256,
            "printer_native_sha256": evidence.plate_printer_native_sha256,
        },
        "issues": dict(evidence.issue_summary),
        "instance_evidence": materialized_3mf_instance_evidence_manifest(
            evidence.instance_evidence
        ),
        "native_execution": selected_materialized_plate_native_manifest(
            evidence.native_execution
        ),
        "whole_plate_native_evidence": whole_plate_native_envelope_manifest(
            evidence.whole_plate_native_evidence
        ),
        "production_plate_authority_ready": True,
        "production_enablement_performed": False,
        "authority_rule": (
            "Plate production authority requires one uninterrupted exact provenance chain from selected sliced winner through deterministic per-instance 3MF materialization, exact retained-config native slicing, zero critical UVtools issues, and matching final native whole-print bounds. This evidence object does not enable production, change profile readiness, merge code, deploy a service, or bypass printer-specific physical validation."
        ),
    }
