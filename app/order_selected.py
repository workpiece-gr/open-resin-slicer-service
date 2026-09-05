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


SELECTED_ORDER_SCHEMA = "workpiece-resin-order-manifest-v3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SelectedOrientationOrderError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SelectedOrientationOrderError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


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
) -> dict:
    """Build an order whose source, orientation and physical plates share one provenance chain."""
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
        plates.append(item)

    result = dict(manifest)
    result["schema"] = SELECTED_ORDER_SCHEMA
    result["plates"] = plates
    result["selected_orientation_plan"] = orientation_plate_plan_manifest(
        selected_orientation_plan
    )
    result["review_rule"] = (
        str(manifest.get("review_rule", "")).rstrip()
        + " The order source, orientation, effective config, selected sliced artifact chain, plate materialization and final plate files must all remain bound; callers cannot substitute an independent orientation or unrelated materialized plate."
    ).strip()
    return result
