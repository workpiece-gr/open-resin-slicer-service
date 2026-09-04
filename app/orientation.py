from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence


class OrientationEvaluationError(ValueError):
    pass


_EVIDENCE_STAGES = {"geometry-only", "sliced", "uvtools-inspected"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_PROVENANCE = {
    "geometry-only": {"source_stl_sha256"},
    "sliced": {"source_stl_sha256", "review_3mf_sha256", "intermediate_sl1_sha256", "prusaslicer_commit"},
    "uvtools-inspected": {
        "source_stl_sha256",
        "review_3mf_sha256",
        "intermediate_sl1_sha256",
        "printer_native_sha256",
        "prusaslicer_commit",
        "uvtools_version",
    },
}


def _finite_nonnegative(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationEvaluationError(f"{name} must be a finite non-negative number.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationEvaluationError(f"{name} must be a finite non-negative number.") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise OrientationEvaluationError(f"{name} must be a finite non-negative number.")
    return numeric


def _nonnegative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OrientationEvaluationError(f"{name} must be a non-negative integer.")
    return value


def _angle(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise OrientationEvaluationError(f"{name} must be finite numeric degrees.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OrientationEvaluationError(f"{name} must be finite numeric degrees.") from exc
    if not math.isfinite(numeric):
        raise OrientationEvaluationError(f"{name} must be finite numeric degrees.")
    normalized = ((numeric + 180.0) % 360.0) - 180.0
    return 0.0 if normalized == -0.0 else round(normalized, 6)


def _required_text(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise OrientationEvaluationError(f"{name} is required.")
    return normalized


@dataclass(frozen=True)
class OrientationCandidate:
    candidate_id: str
    x_deg: float
    y_deg: float
    z_deg: float


@dataclass(frozen=True)
class OrientationEvidence:
    evidence_stage: str
    bounds_fit: bool
    z_height_mm: float
    peak_layer_area_mm2: float
    support_volume_mm3: float
    support_contact_count: int
    estimated_print_time_s: float
    island_count: int
    suction_cup_count: int
    sealed_cavity_count: int
    resin_trap_count: int
    provenance: Mapping[str, str]


@dataclass(frozen=True)
class OrientationPolicy:
    policy_id: str
    max_island_count: int = 0
    max_suction_cup_count: int = 0
    max_sealed_cavity_count: int = 0
    max_resin_trap_count: int = 0
    reference_z_height_mm: float = 150.0
    reference_peak_layer_area_mm2: float = 10_000.0
    reference_support_volume_mm3: float = 10_000.0
    reference_support_contact_count: int = 100
    reference_print_time_s: float = 21_600.0
    weight_z_height: float = 1.0
    weight_peak_layer_area: float = 2.0
    weight_support_volume: float = 1.0
    weight_support_contacts: float = 0.5
    weight_print_time: float = 0.5


def _validate_policy(policy: OrientationPolicy) -> dict[str, float | int | str]:
    values = {
        "policy_id": _required_text("policy_id", policy.policy_id),
        "max_island_count": _nonnegative_int("max_island_count", policy.max_island_count),
        "max_suction_cup_count": _nonnegative_int("max_suction_cup_count", policy.max_suction_cup_count),
        "max_sealed_cavity_count": _nonnegative_int("max_sealed_cavity_count", policy.max_sealed_cavity_count),
        "max_resin_trap_count": _nonnegative_int("max_resin_trap_count", policy.max_resin_trap_count),
        "reference_z_height_mm": _finite_nonnegative("reference_z_height_mm", policy.reference_z_height_mm),
        "reference_peak_layer_area_mm2": _finite_nonnegative(
            "reference_peak_layer_area_mm2", policy.reference_peak_layer_area_mm2
        ),
        "reference_support_volume_mm3": _finite_nonnegative(
            "reference_support_volume_mm3", policy.reference_support_volume_mm3
        ),
        "reference_support_contact_count": _nonnegative_int(
            "reference_support_contact_count", policy.reference_support_contact_count
        ),
        "reference_print_time_s": _finite_nonnegative("reference_print_time_s", policy.reference_print_time_s),
        "weight_z_height": _finite_nonnegative("weight_z_height", policy.weight_z_height),
        "weight_peak_layer_area": _finite_nonnegative("weight_peak_layer_area", policy.weight_peak_layer_area),
        "weight_support_volume": _finite_nonnegative("weight_support_volume", policy.weight_support_volume),
        "weight_support_contacts": _finite_nonnegative("weight_support_contacts", policy.weight_support_contacts),
        "weight_print_time": _finite_nonnegative("weight_print_time", policy.weight_print_time),
    }
    for key in (
        "reference_z_height_mm",
        "reference_peak_layer_area_mm2",
        "reference_support_volume_mm3",
        "reference_support_contact_count",
        "reference_print_time_s",
    ):
        if values[key] == 0:
            raise OrientationEvaluationError(f"{key} must be greater than zero.")
    return values


def _validated_provenance(stage: str, provenance: Mapping[str, str]) -> dict[str, str]:
    result = {}
    for key, value in sorted(provenance.items()):
        normalized_key = _required_text("provenance key", key)
        normalized_value = _required_text("provenance value", value)
        if normalized_key.endswith("_sha256"):
            normalized_value = normalized_value.lower()
            if not _SHA256_RE.fullmatch(normalized_value):
                raise OrientationEvaluationError(
                    f"{normalized_key} must be a 64-character SHA-256 hex digest."
                )
        result[normalized_key] = normalized_value
    missing = sorted(_REQUIRED_PROVENANCE[stage] - set(result))
    if missing:
        raise OrientationEvaluationError(
            f"{stage} evidence is missing required provenance: {', '.join(missing)}."
        )
    return result


def evaluate_orientation(
    candidate: OrientationCandidate,
    evidence: OrientationEvidence,
    *,
    policy: OrientationPolicy,
) -> dict:
    """Evaluate one measured resin orientation without granting production authority."""
    policy_values = _validate_policy(policy)
    candidate_id = _required_text("candidate_id", candidate.candidate_id)
    stage = str(evidence.evidence_stage).strip()
    if stage not in _EVIDENCE_STAGES:
        raise OrientationEvaluationError("evidence_stage is not recognized.")
    if not isinstance(evidence.bounds_fit, bool):
        raise OrientationEvaluationError("bounds_fit must be boolean.")

    angles = {
        "x": _angle("x_deg", candidate.x_deg),
        "y": _angle("y_deg", candidate.y_deg),
        "z": _angle("z_deg", candidate.z_deg),
    }
    metrics = {
        "z_height_mm": _finite_nonnegative("z_height_mm", evidence.z_height_mm),
        "peak_layer_area_mm2": _finite_nonnegative("peak_layer_area_mm2", evidence.peak_layer_area_mm2),
        "support_volume_mm3": _finite_nonnegative("support_volume_mm3", evidence.support_volume_mm3),
        "support_contact_count": _nonnegative_int("support_contact_count", evidence.support_contact_count),
        "estimated_print_time_s": _finite_nonnegative("estimated_print_time_s", evidence.estimated_print_time_s),
        "island_count": _nonnegative_int("island_count", evidence.island_count),
        "suction_cup_count": _nonnegative_int("suction_cup_count", evidence.suction_cup_count),
        "sealed_cavity_count": _nonnegative_int("sealed_cavity_count", evidence.sealed_cavity_count),
        "resin_trap_count": _nonnegative_int("resin_trap_count", evidence.resin_trap_count),
    }
    provenance = _validated_provenance(stage, evidence.provenance)

    blockers = []
    if not evidence.bounds_fit:
        blockers.append("outside_validated_manufacturing_envelope")
    for metric_key, policy_key, blocker in (
        ("island_count", "max_island_count", "islands_exceed_policy"),
        ("suction_cup_count", "max_suction_cup_count", "suction_cups_exceed_policy"),
        ("sealed_cavity_count", "max_sealed_cavity_count", "sealed_cavities_exceed_policy"),
        ("resin_trap_count", "max_resin_trap_count", "resin_traps_exceed_policy"),
    ):
        if metrics[metric_key] > policy_values[policy_key]:
            blockers.append(blocker)

    components = {
        "z_height": policy.weight_z_height * metrics["z_height_mm"] / policy_values["reference_z_height_mm"],
        "peak_layer_area": (
            policy.weight_peak_layer_area
            * metrics["peak_layer_area_mm2"]
            / policy_values["reference_peak_layer_area_mm2"]
        ),
        "support_volume": (
            policy.weight_support_volume
            * metrics["support_volume_mm3"]
            / policy_values["reference_support_volume_mm3"]
        ),
        "support_contacts": (
            policy.weight_support_contacts
            * metrics["support_contact_count"]
            / policy_values["reference_support_contact_count"]
        ),
        "print_time": (
            policy.weight_print_time
            * metrics["estimated_print_time_s"]
            / policy_values["reference_print_time_s"]
        ),
    }
    rounded_components = {key: round(value, 9) for key, value in components.items()}
    score = round(sum(rounded_components.values()), 9)

    return {
        "schema": "workpiece-resin-orientation-evaluation-v1",
        "candidate_id": candidate_id,
        "orientation_deg": angles,
        "policy_id": policy_values["policy_id"],
        "evidence_stage": stage,
        "provenance": provenance,
        "metrics": metrics,
        "hard_blockers": blockers,
        "hard_blocked": bool(blockers),
        "score_components": rounded_components,
        "soft_score": score,
        "production_evidence_complete": stage == "uvtools-inspected",
        "human_review_required": True,
        "authority": "candidate-ranking-only",
    }


def rank_orientations(evaluations: Sequence[Mapping]) -> list[dict]:
    """Rank unblocked candidates first, then deterministic soft score and candidate ID."""
    normalized = []
    seen_ids = set()
    for item in evaluations:
        if item.get("schema") != "workpiece-resin-orientation-evaluation-v1":
            raise OrientationEvaluationError("All evaluations must use the orientation evaluation schema.")
        candidate_id = _required_text("candidate_id", item.get("candidate_id", ""))
        if candidate_id in seen_ids:
            raise OrientationEvaluationError("candidate_id values must be unique.")
        seen_ids.add(candidate_id)
        score = _finite_nonnegative("soft_score", item.get("soft_score"))
        hard_blocked = item.get("hard_blocked")
        if not isinstance(hard_blocked, bool):
            raise OrientationEvaluationError("hard_blocked must be boolean.")
        normalized.append((hard_blocked, score, candidate_id, dict(item)))
    normalized.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in normalized]
