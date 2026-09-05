from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .materialization import (
    MaterializedEnvelopeObservation,
    MaterializedPlateEvidence,
    PlateMaterializationSpec,
    finalize_materialized_plate,
    materialized_plate_manifest,
    prepare_printer_plate_materialization,
)
from .orientation_plate import SelectedOrientationPlatePlan


SELECTED_MATERIALIZATION_SCHEMA = "workpiece-resin-selected-materialized-plate-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SelectedMaterializationError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SelectedMaterializationError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


@dataclass(frozen=True)
class SelectedPlateMaterializationSpec:
    source_sha256: str
    selected_orientation_deg: tuple[float, float, float]
    selected_review_3mf_sha256: str
    selected_intermediate_sl1_sha256: str
    selected_printer_native_sha256: str
    plate_spec: PlateMaterializationSpec


@dataclass(frozen=True)
class SelectedMaterializedPlateEvidence:
    source_sha256: str
    selected_orientation_deg: tuple[float, float, float]
    selected_review_3mf_sha256: str
    selected_intermediate_sl1_sha256: str
    selected_printer_native_sha256: str
    materialized_plate: MaterializedPlateEvidence

    @property
    def plate_index(self) -> int:
        return self.materialized_plate.plate_index

    @property
    def project_sha256(self) -> str:
        return self.materialized_plate.project_sha256


def prepare_selected_plate_materialization(
    selected_plan: SelectedOrientationPlatePlan,
    *,
    plate_index: int,
    require_validated_mapping: bool = True,
) -> SelectedPlateMaterializationSpec:
    """Prepare one plate only from the exact source-bound sliced-orientation winner."""
    source_hash = _sha256("source_sha256", selected_plan.source_sha256)
    review_hash = _sha256(
        "selected_review_3mf_sha256", selected_plan.review_project_sha256
    )
    intermediate_hash = _sha256(
        "selected_intermediate_sl1_sha256", selected_plan.intermediate_sha256
    )
    native_hash = _sha256(
        "selected_printer_native_sha256", selected_plan.native_sha256
    )

    plate_spec = prepare_printer_plate_materialization(
        selected_plan.printer_plate_plan,
        plate_index=plate_index,
        pretranslation_envelope=selected_plan.pretranslation_envelope,
        require_validated_mapping=require_validated_mapping,
    )
    return SelectedPlateMaterializationSpec(
        source_sha256=source_hash,
        selected_orientation_deg=selected_plan.orientation_deg,
        selected_review_3mf_sha256=review_hash,
        selected_intermediate_sl1_sha256=intermediate_hash,
        selected_printer_native_sha256=native_hash,
        plate_spec=plate_spec,
    )


def finalize_selected_materialized_plate(
    spec: SelectedPlateMaterializationSpec,
    *,
    project_bytes: bytes,
    observations: Iterable[MaterializedEnvelopeObservation],
) -> SelectedMaterializedPlateEvidence:
    """Bind the exact materialized plate output to its complete upstream sliced winner receipt."""
    materialized = finalize_materialized_plate(
        spec.plate_spec,
        project_bytes=project_bytes,
        observations=observations,
    )
    return SelectedMaterializedPlateEvidence(
        source_sha256=_sha256("source_sha256", spec.source_sha256),
        selected_orientation_deg=spec.selected_orientation_deg,
        selected_review_3mf_sha256=_sha256(
            "selected_review_3mf_sha256", spec.selected_review_3mf_sha256
        ),
        selected_intermediate_sl1_sha256=_sha256(
            "selected_intermediate_sl1_sha256", spec.selected_intermediate_sl1_sha256
        ),
        selected_printer_native_sha256=_sha256(
            "selected_printer_native_sha256", spec.selected_printer_native_sha256
        ),
        materialized_plate=materialized,
    )


def selected_materialized_plate_manifest(
    evidence: SelectedMaterializedPlateEvidence,
) -> dict:
    return {
        "schema": SELECTED_MATERIALIZATION_SCHEMA,
        "source_sha256": _sha256("source_sha256", evidence.source_sha256),
        "selected_orientation_deg": {
            "x": evidence.selected_orientation_deg[0],
            "y": evidence.selected_orientation_deg[1],
            "z": evidence.selected_orientation_deg[2],
        },
        "selected_sliced_artifacts": {
            "review_3mf_sha256": _sha256(
                "selected_review_3mf_sha256", evidence.selected_review_3mf_sha256
            ),
            "intermediate_sl1_sha256": _sha256(
                "selected_intermediate_sl1_sha256",
                evidence.selected_intermediate_sl1_sha256,
            ),
            "printer_native_sha256": _sha256(
                "selected_printer_native_sha256",
                evidence.selected_printer_native_sha256,
            ),
        },
        "materialized_plate": materialized_plate_manifest(evidence.materialized_plate),
        "provenance_rule": (
            "This per-plate 3MF output was materialized through the selected-orientation path from the exact source-bound sliced winner; "
            "its final supported/padded envelopes are separately re-extracted and bound to the exact plate 3MF hash."
        ),
    }
