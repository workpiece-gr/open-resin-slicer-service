from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .orientation import (
    OrientationDecision,
    OrientationMetrics,
    orientation_decision_manifest,
    rank_orientation_candidates,
)
from .orientation_candidates import OrientationSpec
from .orientation_screen import ProxyScreeningDecision


SLICED_ORIENTATION_SCHEMA = "workpiece-resin-orientation-sliced-validation-v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SlicedOrientationValidationError(ValueError):
    pass


def _sha256(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SlicedOrientationValidationError(
            f"{name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


@dataclass(frozen=True)
class SlicedFinalistEvidence:
    """Native-artifact measurements for one proxy-screened orientation finalist."""

    spec: OrientationSpec
    source_sha256: str
    review_project_sha256: str
    intermediate_sha256: str
    native_sha256: str
    max_layer_area_mm2: float
    material_volume_mm3: float
    footprint_area_mm2: float
    z_height_mm: float
    unresolved_islands: int = 0
    unresolved_suction_cups: int = 0
    unresolved_resin_traps: int = 0
    unresolved_touching_bounds: int = 0
    unresolved_empty_layers: int = 0

    @property
    def canonical_key(self) -> tuple[float, float, float]:
        return self.spec.canonical_key

    def validate(self) -> "SlicedFinalistEvidence":
        self.spec.validate()
        _sha256("source_sha256", self.source_sha256)
        _sha256("review_project_sha256", self.review_project_sha256)
        _sha256("intermediate_sha256", self.intermediate_sha256)
        _sha256("native_sha256", self.native_sha256)
        self.metrics.validate()
        return self

    @property
    def metrics(self) -> OrientationMetrics:
        return OrientationMetrics(
            max_layer_area_mm2=self.max_layer_area_mm2,
            material_volume_mm3=self.material_volume_mm3,
            footprint_area_mm2=self.footprint_area_mm2,
            z_height_mm=self.z_height_mm,
            unresolved_islands=self.unresolved_islands,
            unresolved_suction_cups=self.unresolved_suction_cups,
            unresolved_resin_traps=self.unresolved_resin_traps,
            unresolved_touching_bounds=self.unresolved_touching_bounds,
            unresolved_empty_layers=self.unresolved_empty_layers,
            source="sliced-validation",
        )

    def artifact_record(self) -> dict:
        return {
            "review_3mf_sha256": _sha256(
                "review_project_sha256", self.review_project_sha256
            ),
            "intermediate_sl1_sha256": _sha256(
                "intermediate_sha256", self.intermediate_sha256
            ),
            "printer_native_sha256": _sha256("native_sha256", self.native_sha256),
        }


@dataclass(frozen=True)
class SlicedOrientationValidation:
    source_sha256: str
    evidence: tuple[SlicedFinalistEvidence, ...]
    decision: OrientationDecision

    @property
    def selected_evidence(self) -> SlicedFinalistEvidence | None:
        if self.decision.selected is None:
            return None
        key = self.decision.selected.candidate.canonical_key
        return next(item for item in self.evidence if item.canonical_key == key)


def validate_sliced_finalists(
    proxy_screening: ProxyScreeningDecision,
    evidence: Sequence[SlicedFinalistEvidence],
    *,
    weights: Mapping[str, float] | None = None,
) -> SlicedOrientationValidation:
    """Require exact sliced evidence coverage for every proxy finalist and rank only that evidence."""
    if not proxy_screening.finalists:
        raise SlicedOrientationValidationError(
            "Proxy screening produced no finalists; sliced automatic selection cannot proceed."
        )

    expected_keys = tuple(item.candidate.spec.canonical_key for item in proxy_screening.finalists)
    expected_set = set(expected_keys)
    by_key: dict[tuple[float, float, float], SlicedFinalistEvidence] = {}
    source_hashes: set[str] = set()
    for item in evidence:
        item.validate()
        key = item.canonical_key
        if key in by_key:
            raise SlicedOrientationValidationError(
                f"Duplicate sliced evidence for orientation {key}."
            )
        by_key[key] = item
        source_hashes.add(_sha256("source_sha256", item.source_sha256))

    actual_set = set(by_key)
    if actual_set != expected_set:
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        raise SlicedOrientationValidationError(
            "Sliced evidence must cover every proxy finalist exactly once; "
            f"missing={missing}, extra={extra}."
        )
    if len(source_hashes) != 1:
        raise SlicedOrientationValidationError(
            "All sliced finalist evidence must derive from the same exact source STL hash."
        )

    ordered = tuple(by_key[key] for key in expected_keys)
    candidates = tuple(item.spec.with_metrics(item.metrics) for item in ordered)
    decision = rank_orientation_candidates(
        candidates,
        require_sliced_validation=True,
        weights=weights,
    )
    return SlicedOrientationValidation(
        source_sha256=next(iter(source_hashes)),
        evidence=ordered,
        decision=decision,
    )


def sliced_orientation_manifest(
    validation: SlicedOrientationValidation,
    *,
    weights: Mapping[str, float] | None = None,
) -> dict:
    evidence_by_key = {item.canonical_key: item for item in validation.evidence}

    evidence_payload = []
    for ranked in validation.decision.ranked:
        key = ranked.candidate.canonical_key
        item = evidence_by_key[key]
        evidence_payload.append(
            {
                "orientation_deg": {"x": key[0], "y": key[1], "z": key[2]},
                "artifacts": item.artifact_record(),
                "native_metrics": {
                    "max_layer_area_mm2": float(item.max_layer_area_mm2),
                    "material_volume_mm3": float(item.material_volume_mm3),
                    "footprint_area_mm2": float(item.footprint_area_mm2),
                    "z_height_mm": float(item.z_height_mm),
                    "unresolved_islands": item.unresolved_islands,
                    "unresolved_suction_cups": item.unresolved_suction_cups,
                    "unresolved_resin_traps": item.unresolved_resin_traps,
                    "unresolved_touching_bounds": item.unresolved_touching_bounds,
                    "unresolved_empty_layers": item.unresolved_empty_layers,
                },
                "blocked_reasons": list(ranked.blocked_reasons),
                "score": ranked.score,
            }
        )

    selected = validation.selected_evidence
    return {
        "schema": SLICED_ORIENTATION_SCHEMA,
        "status": (
            "manual-review-required"
            if validation.decision.manual_review_required
            else "sliced-finalist-selected"
        ),
        "automatic_production_authority": False,
        "source_sha256": _sha256("source_sha256", validation.source_sha256),
        "finalist_coverage": "exact",
        "metric_authority": "exact-retained-printer-native-artifact",
        "decision": orientation_decision_manifest(validation.decision, weights=weights),
        "selected_artifacts": selected.artifact_record() if selected else None,
        "evidence": evidence_payload,
        "review_rule": (
            "Only proxy finalists derived from the same exact source STL and carrying exact retained 3MF/SL1/native hashes may enter sliced ranking. "
            "Soft ranking metrics are derived from the exact retained printer-native artifact, not hidden slicer state or geometry proxies. "
            "All engine-critical native issues are hard blockers. This decision still does not authorize production; plate materialization, printer mapping, calibrated resin tuple, and physical print acceptance remain mandatory."
        ),
    }
