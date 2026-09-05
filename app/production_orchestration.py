from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .engine import PRUSA_SLICER_COMMIT, PRUSA_SLICER_VERSION, UVTOOLS_VERSION
from .materialization_selected import (
    SelectedPlateProjectMaterialization,
    materialize_selected_plate_project,
)
from .materialized_3mf_instance_evidence import (
    SelectedMaterialized3MFInstanceEvidence,
    finalize_selected_materialized_plate_from_verified_build_items,
)
from .materialized_plate_execution import (
    SelectedMaterializedPlateNativeExecution,
    execute_selected_materialized_plate_native,
)
from .materialized_plate_native_validation import (
    WholePlateNativeEnvelopeEvidence,
    validate_whole_plate_native_envelope,
)
from .order_selected import (
    SelectedPlateArtifactRecord,
    build_selected_orientation_order_manifest,
)
from .orientation_adapter import (
    RealSlicedFinalistExecution,
    execute_real_sliced_finalists,
)
from .orientation_pipeline import ProxyOrientationPlan, build_proxy_orientation_plan
from .orientation_plate import SelectedOrientationPlatePlan, plan_selected_sliced_orientation
from .plate_authority import (
    SelectedPlateAuthorityEvidence,
    validate_selected_plate_authority,
)
from .profiles import ProfileRegistry
from .toolchain import resolve_toolchain_ref, toolchain_record


PRODUCTION_ORDER_AUTHORITY = "production-authoritative"
_PRODUCTION_ORCHESTRATION_SCHEMA = "workpiece-resin-selected-production-orchestration-v1"


class ProductionOrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SelectedProductionPlateResult:
    plate_index: int
    materialization: SelectedPlateProjectMaterialization
    instance_evidence: SelectedMaterialized3MFInstanceEvidence
    native_execution: SelectedMaterializedPlateNativeExecution
    whole_plate_native_evidence: WholePlateNativeEnvelopeEvidence
    authority_evidence: SelectedPlateAuthorityEvidence
    retained_project_filename: str
    retained_intermediate_filename: str
    retained_native_filename: str


@dataclass(frozen=True)
class SelectedProductionOrderResult:
    source_sha256: str
    proxy_plan: ProxyOrientationPlan
    sliced_execution: RealSlicedFinalistExecution
    selected_orientation_plan: SelectedOrientationPlatePlan
    plates: tuple[SelectedProductionPlateResult, ...]
    toolchain_image_ref: str
    order_manifest: dict


def _positive_quantity(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProductionOrchestrationError("requested_quantity must be a positive integer.")
    return value


def _retained_source_filename(original_name: str) -> str:
    normalized = str(original_name).strip().replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    if not filename:
        raise ProductionOrchestrationError("original_name must identify the retained source STL.")
    return filename


def _safe_stem(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-._") or "workpiece"


def execute_selected_production_order(
    *,
    source_stl: bytes,
    original_name: str,
    requested_quantity: int,
    registry: ProfileRegistry,
    printer_profile_id: str,
    resin_profile_id: str,
    quality_profile_id: str,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    finalist_limit: int = 5,
    spacing_mm: float = 5.0,
    edge_margin_mm: float = 3.0,
    allow_rotate_90: bool = True,
) -> SelectedProductionOrderResult:
    """Execute the complete selected resin production-evidence chain.

    This coordinator adds no geometric or slicing authority of its own. It sequences the
    existing source-bound proxy screen, real sliced-finalist selection, printer-backed plate
    planning, deterministic 3MF materialization, exact retained-config plate slicing,
    per-instance exact-project proof, final native whole-plate proof, plate authority gate,
    and selected-order v4 manifest. Production profile resolution and an immutable runtime
    toolchain image receipt are required before any expensive finalist slicing begins, and
    that receipt is validated again inside the selected-order authority manifest.

    The function establishes evidence only. It does not deploy, send a job to a printer, or
    change profile readiness.
    """
    if not isinstance(source_stl, bytes) or not source_stl:
        raise ProductionOrchestrationError("source_stl must contain exact non-empty STL bytes.")
    quantity = _positive_quantity(requested_quantity)
    source_filename = _retained_source_filename(original_name)
    stem = _safe_stem(source_filename)

    toolchain_ref = resolve_toolchain_ref(required=True)
    assert toolchain_ref is not None
    execution_environment = toolchain_record(toolchain_ref)

    printer, resin, quality = registry.resolve_production(
        printer_profile_id,
        resin_profile_id,
        quality_profile_id,
    )

    proxy_plan = build_proxy_orientation_plan(
        source_stl,
        finalist_limit=finalist_limit,
    )
    sliced = execute_real_sliced_finalists(
        proxy_plan=proxy_plan,
        source_stl=source_stl,
        original_name=source_filename,
        printer=printer,
        resin=resin,
        quality=quality,
        prusa_bin=prusa_bin,
        uvtools_cmd=uvtools_cmd,
        slice_timeout=slice_timeout,
        uvtools_timeout=uvtools_timeout,
    )
    if sliced.selected_result is None or sliced.selected_native_envelope is None:
        raise ProductionOrchestrationError(
            "Sliced finalist validation requires manual review; automatic production orchestration is blocked."
        )

    selected_artifact = sliced.selected_result.artifact
    if hashlib.sha256(source_stl).hexdigest() != selected_artifact.source_sha256:
        raise ProductionOrchestrationError(
            "Selected sliced winner is not bound to the exact requested source STL bytes."
        )

    selected_plan = plan_selected_sliced_orientation(
        registry=registry,
        printer_profile_id=printer.id,
        sliced_validation=sliced.validation,
        native_envelope=sliced.selected_native_envelope,
        quantity=quantity,
        spacing_mm=spacing_mm,
        edge_margin_mm=edge_margin_mm,
        allow_rotate_90=allow_rotate_90,
    )

    plate_results: list[SelectedProductionPlateResult] = []
    order_records: list[SelectedPlateArtifactRecord] = []
    for plate in selected_plan.printer_plate_plan.plan.plates:
        plate_index = plate.plate_index
        materialization = materialize_selected_plate_project(
            selected_plan,
            registry=registry,
            plate_index=plate_index,
            selected_review_project_bytes=selected_artifact.project_bytes,
        )
        instance_evidence = finalize_selected_materialized_plate_from_verified_build_items(
            selected_plan,
            materialization,
            selected_review_project_bytes=selected_artifact.project_bytes,
        )
        native_execution = execute_selected_materialized_plate_native(
            materialization,
            selected_effective_config_bytes=selected_artifact.effective_config_bytes,
            printer=printer,
            prusa_bin=prusa_bin,
            uvtools_cmd=uvtools_cmd,
            slice_timeout=slice_timeout,
            uvtools_timeout=uvtools_timeout,
            reject_critical=True,
        )
        whole_plate = validate_whole_plate_native_envelope(
            materialization,
            native_execution,
            printer=printer,
        )
        authority = validate_selected_plate_authority(
            instance_evidence,
            native_execution,
            whole_plate,
            printer=printer,
        )

        suffix = str(printer.metadata.get("native_format", "native")).lower().lstrip(".") or "native"
        project_filename = f"{stem}-plate-{plate_index:03d}-materialized.3mf"
        intermediate_filename = f"{stem}-plate-{plate_index:03d}-intermediate.sl1"
        native_filename = f"{stem}-plate-{plate_index:03d}.{suffix}"
        artifact = native_execution.artifact

        plate_result = SelectedProductionPlateResult(
            plate_index=plate_index,
            materialization=materialization,
            instance_evidence=instance_evidence,
            native_execution=native_execution,
            whole_plate_native_evidence=whole_plate,
            authority_evidence=authority,
            retained_project_filename=project_filename,
            retained_intermediate_filename=intermediate_filename,
            retained_native_filename=native_filename,
        )
        plate_results.append(plate_result)
        order_records.append(
            SelectedPlateArtifactRecord(
                plate_index=plate_index,
                project_filename=project_filename,
                intermediate_filename=intermediate_filename,
                intermediate_sha256=artifact.intermediate_sha256,
                native_filename=native_filename,
                native_sha256=artifact.native_sha256,
                issue_summary=artifact.issue_summary,
                materialization=instance_evidence.materialized_plate,
                authority_evidence=authority,
            )
        )

    order_manifest = build_selected_orientation_order_manifest(
        source_filename=source_filename,
        source_sha256=hashlib.sha256(source_stl).hexdigest(),
        requested_quantity=quantity,
        printer_profile=printer.id,
        resin_profile=resin.id,
        quality_profile=quality.id,
        selected_orientation_plan=selected_plan,
        plate_artifacts=tuple(order_records),
        prusaslicer_version=PRUSA_SLICER_VERSION,
        prusaslicer_commit=PRUSA_SLICER_COMMIT,
        uvtools_version=UVTOOLS_VERSION,
        authority=PRODUCTION_ORDER_AUTHORITY,
        execution_environment=execution_environment,
    )
    order_manifest = dict(order_manifest)
    order_manifest["orchestration"] = {
        "schema": _PRODUCTION_ORCHESTRATION_SCHEMA,
        "proxy_finalist_count": len(proxy_plan.screening.finalists),
        "executed_finalist_count": sliced.executed_finalist_count,
        "physical_plate_count": len(plate_results),
        "production_enablement_performed": False,
    }

    return SelectedProductionOrderResult(
        source_sha256=hashlib.sha256(source_stl).hexdigest(),
        proxy_plan=proxy_plan,
        sliced_execution=sliced,
        selected_orientation_plan=selected_plan,
        plates=tuple(plate_results),
        toolchain_image_ref=toolchain_ref,
        order_manifest=order_manifest,
    )
