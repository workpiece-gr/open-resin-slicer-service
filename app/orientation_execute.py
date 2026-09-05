from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from .engine import NativeArtifact
from .orientation_candidates import OrientationSpec
from .orientation_pipeline import ProxyOrientationPlan
from .orientation_sliced import (
    SlicedFinalistEvidence,
    SlicedOrientationValidation,
    validate_sliced_finalists,
)


class SlicedFinalistExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class SlicedMeasurements:
    """Metrics extracted from the exact retained printer-native artifact."""

    source_sha256: str
    review_project_sha256: str
    intermediate_sha256: str
    native_sha256: str
    max_layer_area_mm2: float
    material_volume_mm3: float
    footprint_area_mm2: float
    z_height_mm: float


@dataclass(frozen=True)
class FinalistSliceResult:
    artifact: NativeArtifact
    measurements: SlicedMeasurements


@dataclass(frozen=True)
class SlicedFinalistExecution:
    validation: SlicedOrientationValidation
    results: tuple[FinalistSliceResult, ...]


def _orientation_key(artifact: NativeArtifact) -> tuple[float, float, float]:
    return OrientationSpec(
        artifact.orientation.x,
        artifact.orientation.y,
        artifact.orientation.z,
    ).canonical_key


def _assert_equal(name: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise SlicedFinalistExecutionError(f"{name} does not match the exact finalist artifact chain.")


def execute_sliced_finalists(
    *,
    proxy_plan: ProxyOrientationPlan,
    source_stl: bytes,
    printer_profile: str,
    resin_profile: str,
    quality_profile: str,
    execute_finalist: Callable[[OrientationSpec], FinalistSliceResult],
) -> SlicedFinalistExecution:
    """Execute exactly the proxy finalists and validate artifact-native measurement binding.

    The callback is injected so this contract remains unit-testable without PrusaSlicer.
    A real adapter must return the NativeArtifact plus metrics extracted from its exact
    retained printer-native output; hidden Prusa state and proxy estimates are rejected
    as measurement authority by contract.
    """
    source_hash = hashlib.sha256(source_stl).hexdigest()
    if source_hash != proxy_plan.source_sha256:
        raise SlicedFinalistExecutionError(
            "source_stl does not match the source-bound proxy orientation plan."
        )
    if not proxy_plan.screening.finalists:
        raise SlicedFinalistExecutionError(
            "Proxy orientation plan has no finalists; sliced execution requires manual review."
        )

    results: list[FinalistSliceResult] = []
    evidence: list[SlicedFinalistEvidence] = []
    for screened in proxy_plan.screening.finalists:
        spec = screened.candidate.spec
        result = execute_finalist(spec)
        artifact = result.artifact
        measurements = result.measurements

        _assert_equal("artifact source SHA-256", artifact.source_sha256, source_hash)
        if _orientation_key(artifact) != spec.canonical_key:
            raise SlicedFinalistExecutionError(
                "Slicer artifact orientation does not match the requested proxy finalist."
            )
        _assert_equal("printer profile", artifact.printer_profile, printer_profile)
        _assert_equal("resin profile", artifact.resin_profile, resin_profile)
        _assert_equal("quality profile", artifact.quality_profile, quality_profile)

        _assert_equal("measurement source SHA-256", measurements.source_sha256, artifact.source_sha256)
        _assert_equal(
            "measurement review 3MF SHA-256",
            measurements.review_project_sha256,
            artifact.project_sha256,
        )
        _assert_equal(
            "measurement intermediate SL1 SHA-256",
            measurements.intermediate_sha256,
            artifact.intermediate_sha256,
        )
        _assert_equal(
            "measurement printer-native SHA-256",
            measurements.native_sha256,
            artifact.native_sha256,
        )

        issues = artifact.issue_summary
        evidence.append(
            SlicedFinalistEvidence(
                spec=spec,
                source_sha256=source_hash,
                review_project_sha256=artifact.project_sha256,
                intermediate_sha256=artifact.intermediate_sha256,
                native_sha256=artifact.native_sha256,
                max_layer_area_mm2=measurements.max_layer_area_mm2,
                material_volume_mm3=measurements.material_volume_mm3,
                footprint_area_mm2=measurements.footprint_area_mm2,
                z_height_mm=measurements.z_height_mm,
                unresolved_islands=int(issues.get("islands", 0)),
                unresolved_suction_cups=int(issues.get("suction_cups", 0)),
                unresolved_resin_traps=int(issues.get("resin_traps", 0)),
            )
        )
        results.append(result)

    validation = validate_sliced_finalists(proxy_plan.screening, evidence)
    return SlicedFinalistExecution(validation=validation, results=tuple(results))
