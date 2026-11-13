from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .engine import (
    UVTOOLS_SUCCESS_EXIT,
    EngineError,
    NativeArtifact,
    Orientation,
    slice_native,
)
from .issue_triage import support_lift_layers, triage_issue_summary
from .orientation_execute import (
    FinalistSliceResult,
    SlicedMeasurements,
    execute_sliced_finalists,
)
from .orientation_pipeline import ProxyOrientationPlan
from .orientation_sliced import SlicedOrientationValidation
from .profiles import Profile
from .uvtools_metrics import (
    NativeArtifactMetrics,
    NativeBoundingRectangle,
    UVtoolsMetricError,
    base_property_command,
    layer_property_command,
    parse_base_native_properties,
    parse_native_artifact_metrics,
)


class SlicedFinalistAdapterError(RuntimeError):
    pass


SELECTION_SLICED_WINNER = "sliced-winner"
SELECTION_LEAST_CRITICAL = "sliced-least-critical"
SELECTION_PROXY_FALLBACK = "proxy-best-fallback"
SELECTION_NONE = "manual-review-required"


@dataclass(frozen=True)
class RealSlicedFinalistExecution:
    """Real pinned-engine finalist execution with only the selected heavy artifact retained.

    ``selected_native_rectangle`` is the whole-print XY bounding rectangle UVtools measured
    on the selected CTB, in UVtools display millimetres. Its width/depth are the supported
    and padded footprint used for plate planning.
    """

    validation: SlicedOrientationValidation
    selected_result: FinalistSliceResult | None
    selected_native_rectangle: NativeBoundingRectangle | None
    executed_finalist_count: int
    selection: str = SELECTION_NONE


@dataclass(frozen=True)
class _SpooledFinalist:
    receipt: NativeArtifact
    measurements: SlicedMeasurements
    native_rectangle: NativeBoundingRectangle
    project_path: Path
    effective_config_path: Path
    intermediate_path: Path
    native_path: Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_uvtools_text(
    command: Sequence[str],
    *,
    timeout: int,
    action: str,
) -> str:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise SlicedFinalistAdapterError("UVtools timeout must be a positive integer number of seconds.")
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise SlicedFinalistAdapterError(
            f"Pinned UVtools {action} timed out after {timeout} seconds."
        ) from exc
    if result.returncode != UVTOOLS_SUCCESS_EXIT:
        raise SlicedFinalistAdapterError(
            f"Pinned UVtools {action} failed with status {result.returncode}: {result.stdout[-2000:]}"
        )
    return result.stdout


def _verify_retained_artifact_bytes(artifact: NativeArtifact) -> None:
    payloads = (
        ("review 3MF", artifact.project_bytes, artifact.project_sha256),
        (
            "effective config",
            artifact.effective_config_bytes,
            artifact.effective_config_sha256,
        ),
        ("intermediate SL1", artifact.intermediate_bytes, artifact.intermediate_sha256),
        ("printer-native artifact", artifact.bytes, artifact.native_sha256),
    )
    for name, data, expected_hash in payloads:
        if not data:
            raise SlicedFinalistAdapterError(f"{name} is empty; exact finalist retention failed.")
        if _sha(data) != expected_hash:
            raise SlicedFinalistAdapterError(
                f"{name} bytes do not match the SHA-256 recorded by the exact finalist artifact chain."
            )


def _artifact_receipt(artifact: NativeArtifact) -> NativeArtifact:
    """Drop heavyweight retained bytes while preserving the exact validation receipt."""
    return replace(
        artifact,
        project_bytes=b"",
        effective_config_bytes=b"",
        intermediate_bytes=b"",
        bytes=b"",
    )


def _write_spooled_artifact(
    root: Path,
    *,
    finalist_index: int,
    artifact: NativeArtifact,
) -> tuple[Path, Path, Path, Path]:
    _verify_retained_artifact_bytes(artifact)
    candidate_root = root / f"finalist-{finalist_index:03d}"
    try:
        candidate_root.mkdir(parents=True, exist_ok=False)
        project_path = candidate_root / "review.3mf"
        effective_config_path = candidate_root / "effective.ini"
        intermediate_path = candidate_root / "intermediate.sl1"
        native_suffix = Path(artifact.filename).suffix.lower() or ".native"
        native_path = candidate_root / f"printer-native{native_suffix}"
        project_path.write_bytes(artifact.project_bytes)
        effective_config_path.write_bytes(artifact.effective_config_bytes)
        intermediate_path.write_bytes(artifact.intermediate_bytes)
        native_path.write_bytes(artifact.bytes)
    except OSError as exc:
        raise SlicedFinalistAdapterError(
            "Unable to spool exact sliced-finalist artifacts to temporary storage."
        ) from exc
    return project_path, effective_config_path, intermediate_path, native_path


def _extract_native_metrics(
    native_path: Path,
    *,
    uvtools_cmd: str,
    uvtools_timeout: int,
) -> NativeArtifactMetrics:
    try:
        base_output = _run_uvtools_text(
            base_property_command(uvtools_cmd, str(native_path)),
            timeout=uvtools_timeout,
            action="base native-property extraction",
        )
        layer_count, _, _ = parse_base_native_properties(base_output)
        layer_output = _run_uvtools_text(
            layer_property_command(
                uvtools_cmd,
                str(native_path),
                layer_count=layer_count,
            ),
            timeout=uvtools_timeout,
            action="per-layer native-property extraction",
        )
        return parse_native_artifact_metrics(base_output, layer_output)
    except UVtoolsMetricError as exc:
        raise SlicedFinalistAdapterError(
            "Pinned UVtools native metrics were incomplete or unparseable; finalist ranking is blocked."
        ) from exc


def _restore_spooled_artifact(item: _SpooledFinalist) -> NativeArtifact:
    try:
        artifact = replace(
            item.receipt,
            project_bytes=item.project_path.read_bytes(),
            effective_config_bytes=item.effective_config_path.read_bytes(),
            intermediate_bytes=item.intermediate_path.read_bytes(),
            bytes=item.native_path.read_bytes(),
        )
    except OSError as exc:
        raise SlicedFinalistAdapterError(
            "Unable to restore the selected sliced-finalist artifact from temporary storage."
        ) from exc
    _verify_retained_artifact_bytes(artifact)
    return artifact


def _critical_count(evidence) -> int:
    return (
        evidence.unresolved_islands + evidence.unresolved_suction_cups + evidence.unresolved_resin_traps
        + evidence.unresolved_touching_bounds + evidence.unresolved_empty_layers
    )


def fewest_critical_finalist(evidence: Sequence):
    """The sliced finalist with the fewest critical findings; on a tie the earlier one (the proxy ranking order of ``evidence``)."""
    if not evidence:
        raise SlicedFinalistAdapterError("No sliced finalist evidence to choose from.")
    return min(enumerate(evidence), key=lambda pair: (_critical_count(pair[1]), pair[0]))[1]


def execute_real_sliced_finalists(
    *,
    proxy_plan: ProxyOrientationPlan,
    source_stl: bytes,
    original_name: str,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    fallback_to_proxy_best: bool = False,
) -> RealSlicedFinalistExecution:
    """Slice exactly the proxy finalists, measure each CTB, and retain only the winner.

    Finalists are executed sequentially. Each artifact chain is written to temporary disk
    immediately after slicing and represented in memory only by a lightweight receipt
    while ranking continues. After ranking picks a winner, only that 3MF/effective
    config/SL1/native chain is restored to memory, together with the UVtools whole-print
    bounding rectangle of its CTB.

    Critical UVtools findings are deliberately *not* rejected inside ``slice_native``:
    they enter ranking as hard blockers so one bad orientation does not abort the others.
    When every finalist is blocked there is no winner. With ``fallback_to_proxy_best`` the
    best proxy-screened finalist is returned instead (``selection`` says so) so an order
    project can still be produced for human review; otherwise ``selected_result`` is None.
    """
    from .estimate import EstimateError, cycle_profile, mesh_stats   # estimate.py imports this module: import here to avoid the cycle

    if not isinstance(source_stl, bytes) or not source_stl:
        raise SlicedFinalistAdapterError("source_stl must contain exact non-empty STL bytes.")
    if not str(original_name).strip():
        raise SlicedFinalistAdapterError("original_name is required for retained artifact provenance.")

    spooled: dict[tuple[float, float, float], _SpooledFinalist] = {}
    with tempfile.TemporaryDirectory(prefix="workpiece-resin-finalists-") as temp:
        spool_root = Path(temp)

        def execute_one(spec):
            key = spec.canonical_key
            if key in spooled:
                raise SlicedFinalistAdapterError(
                    f"Duplicate proxy finalist orientation reached the real slicer: {key}."
                )
            try:
                artifact = slice_native(
                    source_stl,
                    original_name=original_name,
                    printer=printer,
                    resin=resin,
                    quality=quality,
                    orientation=Orientation(spec.x_deg, spec.y_deg, spec.z_deg),
                    prusa_bin=prusa_bin,
                    uvtools_cmd=uvtools_cmd,
                    slice_timeout=slice_timeout,
                    uvtools_timeout=uvtools_timeout,
                    reject_critical=False,
                )
            except EngineError as exc:
                raise SlicedFinalistAdapterError(
                    f"Real slicer execution failed for finalist {key}: {exc}"
                ) from exc

            finalist_index = len(spooled) + 1
            project_path, config_path, intermediate_path, native_path = _write_spooled_artifact(
                spool_root,
                finalist_index=finalist_index,
                artifact=artifact,
            )
            metrics = _extract_native_metrics(
                native_path,
                uvtools_cmd=uvtools_cmd,
                uvtools_timeout=uvtools_timeout,
            )
            # UVtools reports one island on the first model layer of any part with a flat underside (it rests on the support tips): information, not a print risk (app/issue_triage.py).
            try:
                lift_layers = support_lift_layers(
                    metrics.z_height_mm,
                    mesh_stats(source_stl, Orientation(spec.x_deg, spec.y_deg, spec.z_deg)).dims_mm[2],
                    cycle_profile(resin, artifact.effective_config_bytes).layer_height_mm,
                )
            except (EstimateError, ValueError, KeyError):
                lift_layers = None   # cannot tell where the support region ends: nothing is excused
            artifact = replace(
                artifact,
                issue_summary=triage_issue_summary(artifact.issue_summary, artifact.issue_text, lift_layers=lift_layers),
            )
            measurements = SlicedMeasurements(
                source_sha256=artifact.source_sha256,
                review_project_sha256=artifact.project_sha256,
                effective_config_sha256=artifact.effective_config_sha256,
                intermediate_sha256=artifact.intermediate_sha256,
                native_sha256=artifact.native_sha256,
                max_layer_area_mm2=metrics.max_layer_area_mm2,
                material_volume_mm3=metrics.material_volume_mm3,
                footprint_area_mm2=metrics.footprint_area_mm2,
                z_height_mm=metrics.z_height_mm,
            )
            receipt = _artifact_receipt(artifact)
            spooled[key] = _SpooledFinalist(
                receipt=receipt,
                measurements=measurements,
                native_rectangle=metrics.bounding_rectangle,
                project_path=project_path,
                effective_config_path=config_path,
                intermediate_path=intermediate_path,
                native_path=native_path,
            )
            return FinalistSliceResult(artifact=receipt, measurements=measurements)

        execution = execute_sliced_finalists(
            proxy_plan=proxy_plan,
            source_stl=source_stl,
            printer_profile=printer.id,
            resin_profile=resin.id,
            quality_profile=quality.id,
            execute_finalist=execute_one,
        )

        selected = execution.validation.selected_evidence
        if selected is not None:
            selected_key = selected.canonical_key
            selection = SELECTION_SLICED_WINNER
        elif fallback_to_proxy_best:
            # Every finalist still has critical findings: take the one with the fewest (ties go to the proxy ranking order); the project warns only because THIS winner is still critical.
            selected_key = fewest_critical_finalist(execution.validation.evidence).canonical_key
            selection = SELECTION_LEAST_CRITICAL
        else:
            return RealSlicedFinalistExecution(
                validation=execution.validation,
                selected_result=None,
                selected_native_rectangle=None,
                executed_finalist_count=len(spooled),
            )

        selected_item = spooled.get(selected_key)
        if selected_item is None:
            raise SlicedFinalistAdapterError(
                "Selected orientation has no matching spooled artifact."
            )
        selected_artifact = _restore_spooled_artifact(selected_item)
        return RealSlicedFinalistExecution(
            validation=execution.validation,
            selected_result=FinalistSliceResult(
                artifact=selected_artifact,
                measurements=selected_item.measurements,
            ),
            selected_native_rectangle=selected_item.native_rectangle,
            executed_finalist_count=len(spooled),
            selection=selection,
        )
