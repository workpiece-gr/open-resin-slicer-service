from __future__ import annotations

import re
from typing import Sequence

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


def build_selected_orientation_order_manifest(
    *,
    source_filename: str,
    source_sha256: str,
    requested_quantity: int,
    printer_profile: str,
    resin_profile: str,
    quality_profile: str,
    selected_orientation_plan: SelectedOrientationPlatePlan,
    plate_artifacts: Sequence[PlateArtifactRecord],
    prusaslicer_version: str,
    prusaslicer_commit: str,
    uvtools_version: str,
    authority: str,
) -> dict:
    """Build an order whose orientation and plate plan are derived from one selected sliced chain."""
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
    manifest = build_order_manifest(
        source_filename=source_filename,
        source_sha256=source_hash,
        requested_quantity=requested_quantity,
        orientation_deg=orientation,
        printer_profile=printer_profile,
        resin_profile=resin_profile,
        quality_profile=quality_profile,
        printer_plate_plan=selected_orientation_plan.printer_plate_plan,
        plate_artifacts=plate_artifacts,
        prusaslicer_version=prusaslicer_version,
        prusaslicer_commit=prusaslicer_commit,
        uvtools_version=uvtools_version,
        authority=authority,
    )

    result = dict(manifest)
    result["schema"] = SELECTED_ORDER_SCHEMA
    result["selected_orientation_plan"] = orientation_plate_plan_manifest(
        selected_orientation_plan
    )
    result["review_rule"] = (
        str(manifest.get("review_rule", "")).rstrip()
        + " The order orientation is derived from the exact sliced-validation winner; callers cannot substitute independent orientation angles."
    ).strip()
    return result
