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
from .native_envelope import NativeEnvelopeEvidence, native_envelope_from_rectangle
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
    UVtoolsMetricError,
    base_property_command,
    layer_property_command,
    parse_base_native_properties,
    parse_native_artifact_metrics,
)


class SlicedFinalistAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class RealSlicedFinalistExecution:
    """Real pinned-engine finalist execution with only the selected heavy artifact retained."""

    validation: SlicedOrientationValidation
    selected_result: FinalistSliceResult | None
    selected_native_envelope: NativeEnvelopeEvidence | None
    executed_finalist_count: int


@dataclass(frozen=True)
class _SpooledFinalist:
    receipt: NativeArtifact
    measurements: SlicedMeasurements
    native_envelope: NativeEnvelopeEvidence
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
) -> RealSlicedFinalistExecution:
    """Slice exactly the proxy finalists, measure exact CTBs, and retain only the winner.

    Finalists are executed sequentially. Each complete retained artifact chain is written
    to temporary disk immediately after slicing, then represented in memory only by a
    lightweight hash/issue receipt while ranking continues. After sliced validation picks
    a winner, only that exact 3MF/effective-config/SL1/native chain is restored to memory.

    The exact whole-print XY bounding rectangle from pinned UVtools is retained alongside
    each receipt in UVtools native display millimetres. For the winner that rectangle is
    exposed as CTB-hash-bound evidence only; it does not map or authorize the conservative
    manufacturing envelope.

    Critical UVtools findings are deliberately *not* rejected inside ``slice_native``:
    they must enter sliced-finalist validation so one blocked orientation does not abort
    evaluation of other finalists. Production authority is not granted by this adapter.
    """
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
            native_envelope = native_envelope_from_rectangle(
                printer_profile_id=printer.id,
                printer_native_sha256=artifact.native_sha256,
                rectangle=metrics.bounding_rectangle,
            )
            receipt = _artifact_receipt(artifact)
            spooled[key] = _SpooledFinalist(
                receipt=receipt,
                measurements=measurements,
                native_envelope=native_envelope,
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
        if selected is None:
            return RealSlicedFinalistExecution(
                validation=execution.validation,
                selected_result=None,
                selected_native_envelope=None,
                executed_finalist_count=len(spooled),
            )

        selected_item = spooled.get(selected.canonical_key)
        if selected_item is None:
            raise SlicedFinalistAdapterError(
                "Selected sliced evidence has no matching spooled exact artifact."
            )
        if selected.native_sha256 != selected_item.native_envelope.printer_native_sha256:
            raise SlicedFinalistAdapterError(
                "Selected native envelope is not bound to the exact selected printer-native artifact hash."
            )
        selected_artifact = _restore_spooled_artifact(selected_item)
        return RealSlicedFinalistExecution(
            validation=execution.validation,
            selected_result=FinalistSliceResult(
                artifact=selected_artifact,
                measurements=selected_item.measurements,
            ),
            selected_native_envelope=selected_item.native_envelope,
            executed_finalist_count=len(spooled),
        )
