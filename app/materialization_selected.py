from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable

from .coordinate_mapping import CoordinateMappingError
from .materialization import (
    MaterializedEnvelopeObservation,
    MaterializedPlateEvidence,
    PlateMaterializationSpec,
    finalize_materialized_plate,
    materialized_plate_manifest,
    prepare_printer_plate_materialization,
)
from .orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    SelectedOrientationPlatePlan,
)
from .profiles import ProfileError, ProfileRegistry
from .prusa_3mf_instances import (
    DisplayInstancePlacement,
    Materialized3MFProject,
    ThreeMFMaterializationError,
    materialize_prusa_project_instances,
)


SELECTED_MATERIALIZATION_SCHEMA = "workpiece-resin-selected-materialized-plate-v2"
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
    selected_effective_config_sha256: str
    selected_intermediate_sl1_sha256: str
    selected_printer_native_sha256: str
    plate_spec: PlateMaterializationSpec


@dataclass(frozen=True)
class SelectedPlateProjectMaterialization:
    spec: SelectedPlateMaterializationSpec
    display_placements: tuple[DisplayInstancePlacement, ...]
    project: Materialized3MFProject


@dataclass(frozen=True)
class SelectedMaterializedPlateEvidence:
    source_sha256: str
    selected_orientation_deg: tuple[float, float, float]
    selected_review_3mf_sha256: str
    selected_effective_config_sha256: str
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
    """Prepare one plate only from the exact source-bound sliced-orientation winner.

    Native-display envelope coordinates are valid for candidate packing dimensions but
    cannot drive automatic physical translation. Default materialization therefore
    requires both a manufacturing-space pretranslation envelope and the exact validated
    transform that produced it to be bound into the selected plan.
    """
    source_hash = _sha256("source_sha256", selected_plan.source_sha256)
    review_hash = _sha256(
        "selected_review_3mf_sha256", selected_plan.review_project_sha256
    )
    config_hash = _sha256(
        "selected_effective_config_sha256", selected_plan.effective_config_sha256
    )
    intermediate_hash = _sha256(
        "selected_intermediate_sl1_sha256", selected_plan.intermediate_sha256
    )
    native_hash = _sha256(
        "selected_printer_native_sha256", selected_plan.native_sha256
    )

    coordinate_ready = (
        selected_plan.pretranslation_coordinate_space
        == MANUFACTURING_ENVELOPE_COORDINATE_SPACE
        and selected_plan.manufacturing_to_display_transform is not None
    )
    if require_validated_mapping and not coordinate_ready:
        raise SelectedMaterializationError(
            "Selected supported envelope is not bound to a validated manufacturing-to-display transform; a physical mapping transform is required before automatic materialization."
        )

    plate_spec = prepare_printer_plate_materialization(
        selected_plan.printer_plate_plan,
        plate_index=plate_index,
        pretranslation_envelope=selected_plan.pretranslation_envelope,
        require_validated_mapping=require_validated_mapping,
    )
    if not coordinate_ready and plate_spec.automatic_materialization_authority:
        plate_spec = replace(plate_spec, automatic_materialization_authority=False)

    return SelectedPlateMaterializationSpec(
        source_sha256=source_hash,
        selected_orientation_deg=selected_plan.orientation_deg,
        selected_review_3mf_sha256=review_hash,
        selected_effective_config_sha256=config_hash,
        selected_intermediate_sl1_sha256=intermediate_hash,
        selected_printer_native_sha256=native_hash,
        plate_spec=plate_spec,
    )


def materialize_selected_plate_project(
    selected_plan: SelectedOrientationPlatePlan,
    *,
    registry: ProfileRegistry,
    plate_index: int,
    selected_review_project_bytes: bytes,
) -> SelectedPlateProjectMaterialization:
    """Create the exact per-plate Prusa 3MF from validated manufacturing placements.

    The exact transform used to create the manufacturing-space plan is retained in the
    selected plan. Materialization first verifies that the current printer profile still
    resolves to the same transform, preventing mapping drift between planning and output.
    Manufacturing target centres are then converted into display millimetres. A reflected
    mapping reverses the sign of a common +90 degree plate rotation. The exact CTB-bound
    native display envelope remains the rotation pivot for the selected review project.
    """
    spec = prepare_selected_plate_materialization(
        selected_plan,
        plate_index=plate_index,
        require_validated_mapping=True,
    )
    printer_profile_id = selected_plan.printer_plate_plan.printer_profile_id
    source_display_envelope = selected_plan.native_display_envelope
    bound_transform = selected_plan.manufacturing_to_display_transform
    if source_display_envelope is None:
        raise SelectedMaterializationError(
            "Selected plan does not retain the exact CTB-bound native display envelope required for 3MF materialization."
        )
    if bound_transform is None:
        raise SelectedMaterializationError(
            "Selected plan does not retain the validated manufacturing-to-display transform used for planning."
        )
    try:
        current_transform = registry.printer_manufacturing_display_transform(
            printer_profile_id
        )
    except ProfileError as exc:
        raise SelectedMaterializationError(str(exc)) from exc
    if current_transform != bound_transform:
        raise SelectedMaterializationError(
            "Printer manufacturing-to-display transform changed after the selected plate plan was created; replan before materialization."
        )

    try:
        placements: list[DisplayInstancePlacement] = []
        for item in spec.plate_spec.translations:
            display_x, display_y = bound_transform.to_display(
                item.target_x_mm,
                item.target_y_mm,
            )
            placements.append(
                DisplayInstancePlacement(
                    instance_index=item.instance_index,
                    target_display_x_mm=display_x,
                    target_display_y_mm=display_y,
                    rotation_z_deg=bound_transform.display_rotation_for_manufacturing(
                        item.rotation_z_deg
                    ),
                )
            )
        display_placements = tuple(placements)
    except (CoordinateMappingError, ThreeMFMaterializationError) as exc:
        raise SelectedMaterializationError(str(exc)) from exc

    try:
        project = materialize_prusa_project_instances(
            selected_review_project_bytes,
            source_project_sha256=spec.selected_review_3mf_sha256,
            source_display_envelope=source_display_envelope,
            placements=display_placements,
        )
    except ThreeMFMaterializationError as exc:
        raise SelectedMaterializationError(str(exc)) from exc

    expected_indices = tuple(item.instance_index for item in spec.plate_spec.translations)
    if project.instance_indices != expected_indices:
        raise SelectedMaterializationError(
            "Materialized 3MF instance indices differ from the exact selected physical plate plan."
        )
    return SelectedPlateProjectMaterialization(
        spec=spec,
        display_placements=display_placements,
        project=project,
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
        selected_effective_config_sha256=_sha256(
            "selected_effective_config_sha256", spec.selected_effective_config_sha256
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
            "effective_config_sha256": _sha256(
                "selected_effective_config_sha256",
                evidence.selected_effective_config_sha256,
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
            "This per-plate 3MF output was materialized through the selected-orientation path from the exact source-bound sliced winner and its retained effective config; "
            "manufacturing target centres are mapped through the exact validated printer transform bound at planning time into display-space build-item transforms, the live profile must still match that transform, and final supported/padded envelopes must still be separately re-extracted and bound to the exact plate 3MF hash."
        ),
    }
