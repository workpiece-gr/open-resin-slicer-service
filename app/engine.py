from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
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
    project_bytes: bytes
    project_filename: str
    project_sha256: str
    intermediate_bytes: bytes
    intermediate_filename: str
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


def _safe_stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-._") or "workpiece"


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


def _profile_args(printer: Profile, resin: Profile, quality: Profile) -> list[str]:
    return [
        "--load", str(printer.config),
        "--load", str(resin.config),
        "--load", str(quality.config),
    ]


def _orientation_args(orientation: Orientation) -> list[str]:
    orientation.validate()
    return [
        "--rotate-x", f"{orientation.x:g}",
        "--rotate-y", f"{orientation.y:g}",
        "--rotate", f"{orientation.z:g}",
    ]


def build_prusa_project_command(
    prusa_bin: str,
    input_stl: Path,
    output_3mf: Path,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    orientation: Orientation,
) -> list[str]:
    """Build the exact review project first; every later artifact derives from this 3MF."""
    return [
        prusa_bin,
        *_profile_args(printer, resin, quality),
        *_orientation_args(orientation),
        "--export-3mf",
        "--output", str(output_3mf),
        str(input_stl),
    ]


def build_prusa_slice_command(prusa_bin: str, input_project: Path, output_sl1: Path) -> list[str]:
    """Slice the retained 3MF itself. Do not reload profiles or reapply transforms here."""
    return [
        prusa_bin,
        "--export-sla",
        "--output", str(output_sl1),
        str(input_project),
    ]


def build_prusa_command(
    prusa_bin: str,
    input_stl: Path,
    output_sl1: Path,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    orientation: Orientation,
) -> list[str]:
    """Legacy helper retained for callers/tests; new production path is 3MF-first."""
    return [
        prusa_bin,
        *_profile_args(printer, resin, quality),
        *_orientation_args(orientation),
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

    orientation.validate()
    safe_stem = _safe_stem(original_name)

    with tempfile.TemporaryDirectory(prefix="workpiece-resin-") as temp:
        root = Path(temp)
        input_path = root / "source.stl"
        project_path = root / "review.3mf"
        intermediate_path = root / "production.sl1"
        native_path = root / f"production.{native_format}"
        input_path.write_bytes(source)

        # 1) Build the human-review project. It carries model placement/orientation and
        #    the exact printer/material/quality settings used for subsequent slicing.
        project = _run(
            build_prusa_project_command(
                prusa_bin, input_path, project_path, printer, resin, quality, orientation
            ),
            timeout=slice_timeout,
        )
        if project.returncode != 0:
            raise EngineError(f"PrusaSlicer 3MF project export failed: {project.stdout[-2000:]}")
        if not project_path.is_file() or project_path.stat().st_size == 0:
            candidates = list(root.glob("*.3mf"))
            if len(candidates) == 1:
                project_path = candidates[0]
            else:
                raise EngineError("PrusaSlicer did not produce the expected review 3MF project.")

        # 2) Slice the exact retained 3MF, with no second profile load or transform pass.
        #    This makes the project hash part of the authoritative provenance chain.
        sliced = _run(
            build_prusa_slice_command(prusa_bin, project_path, intermediate_path),
            timeout=slice_timeout,
        )
        if sliced.returncode != 0:
            raise EngineError(f"PrusaSlicer SLA export from review 3MF failed: {sliced.stdout[-2000:]}")
        if not intermediate_path.is_file() or intermediate_path.stat().st_size == 0:
            candidates = list(root.glob("*.sl1")) + list(root.glob("*.sl1s"))
            if len(candidates) == 1:
                intermediate_path = candidates[0]
            else:
                raise EngineError("PrusaSlicer did not produce the expected SLA archive from the review 3MF.")

        # 3) Convert to the exact printer-native file and inspect it.
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

        project_bytes = project_path.read_bytes()
        intermediate = intermediate_path.read_bytes()
        native = native_path.read_bytes()
        return NativeArtifact(
            project_bytes=project_bytes,
            project_filename=f"{safe_stem}-workpiece-review.3mf",
            project_sha256=_sha(project_bytes),
            intermediate_bytes=intermediate,
            intermediate_filename=f"{safe_stem}-workpiece-intermediate.sl1",
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


def artifact_manifest(artifact: NativeArtifact, *, source_filename: str, authority: str) -> dict:
    return {
        "schema": "workpiece-resin-bundle-v1",
        "authority": authority,
        "provenance_chain": ["source_stl", "review_3mf", "intermediate_sl1", "printer_native"],
        "engine": {
            "prusaslicer": {"version": PRUSA_SLICER_VERSION, "commit": PRUSA_SLICER_COMMIT},
            "uvtools": {"version": UVTOOLS_VERSION},
        },
        "profiles": {
            "printer": artifact.printer_profile,
            "resin": artifact.resin_profile,
            "quality": artifact.quality_profile,
        },
        "orientation_deg": {
            "x": artifact.orientation.x,
            "y": artifact.orientation.y,
            "z": artifact.orientation.z,
        },
        "files": {
            "source_stl": {"name": source_filename, "sha256": artifact.source_sha256},
            "review_3mf": {"name": artifact.project_filename, "sha256": artifact.project_sha256},
            "intermediate_sl1": {"name": artifact.intermediate_filename, "sha256": artifact.intermediate_sha256},
            "printer_native": {"name": artifact.filename, "sha256": artifact.native_sha256},
            "uvtools_issues": {"name": "uvtools-issues.txt"},
        },
        "issues": artifact.issue_summary,
        "review_rule": (
            "The CTB is valid only for this exact review 3MF hash. If the 3MF is edited, "
            "do not print the bundled CTB; regenerate the bundle from the revised project."
        ),
    }


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def build_review_bundle(source: bytes, *, original_name: str, artifact: NativeArtifact, authority: str) -> tuple[bytes, str]:
    safe_stem = _safe_stem(original_name)
    source_filename = f"{safe_stem}-source.stl"
    manifest = artifact_manifest(artifact, source_filename=source_filename, authority=authority)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _zip_write(zf, source_filename, source)
        _zip_write(zf, artifact.project_filename, artifact.project_bytes)
        _zip_write(zf, artifact.intermediate_filename, artifact.intermediate_bytes)
        _zip_write(zf, artifact.filename, artifact.bytes)
        _zip_write(zf, "uvtools-issues.txt", artifact.issue_text.encode("utf-8", errors="replace"))
        _zip_write(zf, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    return buffer.getvalue(), f"{safe_stem}-workpiece-resin-bundle.zip"


def compact_metadata(artifact: NativeArtifact) -> str:
    payload = {
        "engine": {"prusaslicer": PRUSA_SLICER_VERSION, "uvtools": UVTOOLS_VERSION},
        "source_sha256": artifact.source_sha256,
        "project_sha256": artifact.project_sha256,
        "intermediate_sha256": artifact.intermediate_sha256,
        "native_sha256": artifact.native_sha256,
        "printer_profile": artifact.printer_profile,
        "resin_profile": artifact.resin_profile,
        "quality_profile": artifact.quality_profile,
        "orientation_deg": {"x": artifact.orientation.x, "y": artifact.orientation.y, "z": artifact.orientation.z},
        "issues": artifact.issue_summary,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
