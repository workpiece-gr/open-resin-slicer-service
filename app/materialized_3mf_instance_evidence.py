from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Sequence

from .coordinate_mapping import CoordinateMappingError
from .materialization import MaterializedEnvelopeObservation
from .materialization_selected import (
    SelectedMaterializedPlateEvidence,
    SelectedPlateProjectMaterialization,
    finalize_selected_materialized_plate,
)
from .orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    SelectedOrientationPlatePlan,
)
from .placement import Envelope2D, PlacementError
from .prusa_3mf_instances import (
    MAX_3MF_MEMBERS,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_BUILD_TAIL_BYTES,
    MAX_MATERIALIZED_INSTANCES,
    MAX_MODEL_XML_BYTES,
    MODEL_MEMBER,
)


INSTANCE_EVIDENCE_SCHEMA = "workpiece-resin-materialized-3mf-instance-evidence-v1"
VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE = "verified-materialized-3mf-build-items"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ITEM_RE = re.compile(rb"<item\b[^<>]*/>")
_TRANSFORM_RE = re.compile(rb'\btransform="([^"]*)"')
_OBJECT_ID_RE = re.compile(rb'\bobjectid="([0-9]+)"')


class Materialized3MFInstanceEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedBuildItem:
    ordinal: int
    object_id: int
    transform: tuple[float, ...]


@dataclass(frozen=True)
class VerifiedBuildItemEvidence:
    project_sha256: str
    object_id: int
    instance_indices: tuple[int, ...]
    build_items: tuple[ExtractedBuildItem, ...]


@dataclass(frozen=True)
class SelectedMaterialized3MFInstanceEvidence:
    materialized_plate: SelectedMaterializedPlateEvidence
    build_items: VerifiedBuildItemEvidence
    selected_printer_native_sha256: str
    source_display_envelope: Envelope2D
    display_envelopes: tuple[Envelope2D, ...]
    manufacturing_envelopes: tuple[Envelope2D, ...]


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise Materialized3MFInstanceEvidenceError(
            f"{name} must be a lowercase 64-character SHA-256 digest."
        )
    return normalized


def _parse_transform(raw: bytes) -> tuple[float, ...]:
    try:
        values = tuple(float(item) for item in raw.decode("ascii").split())
    except (UnicodeDecodeError, ValueError) as exc:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized 3MF build-item transform is not numeric."
        ) from exc
    if len(values) != 12 or not all(math.isfinite(value) for value in values):
        raise Materialized3MFInstanceEvidenceError(
            "Materialized 3MF build-item transform must contain exactly 12 finite numbers."
        )
    return values


def _canonical_written_transform(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != 12:
        raise Materialized3MFInstanceEvidenceError(
            "Recorded materialized 3MF transform must contain exactly 12 values."
        )
    result: list[float] = []
    for raw in values:
        if isinstance(raw, bool):
            raise Materialized3MFInstanceEvidenceError(
                "Recorded materialized 3MF transforms must contain finite numbers."
            )
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise Materialized3MFInstanceEvidenceError(
                "Recorded materialized 3MF transforms must contain finite numbers."
            ) from exc
        if not math.isfinite(value):
            raise Materialized3MFInstanceEvidenceError(
                "Recorded materialized 3MF transforms must contain finite numbers."
            )
        rendered = "0" if abs(value) < 5e-13 else format(value, ".9g")
        result.append(float(rendered))
    return tuple(result)


def _read_model_tail(project_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(project_bytes), "r") as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if not infos or len(infos) > MAX_3MF_MEMBERS:
                raise Materialized3MFInstanceEvidenceError(
                    "Materialized 3MF ZIP member count is outside the supported bound."
                )
            if len(names) != len(set(names)):
                raise Materialized3MFInstanceEvidenceError(
                    "Materialized 3MF contains duplicate ZIP members."
                )
            if sum(item.file_size for item in infos) > MAX_3MF_UNCOMPRESSED_BYTES:
                raise Materialized3MFInstanceEvidenceError(
                    "Materialized 3MF exceeds the bounded uncompressed project size."
                )
            model_infos = [item for item in infos if item.filename == MODEL_MEMBER]
            if len(model_infos) != 1:
                raise Materialized3MFInstanceEvidenceError(
                    f"Materialized 3MF must contain exactly one {MODEL_MEMBER} member."
                )
            model_info = model_infos[0]
            if model_info.flag_bits & 0x1:
                raise Materialized3MFInstanceEvidenceError(
                    "Encrypted materialized 3MF model XML is not supported."
                )
            if model_info.file_size <= 0 or model_info.file_size > MAX_MODEL_XML_BYTES:
                raise Materialized3MFInstanceEvidenceError(
                    "Materialized 3MF model XML size is outside the supported bound."
                )

            tail = bytearray()
            total = 0
            with archive.open(model_info, "r") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_MODEL_XML_BYTES:
                        raise Materialized3MFInstanceEvidenceError(
                            "Materialized 3MF model XML exceeded the supported bound while reading."
                        )
                    tail.extend(chunk)
                    if len(tail) > MAX_BUILD_TAIL_BYTES:
                        del tail[: len(tail) - MAX_BUILD_TAIL_BYTES]
            if total != model_info.file_size:
                raise Materialized3MFInstanceEvidenceError(
                    "Materialized 3MF model XML length does not match its ZIP receipt."
                )
            return bytes(tail)
    except zipfile.BadZipFile as exc:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized project is not a valid 3MF ZIP archive."
        ) from exc


def extract_materialized_build_items(
    project_bytes: bytes,
    *,
    project_sha256: str,
) -> tuple[ExtractedBuildItem, ...]:
    """Parse build items back from the exact SHA-bound materialized Prusa 3MF."""
    if not isinstance(project_bytes, bytes) or not project_bytes:
        raise Materialized3MFInstanceEvidenceError(
            "project_bytes must contain the exact non-empty materialized 3MF."
        )
    expected_hash = _sha256("project_sha256", project_sha256)
    if hashlib.sha256(project_bytes).hexdigest() != expected_hash:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized 3MF bytes do not match their exact SHA-256 receipt."
        )

    tail = _read_model_tail(project_bytes)
    if tail.count(b"<build") != 1 or tail.count(b"</build>") != 1:
        raise Materialized3MFInstanceEvidenceError(
            "Expected exactly one pinned-Prusa build section near the end of materialized 3MF model XML."
        )
    start = tail.index(b"<build")
    end = tail.index(b"</build>", start) + len(b"</build>")
    build = tail[start:end]
    items = list(_ITEM_RE.finditer(build))
    if not items or len(items) > MAX_MATERIALIZED_INSTANCES:
        raise Materialized3MFInstanceEvidenceError(
            f"Materialized 3MF build must contain between 1 and {MAX_MATERIALIZED_INSTANCES} build items."
        )

    result: list[ExtractedBuildItem] = []
    object_ids: set[int] = set()
    for ordinal, match in enumerate(items, start=1):
        item = match.group(0)
        transforms = list(_TRANSFORM_RE.finditer(item))
        object_matches = list(_OBJECT_ID_RE.finditer(item))
        if len(transforms) != 1 or len(object_matches) != 1:
            raise Materialized3MFInstanceEvidenceError(
                "Every materialized 3MF build item must contain exactly one canonical objectid and transform attribute."
            )
        object_id = int(object_matches[0].group(1))
        if object_id < 1:
            raise Materialized3MFInstanceEvidenceError(
                "Materialized 3MF build-item objectid must be positive."
            )
        object_ids.add(object_id)
        result.append(
            ExtractedBuildItem(
                ordinal=ordinal,
                object_id=object_id,
                transform=_parse_transform(transforms[0].group(1)),
            )
        )
    if len(object_ids) != 1:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized plate build items must all reference the same selected source object."
        )
    return tuple(result)


def verify_materialized_project_build_items(
    materialization: SelectedPlateProjectMaterialization,
) -> VerifiedBuildItemEvidence:
    """Prove the transforms stored in the exact 3MF equal the materializer receipt."""
    project = materialization.project
    project_hash = _sha256("materialized project sha256", project.sha256)
    if hashlib.sha256(project.bytes).hexdigest() != project_hash:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized 3MF project bytes do not match the materializer SHA-256 receipt."
        )
    indices = tuple(project.instance_indices)
    expected_indices = tuple(
        item.instance_index for item in materialization.spec.plate_spec.translations
    )
    placement_indices = tuple(item.instance_index for item in materialization.display_placements)
    if indices != expected_indices or placement_indices != expected_indices:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized project, display placements and selected physical plate plan must contain the same ordered instance indices."
        )
    if project.instance_count != len(indices) or len(project.display_transforms) != len(indices):
        raise Materialized3MFInstanceEvidenceError(
            "Materializer instance count/transform receipt does not match its instance indices."
        )

    extracted = extract_materialized_build_items(
        project.bytes,
        project_sha256=project_hash,
    )
    if len(extracted) != len(indices):
        raise Materialized3MFInstanceEvidenceError(
            "Exact materialized 3MF build-item count does not match the selected physical plate."
        )
    for index, (item, expected_transform) in enumerate(
        zip(extracted, project.display_transforms, strict=True),
        start=1,
    ):
        canonical = _canonical_written_transform(expected_transform)
        if item.transform != canonical:
            raise Materialized3MFInstanceEvidenceError(
                f"Exact materialized 3MF build item {index} transform differs from the materializer receipt."
            )

    return VerifiedBuildItemEvidence(
        project_sha256=project_hash,
        object_id=extracted[0].object_id,
        instance_indices=indices,
        build_items=extracted,
    )


def _display_envelope(
    source: Envelope2D,
    *,
    target_x_mm: float,
    target_y_mm: float,
    rotation_z_deg: int,
) -> Envelope2D:
    if rotation_z_deg == 0:
        width = source.width_mm
        depth = source.depth_mm
    elif rotation_z_deg in {-90, 90}:
        width = source.depth_mm
        depth = source.width_mm
    else:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized display placement rotation must be -90, 0, or 90 degrees."
        )
    return Envelope2D(
        min_x_mm=target_x_mm - width / 2.0,
        max_x_mm=target_x_mm + width / 2.0,
        min_y_mm=target_y_mm - depth / 2.0,
        max_y_mm=target_y_mm + depth / 2.0,
    )


def _verify_selected_plan_provenance(
    selected_plan: SelectedOrientationPlatePlan,
    materialization: SelectedPlateProjectMaterialization,
) -> None:
    spec = materialization.spec
    plate_spec = spec.plate_spec
    expected = (
        selected_plan.source_sha256,
        selected_plan.orientation_deg,
        selected_plan.review_project_sha256,
        selected_plan.effective_config_sha256,
        selected_plan.intermediate_sha256,
        selected_plan.native_sha256,
    )
    actual = (
        spec.source_sha256,
        spec.selected_orientation_deg,
        spec.selected_review_3mf_sha256,
        spec.selected_effective_config_sha256,
        spec.selected_intermediate_sl1_sha256,
        spec.selected_printer_native_sha256,
    )
    if actual != expected:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized plate does not remain bound to the exact selected sliced-orientation artifact chain."
        )
    if selected_plan.printer_plate_plan.printer_profile_id != plate_spec.printer_profile_id:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized plate printer profile differs from the selected orientation plan."
        )
    if selected_plan.printer_plate_plan.plan != plate_spec.plan:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized plate plan differs from the exact selected orientation plate plan."
        )
    if plate_spec.manufacturing_envelope_coordinate_mapping != "validated":
        raise Materialized3MFInstanceEvidenceError(
            "Verified build-item instance evidence requires a validated manufacturing-envelope coordinate mapping."
        )
    if (
        selected_plan.pretranslation_coordinate_space
        != MANUFACTURING_ENVELOPE_COORDINATE_SPACE
        or selected_plan.manufacturing_to_display_transform is None
    ):
        raise Materialized3MFInstanceEvidenceError(
            "Selected orientation plan is not bound to a validated manufacturing-to-display transform."
        )
    if selected_plan.native_display_envelope is None:
        raise Materialized3MFInstanceEvidenceError(
            "Selected orientation plan does not retain the exact CTB-bound source display envelope."
        )
    if selected_plan.native_display_envelope != materialization.source_display_envelope:
        raise Materialized3MFInstanceEvidenceError(
            "Materialized plate source display envelope differs from the exact selected CTB-bound envelope."
        )


def finalize_selected_materialized_plate_from_verified_build_items(
    selected_plan: SelectedOrientationPlatePlan,
    materialization: SelectedPlateProjectMaterialization,
) -> SelectedMaterialized3MFInstanceEvidence:
    """Validate per-instance materialized envelopes from exact 3MF build-item evidence.

    This is deliberately not called geometry re-extraction. It parses every build-item
    transform back from the exact materialized 3MF, proves those transforms equal the
    deterministic materializer receipt, applies the verified rigid placements to the exact
    selected single-instance CTB supported/padded display envelope, maps each rectangle
    back through the exact validated manufacturing/display transform bound at planning
    time, and finally runs the ordinary slot/margin/spacing validator.
    """
    _verify_selected_plan_provenance(selected_plan, materialization)
    build_evidence = verify_materialized_project_build_items(materialization)
    transform = selected_plan.manufacturing_to_display_transform
    assert transform is not None  # checked above

    display_envelopes: list[Envelope2D] = []
    manufacturing_envelopes: list[Envelope2D] = []
    observations: list[MaterializedEnvelopeObservation] = []
    for placement in materialization.display_placements:
        display_envelope = _display_envelope(
            materialization.source_display_envelope,
            target_x_mm=placement.target_display_x_mm,
            target_y_mm=placement.target_display_y_mm,
            rotation_z_deg=placement.rotation_z_deg,
        )
        try:
            bounds = transform.display_bounds_to_manufacturing_bounds(
                min_display_x_mm=display_envelope.min_x_mm,
                max_display_x_mm=display_envelope.max_x_mm,
                min_display_y_mm=display_envelope.min_y_mm,
                max_display_y_mm=display_envelope.max_y_mm,
            )
            manufacturing_envelope = Envelope2D(
                min_x_mm=bounds[0],
                max_x_mm=bounds[1],
                min_y_mm=bounds[2],
                max_y_mm=bounds[3],
            )
        except (CoordinateMappingError, PlacementError) as exc:
            raise Materialized3MFInstanceEvidenceError(str(exc)) from exc
        display_envelopes.append(display_envelope)
        manufacturing_envelopes.append(manufacturing_envelope)
        observations.append(
            MaterializedEnvelopeObservation(
                instance_index=placement.instance_index,
                envelope=manufacturing_envelope,
                project_sha256=build_evidence.project_sha256,
                source=VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE,
            )
        )

    try:
        finalized = finalize_selected_materialized_plate(
            materialization.spec,
            project_bytes=materialization.project.bytes,
            observations=tuple(observations),
        )
    except ValueError as exc:
        raise Materialized3MFInstanceEvidenceError(str(exc)) from exc

    return SelectedMaterialized3MFInstanceEvidence(
        materialized_plate=finalized,
        build_items=build_evidence,
        selected_printer_native_sha256=_sha256(
            "selected printer native sha256",
            selected_plan.native_sha256,
        ),
        source_display_envelope=materialization.source_display_envelope,
        display_envelopes=tuple(display_envelopes),
        manufacturing_envelopes=tuple(manufacturing_envelopes),
    )


def materialized_3mf_instance_evidence_manifest(
    evidence: SelectedMaterialized3MFInstanceEvidence,
) -> dict:
    finalized = evidence.materialized_plate
    build = evidence.build_items
    items = []
    for instance_index, build_item, display, manufacturing in zip(
        build.instance_indices,
        build.build_items,
        evidence.display_envelopes,
        evidence.manufacturing_envelopes,
        strict=True,
    ):
        items.append(
            {
                "instance_index": instance_index,
                "build_ordinal": build_item.ordinal,
                "object_id": build_item.object_id,
                "transform": list(build_item.transform),
                "display_envelope_mm": {
                    "min_x": display.min_x_mm,
                    "max_x": display.max_x_mm,
                    "min_y": display.min_y_mm,
                    "max_y": display.max_y_mm,
                },
                "manufacturing_envelope_mm": {
                    "min_x": manufacturing.min_x_mm,
                    "max_x": manufacturing.max_x_mm,
                    "min_y": manufacturing.min_y_mm,
                    "max_y": manufacturing.max_y_mm,
                },
            }
        )
    return {
        "schema": INSTANCE_EVIDENCE_SCHEMA,
        "plate_index": finalized.plate_index,
        "materialized_project_sha256": build.project_sha256,
        "selected_printer_native_sha256": evidence.selected_printer_native_sha256,
        "selected_source_display_envelope_mm": {
            "min_x": evidence.source_display_envelope.min_x_mm,
            "max_x": evidence.source_display_envelope.max_x_mm,
            "min_y": evidence.source_display_envelope.min_y_mm,
            "max_y": evidence.source_display_envelope.max_y_mm,
        },
        "shared_source_object_id": build.object_id,
        "instance_count": len(build.instance_indices),
        "instances": items,
        "evidence_method": VERIFIED_BUILD_ITEM_OBSERVATION_SOURCE,
        "geometry_reextraction_performed": False,
        "per_instance_materialized_project_validation_satisfied": True,
        "whole_plate_native_validation_still_required": True,
        "validation_rule": (
            "Every build item is parsed back from the exact SHA-bound materialized 3MF and must match the deterministic materializer transform receipt. Per-instance supported/padded bounds are then derived from the exact selected single-instance CTB envelope through those verified placements and the exact validated manufacturing/display transform before the ordinary slot, margin and spacing checks run. This is a transform proof, not geometry re-extraction; final native whole-plate UVtools validation remains an independent required gate."
        ),
    }
