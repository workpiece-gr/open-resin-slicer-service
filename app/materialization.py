from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

from app.placement import (
    Envelope2D,
    InstanceTranslation,
    PlacementError,
    derive_plate_translations,
    validate_materialized_plate,
)
from app.plate import PlatePlan, PrinterPlatePlan


class MaterializationError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_SOURCE = "re-extracted-materialized-project"


@dataclass(frozen=True)
class PlateMaterializationSpec:
    printer_profile_id: str
    manufacturing_envelope_coordinate_mapping: str
    plan: PlatePlan
    plate_index: int
    pretranslation_envelope: Envelope2D
    translations: tuple[InstanceTranslation, ...]
    automatic_materialization_authority: bool


@dataclass(frozen=True)
class MaterializedEnvelopeObservation:
    instance_index: int
    envelope: Envelope2D
    project_sha256: str
    source: str = _OBSERVATION_SOURCE

    def __post_init__(self) -> None:
        if isinstance(self.instance_index, bool) or not isinstance(self.instance_index, int) or self.instance_index < 1:
            raise MaterializationError("instance_index must be a positive integer.")
        if not _SHA256_RE.fullmatch(self.project_sha256):
            raise MaterializationError("project_sha256 must be a lowercase SHA-256 digest.")
        if self.source != _OBSERVATION_SOURCE:
            raise MaterializationError(
                "Materialized envelopes must be re-extracted from the exact materialized project."
            )


@dataclass(frozen=True)
class MaterializedPlateEvidence:
    printer_profile_id: str
    manufacturing_envelope_coordinate_mapping: str
    plate_index: int
    project_sha256: str
    translations: tuple[InstanceTranslation, ...]
    observations: tuple[MaterializedEnvelopeObservation, ...]
    automatic_materialization_authority: bool


def prepare_printer_plate_materialization(
    profile_plan: PrinterPlatePlan,
    *,
    plate_index: int,
    pretranslation_envelope: Envelope2D,
    require_validated_mapping: bool = True,
) -> PlateMaterializationSpec:
    """Prepare exact translations for one physical plate.

    The input envelope must already represent the oriented, supported and padded object
    before XY translation. Automatic physical materialization is blocked while the
    printer's manufacturing-envelope coordinate mapping remains unverified.
    """
    mapping = profile_plan.manufacturing_envelope_coordinate_mapping
    if require_validated_mapping and mapping != "validated":
        raise MaterializationError(
            "Printer manufacturing-envelope coordinate mapping is not validated; automatic physical materialization is blocked."
        )
    try:
        translations = derive_plate_translations(
            profile_plan.plan,
            plate_index=plate_index,
            pretranslation_envelope=pretranslation_envelope,
        )
    except PlacementError as exc:
        raise MaterializationError(str(exc)) from exc

    return PlateMaterializationSpec(
        printer_profile_id=profile_plan.printer_profile_id,
        manufacturing_envelope_coordinate_mapping=mapping,
        plan=profile_plan.plan,
        plate_index=plate_index,
        pretranslation_envelope=pretranslation_envelope,
        translations=translations,
        automatic_materialization_authority=mapping == "validated",
    )


def finalize_materialized_plate(
    spec: PlateMaterializationSpec,
    *,
    project_bytes: bytes,
    observations: Iterable[MaterializedEnvelopeObservation],
) -> MaterializedPlateEvidence:
    """Bind re-extracted final envelopes to the exact per-plate 3MF and validate them.

    This function does not infer envelopes from planned geometry. Callers must provide
    observations extracted from the exact materialized project bytes. Any support/pad
    drift, missing instance, spacing violation or margin violation fails closed.
    """
    if not isinstance(project_bytes, bytes) or not project_bytes:
        raise MaterializationError("project_bytes must contain the exact non-empty materialized 3MF bytes.")
    project_sha256 = hashlib.sha256(project_bytes).hexdigest()
    observation_tuple = tuple(observations)
    if not observation_tuple:
        raise MaterializationError("At least one materialized envelope observation is required.")

    envelopes: dict[int, Envelope2D] = {}
    for observation in observation_tuple:
        if observation.project_sha256 != project_sha256:
            raise MaterializationError(
                f"Envelope observation for instance {observation.instance_index} is not bound to the exact materialized project."
            )
        if observation.instance_index in envelopes:
            raise MaterializationError(
                f"Duplicate materialized envelope observation for instance {observation.instance_index}."
            )
        envelopes[observation.instance_index] = observation.envelope

    try:
        validate_materialized_plate(
            spec.plan,
            plate_index=spec.plate_index,
            materialized_envelopes=envelopes,
        )
    except PlacementError as exc:
        raise MaterializationError(str(exc)) from exc

    return MaterializedPlateEvidence(
        printer_profile_id=spec.printer_profile_id,
        manufacturing_envelope_coordinate_mapping=spec.manufacturing_envelope_coordinate_mapping,
        plate_index=spec.plate_index,
        project_sha256=project_sha256,
        translations=spec.translations,
        observations=observation_tuple,
        automatic_materialization_authority=spec.automatic_materialization_authority,
    )


def materialized_plate_manifest(evidence: MaterializedPlateEvidence) -> dict:
    return {
        "schema": "workpiece-resin-materialized-plate-v1",
        "printer_profile_id": evidence.printer_profile_id,
        "plate_index": evidence.plate_index,
        "project_sha256": evidence.project_sha256,
        "manufacturing_envelope_coordinate_mapping": (
            evidence.manufacturing_envelope_coordinate_mapping
        ),
        "automatic_materialization_authority": evidence.automatic_materialization_authority,
        "envelope_observation_source": _OBSERVATION_SOURCE,
        "validation_rule": (
            "final supported/padded envelopes are re-extracted from the exact per-plate 3MF and must remain inside planned slots, margins and spacing"
        ),
        "translations": [
            {
                "instance_index": item.instance_index,
                "target_x_mm": item.target_x_mm,
                "target_y_mm": item.target_y_mm,
                "translate_x_mm": item.translate_x_mm,
                "translate_y_mm": item.translate_y_mm,
                "rotation_z_deg": item.rotation_z_deg,
            }
            for item in evidence.translations
        ],
        "materialized_envelopes": [
            {
                "instance_index": item.instance_index,
                "min_x_mm": item.envelope.min_x_mm,
                "max_x_mm": item.envelope.max_x_mm,
                "min_y_mm": item.envelope.min_y_mm,
                "max_y_mm": item.envelope.max_y_mm,
                "project_sha256": item.project_sha256,
            }
            for item in evidence.observations
        ],
    }
