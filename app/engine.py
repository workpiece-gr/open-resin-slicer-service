from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .profiles import Profile

PRUSA_SLICER_VERSION = "2.9.6"
PRUSA_SLICER_COMMIT = "b028299c770b8380ee81c921a2867d522f288123"
UVTOOLS_VERSION = "6.2.0"


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Orientation:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def validate(self) -> "Orientation":
        for value in (self.x, self.y, self.z):
            if not (-360.0 <= value <= 360.0):
                raise EngineError("Rotation values must be between -360 and 360 degrees.")
        return self


@dataclass(frozen=True)
class NativeArtifact:
    bytes: bytes
    filename: str
    media_type: str
    source_sha256: str
    intermediate_sha256: str
    native_sha256: str
    issue_summary: dict[str, int]
    issue_text: str
    printer_profile: str
    resin_profile: str
    quality_profile: str
    orientation: Orientation


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"External resin engine timed out after {timeout} seconds.") from exc


def build_prusa_command(
    prusa_bin: str,
    input_stl: Path,
    output_sl1: Path,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    orientation: Orientation,
) -> list[str]:
    orientation.validate()
    return [
        prusa_bin,
        "--load", str(printer.config),
        "--load", str(resin.config),
        "--load", str(quality.config),
        "--rotate-x", f"{orientation.x:g}",
        "--rotate-y", f"{orientation.y:g}",
        "--rotate", f"{orientation.z:g}",
        "--export-sla",
        "--output", str(output_sl1),
        str(input_stl),
    ]


def parse_uvtools_issues(text: str) -> dict[str, int]:
    """Best-effort stable extraction; raw output is always retained for review."""
    categories = {
        "islands": ("island",),
        "overhangs": ("overhang",),
        "resin_traps": ("resin trap",),
        "suction_cups": ("suction cup",),
        "touching_bounds": ("touching bound",),
        "empty_layers": ("empty layer",),
    }
    lower = text.lower()
    result: dict[str, int] = {}
    for key, terms in categories.items():
        count = 0
        for term in terms:
            # UVtools output varies by version; accept "term: 12" / "12 term(s)" forms.
            matches = re.findall(rf"{re.escape(term)}s?[ \t]*[:=][ \t]*(\d+)", lower)
            matches += re.findall(rf"(\d+)[ \t]+{re.escape(term)}s?\b", lower)
            if matches:
                count = max(count, *(int(x) for x in matches))
        result[key] = count
    return result


def critical_issue_count(summary: dict[str, int]) -> int:
    return sum(summary.get(k, 0) for k in ("islands", "resin_traps", "suction_cups", "touching_bounds", "empty_layers"))


def slice_native(
    source: bytes,
    *,
    original_name: str,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    orientation: Orientation,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    reject_critical: bool,
) -> NativeArtifact:
    native_format = str(printer.metadata.get("native_format", "")).lower().lstrip(".")
    uvtools_target = str(printer.metadata.get("uvtools_target") or native_format).strip()
    if native_format not in {"ctb", "goo"}:
        raise EngineError("Printer profile must declare a validated CTB or GOO native format in the initial service.")
    if not uvtools_target:
        raise EngineError("Printer profile has no UVtools conversion target.")
    if not shutil.which(prusa_bin) and not Path(prusa_bin).is_file():
        raise EngineError("Pinned PrusaSlicer binary is unavailable.")
    if not shutil.which(uvtools_cmd) and not Path(uvtools_cmd).is_file():
        raise EngineError("Pinned UVtoolsCmd binary is unavailable.")

    with tempfile.TemporaryDirectory(prefix="workpiece-resin-") as temp:
        root = Path(temp)
        input_path = root / "source.stl"
        intermediate_path = root / "production.sl1"
        native_path = root / f"production.{native_format}"
        input_path.write_bytes(source)

        prusa = _run(
            build_prusa_command(prusa_bin, input_path, intermediate_path, printer, resin, quality, orientation),
            timeout=slice_timeout,
        )
        if prusa.returncode != 0:
            raise EngineError(f"PrusaSlicer failed: {prusa.stdout[-2000:]}")
        if not intermediate_path.is_file() or intermediate_path.stat().st_size == 0:
            candidates = list(root.glob("*.sl1")) + list(root.glob("*.sl1s"))
            if len(candidates) == 1:
                intermediate_path = candidates[0]
            else:
                raise EngineError("PrusaSlicer did not produce the expected SLA archive.")

        conversion = _run(
            [uvtools_cmd, "convert", str(intermediate_path), uvtools_target, str(native_path)],
            timeout=uvtools_timeout,
        )
        if conversion.returncode != 0 or not native_path.is_file() or native_path.stat().st_size == 0:
            raise EngineError(f"UVtools conversion failed: {conversion.stdout[-2000:]}")

        inspection = _run([uvtools_cmd, "print-issues", str(native_path)], timeout=uvtools_timeout)
        if inspection.returncode != 0:
            raise EngineError(f"UVtools issue inspection failed: {inspection.stdout[-2000:]}")
        issue_text = inspection.stdout[-12000:]
        issues = parse_uvtools_issues(issue_text)
        if reject_critical and critical_issue_count(issues) > 0:
            raise EngineError("UVtools found critical resin-print issues; human review/correction is required.")

        intermediate = intermediate_path.read_bytes()
        native = native_path.read_bytes()
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem).strip("-._") or "workpiece"
        return NativeArtifact(
            bytes=native,
            filename=f"{safe_stem}-workpiece.{native_format}",
            media_type="application/octet-stream",
            source_sha256=_sha(source),
            intermediate_sha256=_sha(intermediate),
            native_sha256=_sha(native),
            issue_summary=issues,
            issue_text=issue_text,
            printer_profile=printer.id,
            resin_profile=resin.id,
            quality_profile=quality.id,
            orientation=orientation,
        )


def compact_metadata(artifact: NativeArtifact) -> str:
    payload = {
        "engine": {"prusaslicer": PRUSA_SLICER_VERSION, "uvtools": UVTOOLS_VERSION},
        "source_sha256": artifact.source_sha256,
        "intermediate_sha256": artifact.intermediate_sha256,
        "native_sha256": artifact.native_sha256,
        "printer_profile": artifact.printer_profile,
        "resin_profile": artifact.resin_profile,
        "quality_profile": artifact.quality_profile,
        "orientation_deg": {"x": artifact.orientation.x, "y": artifact.orientation.y, "z": artifact.orientation.z},
        "issues": artifact.issue_summary,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
