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
    effective_config_bytes: bytes
    effective_config_filename: str
    effective_config_sha256: str
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
    output_effective_config: Path,
    printer: Profile,
    resin: Profile,
    quality: Profile,
    orientation: Orientation,
) -> list[str]:
    """Export oriented review geometry and the merged effective SLA configuration."""
    return [
        prusa_bin,
        *_profile_args(printer, resin, quality),
        *_orientation_args(orientation),
        "--save", str(output_effective_config),
        "--export-3mf",
        "--output", str(output_3mf),
        str(input_stl),
    ]


def build_prusa_slice_command(
    prusa_bin: str,
    input_project: Path,
    effective_config: Path,
    output_sl1: Path,
) -> list[str]:
    """Slice retained geometry using the exact merged config; never reapply orientation."""
    return [
        prusa_bin,
        "--load", str(effective_config),
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
    """Legacy helper retained for callers/tests; authoritative path is recipe-first."""
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
        effective_config_path = root / "effective.ini"
        intermediate_path = root / "production.sl1"
        native_path = root / f"production.{native_format}"
        input_path.write_bytes(source)

        # 1) Export the oriented geometry and, in the same pinned Prusa invocation,
        #    save the fully merged effective SLA config. Prusa CLI --export-3mf passes
        #    a null config pointer to its 3MF writer, so this separate config file is an
        #    intentional part of the recipe rather than pretending the 3MF embeds it.
        project = _run(
            build_prusa_project_command(
                prusa_bin,
                input_path,
                project_path,
                effective_config_path,
                printer,
                resin,
                quality,
                orientation,
            ),
            timeout=slice_timeout,
        )
        if project.returncode != 0:
            raise EngineError(f"PrusaSlicer recipe export failed: {project.stdout[-2000:]}")
        if not project_path.is_file() or project_path.stat().st_size == 0:
            candidates = list(root.glob("*.3mf"))
            if len(candidates) == 1:
                project_path = candidates[0]
            else:
                raise EngineError(f"PrusaSlicer did not produce the expected review 3MF: {project.stdout[-2000:]}")
        if not effective_config_path.is_file() or effective_config_path.stat().st_size == 0:
            raise EngineError(f"PrusaSlicer did not produce the merged effective SLA config: {project.stdout[-2000:]}")

        # 2) Slice the retained oriented geometry using that exact merged config.
        #    Do not reapply rotations. Single-model placement remains PrusaSlicer's
        #    slicing behavior in CP1; explicit plate coordinates become authoritative
        #    in the later plate-layout checkpoint.
        sliced = _run(
            build_prusa_slice_command(
                prusa_bin, project_path, effective_config_path, intermediate_path
            ),
            timeout=slice_timeout,
        )
        if sliced.returncode != 0:
            raise EngineError(f"PrusaSlicer SLA export from retained recipe failed: {sliced.stdout[-2000:]}")
        if not intermediate_path.is_file() or intermediate_path.stat().st_size == 0:
            candidates = list(root.glob("*.sl1")) + list(root.glob("*.sl1s"))
            if len(candidates) == 1:
                intermediate_path = candidates[0]
            else:
                raise EngineError(
                    "PrusaSlicer returned success but produced no SLA archive from the retained recipe: "
                    f"{sliced.stdout[-2000:]}"
                )

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
        effective_config_bytes = effective_config_path.read_bytes()
        intermediate = intermediate_path.read_bytes()
        native = native_path.read_bytes()
        return NativeArtifact(
            project_bytes=project_bytes,
            project_filename=f"{safe_stem}-workpiece-review.3mf",
            project_sha256=_sha(project_bytes),
            effective_config_bytes=effective_config_bytes,
            effective_config_filename=f"{safe_stem}-workpiece-effective.ini",
            effective_config_sha256=_sha(effective_config_bytes),
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
        "slice_recipe": {
            "geometry": "review_3mf",
            "configuration": "effective_config",
            "review_3mf_role": (
                "Oriented geometry/review artifact exported by the pinned Prusa CLI; "
                "it is not treated as a self-contained Prusa configuration project."
            ),
            "effective_config_role": "Merged effective SLA configuration saved by the same pinned Prusa invocation.",
        },
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
            "effective_config": {"name": artifact.effective_config_filename, "sha256": artifact.effective_config_sha256},
            "intermediate_sl1": {"name": artifact.intermediate_filename, "sha256": artifact.intermediate_sha256},
            "printer_native": {"name": artifact.filename, "sha256": artifact.native_sha256},
            "uvtools_issues": {"name": "uvtools-issues.txt"},
        },
        "issues": artifact.issue_summary,
        "review_rule": (
            "The printer-native artifact is valid only for this exact review 3MF hash and effective-config hash. "
            "If either recipe artifact is edited, do not print the bundled native file; regenerate the bundle."
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
        _zip_write(zf, artifact.effective_config_filename, artifact.effective_config_bytes)
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
        "effective_config_sha256": artifact.effective_config_sha256,
        "intermediate_sha256": artifact.intermediate_sha256,
        "native_sha256": artifact.native_sha256,
        "printer_profile": artifact.printer_profile,
        "resin_profile": artifact.resin_profile,
        "quality_profile": artifact.quality_profile,
        "orientation_deg": {"x": artifact.orientation.x, "y": artifact.orientation.y, "z": artifact.orientation.z},
        "issues": artifact.issue_summary,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
