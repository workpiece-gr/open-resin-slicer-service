from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .materialization_selected import (
    SelectedMaterializedPlateEvidence,
    selected_materialized_plate_manifest,
)
from .order import PlateArtifactRecord, build_order_manifest
from .orientation_plate import (
    SelectedOrientationPlatePlan,
    orientation_plate_plan_manifest,
)
from .plate_authority import (
    SelectedPlateAuthorityEvidence,
    selected_plate_authority_manifest,
)
from .toolchain import ToolchainProvenanceError, validate_toolchain_record


SELECTED_ORDER_SCHEMA = "workpiece-resin-order-manifest-v4"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRODUCTION_AUTHORITY = "production-authoritative"


class SelectedOrientationOrderError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SelectedOrientationOrderError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


def _issues(value: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, count in sorted(value.items()):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SelectedOrientationOrderError(
                "Selected plate issue counts must be non-negative integers."
            )
        result[str(key)] = count
    return result


@dataclass(frozen=True)
class SelectedPlateArtifactRecord:
    plate_index: int
    project_filename: str
    intermediate_filename: str
    intermediate_sha256: str
    native_filename: str
    native_sha256: str
    issue_summary: Mapping[str, int]
    materialization: SelectedMaterializedPlateEvidence
    authority_evidence: SelectedPlateAuthorityEvidence | None = None


def _validate_selected_materialization(
    selected_plan: SelectedOrientationPlatePlan,
    record: SelectedPlateArtifactRecord,
) -> PlateArtifactRecord:
    evidence = record.materialization
    if evidence.plate_index != record.plate_index:
        raise SelectedOrientationOrderError(
            "Selected materialization plate_index does not match its artifact record."
        )
    if _sha256("materialization source_sha256", evidence.source_sha256) != _sha256(
        "selected plan source_sha256", selected_plan.source_sha256
    ):
        raise SelectedOrientationOrderError(
            "Plate materialization source hash does not match the selected orientation plan."
        )
    if evidence.selected_orientation_deg != selected_plan.orientation_deg:
        raise SelectedOrientationOrderError(
            "Plate materialization orientation does not match the selected sliced winner."
        )

    expected_hashes = (
        _sha256("selected review 3MF", selected_plan.review_project_sha256),
        _sha256("selected effective config", selected_plan.effective_config_sha256),
        _sha256("selected intermediate SL1", selected_plan.intermediate_sha256),
        _sha256("selected printer-native file", selected_plan.native_sha256),
    )
    actual_hashes = (
        _sha256("materialization selected review 3MF", evidence.selected_review_3mf_sha256),
        _sha256(
            "materialization selected effective config",
            evidence.selected_effective_config_sha256,
        ),
        _sha256(
            "materialization selected intermediate SL1",
            evidence.selected_intermediate_sl1_sha256,
        ),
        _sha256(
            "materialization selected printer-native file",
            evidence.selected_printer_native_sha256,
        ),
    )
    if actual_hashes != expected_hashes:
        raise SelectedOrientationOrderError(
            "Plate materialization does not derive from the exact selected sliced artifact chain."
        )

    materialized = evidence.materialized_plate
    if materialized.printer_profile_id != selected_plan.printer_plate_plan.printer_profile_id:
        raise SelectedOrientationOrderError(
            "Plate materialization printer profile does not match the selected plate plan."
        )

    return PlateArtifactRecord(
        plate_index=record.plate_index,
        project_filename=record.project_filename,
        project_sha256=evidence.project_sha256,
        intermediate_filename=record.intermediate_filename,
        intermediate_sha256=record.intermediate_sha256,
        native_filename=record.native_filename,
        native_sha256=record.native_sha256,
        issue_summary=record.issue_summary,
        materialization=materialized,
    )


def _validate_plate_authority_binding(
    selected_plan: SelectedOrientationPlatePlan,
    record: SelectedPlateArtifactRecord,
) -> SelectedPlateAuthorityEvidence:
    authority = record.authority_evidence
    if authority is None:
        raise SelectedOrientationOrderError(
            "Production-authoritative selected orders require complete plate authority evidence for every physical plate."
        )
    if authority.plate_index != record.plate_index:
        raise SelectedOrientationOrderError(
            "Plate authority evidence plate_index does not match its artifact record."
        )
    if authority.printer_profile_id != selected_plan.printer_plate_plan.printer_profile_id:
        raise SelectedOrientationOrderError(
            "Plate authority evidence printer profile does not match the selected plate plan."
        )

    expected_selected_chain = (
        _sha256("selected plan source_sha256", selected_plan.source_sha256),
        _sha256("selected plan review 3MF", selected_plan.review_project_sha256),
        _sha256("selected plan effective config", selected_plan.effective_config_sha256),
        _sha256("selected plan intermediate SL1", selected_plan.intermediate_sha256),
        _sha256("selected plan printer-native file", selected_plan.native_sha256),
    )
    authority_selected_chain = (
        _sha256("authority source_sha256", authority.source_sha256),
        _sha256("authority selected review 3MF", authority.selected_review_3mf_sha256),
        _sha256(
            "authority selected effective config",
            authority.selected_effective_config_sha256,
        ),
        _sha256(
            "authority selected intermediate SL1",
            authority.selected_intermediate_sl1_sha256,
        ),
        _sha256(
            "authority selected printer-native file",
            authority.selected_printer_native_sha256,
        ),
    )
    if authority_selected_chain != expected_selected_chain:
        raise SelectedOrientationOrderError(
            "Plate authority evidence does not derive from the exact selected sliced artifact chain."
        )

    if authority.instance_evidence.materialized_plate != record.materialization:
        raise SelectedOrientationOrderError(
            "Plate authority instance evidence does not match the exact selected materialization record."
        )
    project_hash = _sha256("record materialized project", record.materialization.project_sha256)
    if _sha256("authority materialized project", authority.materialized_project_sha256) != project_hash:
        raise SelectedOrientationOrderError(
            "Plate authority evidence is not bound to the exact retained materialized review 3MF."
        )

    intermediate_hash = _sha256("record plate intermediate SL1", record.intermediate_sha256)
    native_hash = _sha256("record plate printer-native file", record.native_sha256)
    if _sha256("authority plate intermediate SL1", authority.plate_intermediate_sha256) != intermediate_hash:
        raise SelectedOrientationOrderError(
            "Plate artifact intermediate SL1 hash does not match its production authority evidence."
        )
    if _sha256("authority plate printer-native file", authority.plate_printer_native_sha256) != native_hash:
        raise SelectedOrientationOrderError(
            "Plate artifact printer-native hash does not match its production authority evidence."
        )

    record_issues = _issues(record.issue_summary)
    authority_issues = _issues(authority.issue_summary)
    if record_issues != authority_issues:
        raise SelectedOrientationOrderError(
            "Plate artifact issue summary does not match its production authority evidence."
        )

    execution = authority.native_execution
    if execution.plate_index != record.plate_index:
        raise SelectedOrientationOrderError(
            "Plate authority native execution index does not match its artifact record."
        )
    if _sha256("authority execution materialized project", execution.materialized_project_sha256) != project_hash:
        raise SelectedOrientationOrderError(
            "Plate authority native execution is not bound to the exact retained materialized review 3MF."
        )
    artifact = execution.artifact
    if _sha256("authority native artifact intermediate", artifact.intermediate_sha256) != intermediate_hash:
        raise SelectedOrientationOrderError(
            "Plate authority native execution intermediate hash differs from the retained order file."
        )
    if _sha256("authority native artifact", artifact.native_sha256) != native_hash:
        raise SelectedOrientationOrderError(
            "Plate authority native execution hash differs from the retained order printer-native file."
        )
    if _issues(artifact.issue_summary) != record_issues:
        raise SelectedOrientationOrderError(
            "Plate authority native execution issue receipt differs from the retained order issue summary."
        )

    whole_plate = authority.whole_plate_native_evidence
    if whole_plate.plate_index != record.plate_index:
        raise SelectedOrientationOrderError(
            "Whole-plate native authority evidence index does not match its artifact record."
        )
    if _sha256("whole-plate materialized project", whole_plate.materialized_project_sha256) != project_hash:
        raise SelectedOrientationOrderError(
            "Whole-plate native authority evidence is not bound to the exact materialized review 3MF."
        )
    if _sha256("whole-plate printer-native file", whole_plate.printer_native_sha256) != native_hash:
        raise SelectedOrientationOrderError(
            "Whole-plate native authority evidence is not bound to the exact retained printer-native file."
        )
    return authority


def build_selected_orientation_order_manifest(
    *,
    source_filename: str,
    source_sha256: str,
    requested_quantity: int,
    printer_profile: str,
    resin_profile: str,
    quality_profile: str,
    selected_orientation_plan: SelectedOrientationPlatePlan,
    plate_artifacts: Sequence[SelectedPlateArtifactRecord],
    prusaslicer_version: str,
    prusaslicer_commit: str,
    uvtools_version: str,
    authority: str,
    execution_environment: Mapping[str, object] | None = None,
) -> dict:
    """Build an order whose source, selected winner and physical plates share one exact chain.

    Acceptance-candidate orders retain the earlier selected-materialization contract and do
    not require a production authority object or execution-environment record. A
    production-authoritative order requires one complete, already-validated
    ``SelectedPlateAuthorityEvidence`` for every physical plate plus an immutable
    digest-pinned toolchain record. This function records authority only; it does not
    enable a printer or production route.
    """
    source_hash = _sha256("source_sha256", source_sha256)
    selected_source_hash = _sha256(
        "selected_orientation_plan.source_sha256",
        selected_orientation_plan.source_sha256,
    )
    if source_hash != selected_source_hash:
        raise SelectedOrientationOrderError(
            "Order source STL hash does not match the source bound to sliced orientation validation."
        )

    orientation = {
        "x": selected_orientation_plan.orientation_deg[0],
        "y": selected_orientation_plan.orientation_deg[1],
        "z": selected_orientation_plan.orientation_deg[2],
    }
    lower_records = tuple(
        _validate_selected_materialization(selected_orientation_plan, record)
        for record in plate_artifacts
    )
    authority_value = str(authority).strip()
    authority_by_plate: dict[int, SelectedPlateAuthorityEvidence] = {}
    if authority_value == _PRODUCTION_AUTHORITY:
        for record in plate_artifacts:
            plate_authority = _validate_plate_authority_binding(
                selected_orientation_plan,
                record,
            )
            if record.plate_index in authority_by_plate:
                raise SelectedOrientationOrderError(
                    f"Duplicate production authority evidence for plate {record.plate_index}."
                )
            authority_by_plate[record.plate_index] = plate_authority
    else:
        for record in plate_artifacts:
            if record.authority_evidence is not None:
                authority_by_plate[record.plate_index] = _validate_plate_authority_binding(
                    selected_orientation_plan,
                    record,
                )

    normalized_execution_environment: dict[str, object] | None = None
    if execution_environment is not None or authority_value == _PRODUCTION_AUTHORITY:
        try:
            normalized_execution_environment = validate_toolchain_record(
                execution_environment,
                required=authority_value == _PRODUCTION_AUTHORITY,
            )
        except ToolchainProvenanceError as exc:
            raise SelectedOrientationOrderError(str(exc)) from exc

    manifest = build_order_manifest(
        source_filename=source_filename,
        source_sha256=source_hash,
        requested_quantity=requested_quantity,
        orientation_deg=orientation,
        printer_profile=printer_profile,
        resin_profile=resin_profile,
        quality_profile=quality_profile,
        printer_plate_plan=selected_orientation_plan.printer_plate_plan,
        plate_artifacts=lower_records,
        prusaslicer_version=prusaslicer_version,
        prusaslicer_commit=prusaslicer_commit,
        uvtools_version=uvtools_version,
        authority=authority,
    )

    selected_by_plate = {record.plate_index: record.materialization for record in plate_artifacts}
    plates = []
    for plate in manifest.get("plates", []):
        item = dict(plate)
        plate_index = item.get("plate_index")
        if plate_index not in selected_by_plate:
            raise SelectedOrientationOrderError(
                "Lower-level order manifest contains a plate without selected materialization evidence."
            )
        item["selected_materialization"] = selected_materialized_plate_manifest(
            selected_by_plate[plate_index]
        )
        if plate_index in authority_by_plate:
            item["plate_authority"] = selected_plate_authority_manifest(
                authority_by_plate[plate_index]
            )
        else:
            item["plate_authority"] = None
        plates.append(item)

    if authority_value == _PRODUCTION_AUTHORITY:
        manifest_indices = {item.get("plate_index") for item in plates}
        if set(authority_by_plate) != manifest_indices:
            raise SelectedOrientationOrderError(
                "Production-authoritative selected order does not contain complete plate authority evidence for every physical plate."
            )

    result = dict(manifest)
    result["schema"] = SELECTED_ORDER_SCHEMA
    result["plates"] = plates
    result["selected_orientation_plan"] = orientation_plate_plan_manifest(
        selected_orientation_plan
    )
    if normalized_execution_environment is not None:
        result["execution_environment"] = normalized_execution_environment
    result["production_enablement_performed"] = False
    result["review_rule"] = (
        str(manifest.get("review_rule", "")).rstrip()
        + " The order source, orientation, effective config, selected sliced artifact chain, plate materialization and final plate files must all remain bound; callers cannot substitute an independent orientation or unrelated materialized plate. Production-authoritative selected orders additionally require one complete plate-authority proof per physical plate, bound to the exact retained 3MF/SL1/native hashes and issue receipt, plus an immutable digest-pinned runtime toolchain receipt. Recording this manifest does not enable production."
    ).strip()
    return result
