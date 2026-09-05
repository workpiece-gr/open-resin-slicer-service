from __future__ import annotations

import re
from dataclasses import dataclass

from .orientation_sliced import SlicedOrientationValidation
from .placement import Envelope2D
from .plate import (
    PrinterPlatePlan,
    plan_printer_profile_instances,
    printer_plate_plan_manifest,
)
from .profiles import ProfileRegistry


ORIENTATION_PLATE_SCHEMA = "workpiece-resin-orientation-plate-plan-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OrientationPlatePlanError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise OrientationPlatePlanError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


@dataclass(frozen=True)
class SelectedOrientationPlatePlan:
    orientation_deg: tuple[float, float, float]
    source_sha256: str
    review_project_sha256: str
    intermediate_sha256: str
    native_sha256: str
    pretranslation_envelope: Envelope2D
    printer_plate_plan: PrinterPlatePlan


def plan_selected_sliced_orientation(
    *,
    registry: ProfileRegistry,
    printer_profile_id: str,
    sliced_validation: SlicedOrientationValidation,
    review_project_sha256: str,
    pretranslation_envelope: Envelope2D,
    quantity: int,
    spacing_mm: float = 5.0,
    edge_margin_mm: float = 3.0,
    allow_rotate_90: bool = True,
) -> SelectedOrientationPlatePlan:
    """Pack only the exact selected sliced 3MF's supported/padded XY envelope."""
    selected = sliced_validation.selected_evidence
    if selected is None:
        raise OrientationPlatePlanError(
            "Sliced orientation validation has no selected finalist; plate planning requires manual review."
        )

    supplied_hash = _sha256("review_project_sha256", review_project_sha256)
    selected_hash = _sha256(
        "selected review_project_sha256", selected.review_project_sha256
    )
    if supplied_hash != selected_hash:
        raise OrientationPlatePlanError(
            "Supported/padded envelope is not bound to the selected sliced review 3MF; re-extract from the exact selected project."
        )

    source_hash = _sha256("source_sha256", sliced_validation.source_sha256)
    selected_source_hash = _sha256("selected source_sha256", selected.source_sha256)
    if source_hash != selected_source_hash:
        raise OrientationPlatePlanError(
            "Selected sliced evidence source hash does not match its validation bundle."
        )

    if not isinstance(pretranslation_envelope, Envelope2D):
        raise OrientationPlatePlanError(
            "pretranslation_envelope must be an Envelope2D extracted from the selected supported/padded project."
        )

    profile_plan = plan_printer_profile_instances(
        registry=registry,
        printer_profile_id=printer_profile_id,
        footprint_width_mm=pretranslation_envelope.width_mm,
        footprint_depth_mm=pretranslation_envelope.depth_mm,
        quantity=quantity,
        spacing_mm=spacing_mm,
        edge_margin_mm=edge_margin_mm,
        allow_rotate_90=allow_rotate_90,
    )
    return SelectedOrientationPlatePlan(
        orientation_deg=selected.canonical_key,
        source_sha256=source_hash,
        review_project_sha256=selected_hash,
        intermediate_sha256=_sha256(
            "selected intermediate_sha256", selected.intermediate_sha256
        ),
        native_sha256=_sha256("selected native_sha256", selected.native_sha256),
        pretranslation_envelope=pretranslation_envelope,
        printer_plate_plan=profile_plan,
    )


def orientation_plate_plan_manifest(result: SelectedOrientationPlatePlan) -> dict:
    envelope = result.pretranslation_envelope
    plate_manifest = printer_plate_plan_manifest(result.printer_plate_plan)
    return {
        "schema": ORIENTATION_PLATE_SCHEMA,
        "automatic_materialization_authority": plate_manifest[
            "automatic_materialization_authority"
        ],
        "source_sha256": _sha256("source_sha256", result.source_sha256),
        "selected_orientation_deg": {
            "x": result.orientation_deg[0],
            "y": result.orientation_deg[1],
            "z": result.orientation_deg[2],
        },
        "selected_review_3mf_sha256": _sha256(
            "review_project_sha256", result.review_project_sha256
        ),
        "selected_sliced_artifacts": {
            "review_3mf_sha256": _sha256(
                "review_project_sha256", result.review_project_sha256
            ),
            "intermediate_sl1_sha256": _sha256(
                "intermediate_sha256", result.intermediate_sha256
            ),
            "printer_native_sha256": _sha256("native_sha256", result.native_sha256),
        },
        "supported_pretranslation_envelope_mm": {
            "min_x": envelope.min_x_mm,
            "max_x": envelope.max_x_mm,
            "min_y": envelope.min_y_mm,
            "max_y": envelope.max_y_mm,
            "width": envelope.width_mm,
            "depth": envelope.depth_mm,
        },
        "plate_plan": plate_manifest,
        "review_rule": (
            "Packing dimensions come only from the actual supported/padded envelope extracted from the exact selected sliced review 3MF for this exact source STL. "
            "If source, orientation, supports, pad geometry, or the retained 3MF changes, discard this plan and re-extract/replan."
        ),
    }
