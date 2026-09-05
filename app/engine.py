from __future__ import annotations

import hashlib
import io
import json
import math
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
UVTOOLS_SUCCESS_EXIT = 1  # pinned UVtools 6.2.0 Program.Main returns 1 after normal commands


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Orientation:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def validate(self) -> "Orientation":
        for value in (self.x, self.y, self.z):
            if isinstance(value, bool):
                raise EngineError("Rotation values must be finite numbers between -360 and 360 degrees.")
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise EngineError("Rotation values must be finite numbers between -360 and 360 degrees.") from exc
            if not math.isfinite(numeric) or not (-360.0 <= numeric <= 360.0):
                raise EngineError("Rotation values must be finite numbers between -360 and 360 degrees.")
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
    effective_config_bytes: bytes = b""
    effective_config_filename: str = ""
    effective_config_sha256: str = ""


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


def _positive_profile_number(printer: Profile, key: str) -> float:
    raw = printer.metadata.get(key)
    if isinstance(raw, bool):
        raise EngineError(f"Printer profile {printer.id} requires positive finite {key}.")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise EngineError(f"Printer profile {printer.id} requires positive finite {key}.") from exc
    if not math.isfinite(value) or value <= 0:
        raise EngineError(f"Printer profile {printer.id} requires positive finite {key}.")
    return value


def _display_center_args(printer: Profile) -> list[str]:
    width = _positive_profile_number(printer, "display_width_mm")
    depth = _positive_profile_number(printer, "display_height_mm")
    return ["--center", f"{width / 2:g},{depth / 2:g}"]


def _orientation_args(orientation: Orientation) -> list[str]:
    orientation.validate()
    return [
        "--rotate-x", f"{float(orientation.x):g}",
        "--rotate-y", f"{float(orientation.y):g}",
        "--rotate", f"{float(orientation.z):g}",
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
    """Build retained geometry/placement and resolved config in one pinned invocation."""
    return [
        prusa_bin,
        *_profile_args(printer, resin, quality),
        *_display_center_args(printer),
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
    """Slice the exact retained recipe pair without rearranging/reapplying transforms."""
    return [
        prusa_bin,
        "--load", str(effective_config),
        "--dont-arrange",
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
    """Legacy direct-slice helper; authoritative path uses the paired retained recipe."""
    return [
        prusa_bin,
        *_profile_args(printer, resin, quality),
        *_orientation_args(orientation),
        "--export-sla",
        "--output", str(output_sl1),
        str(input_stl),
    ]


def parse_uvtools_issues(text: str) -> dict[str, int]:
    """Parse the exact pinned UVtools 6.2.0 ``print-issues`` text contract.

    UVtools 6.2.0 prints one ``Issues: N`` header followed by exactly N issue rows
    whose first CSV field is the ``MainIssue.IssueType`` enum value. Production must
    never interpret an absent/changed format as zero issues, so any mismatch fails
    closed instead of returning a best-effort count.
    """
    categories = {
        "Island": "islands",
        "Overhang": "overhangs",
        "ResinTrap": "resin_traps",
        "SuctionCup": "suction_cups",
        "TouchingBound": "touching_bounds",
        "EmptyLayer": "empty_layers",
    }
    recognized_types = {*categories, "PrintHeight", "Debug"}
    result = {key: 0 for key in categories.values()}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headers = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"Issues:\s*(\d+)", line)
        if match:
            headers.append((index, match))
    if len(headers) != 1:
        raise EngineError(
            f"Pinned UVtools {UVTOOLS_VERSION} issue output is not confidently parseable: expected one Issues total."
        )

    header_index, header = headers[0]
    total = int(header.group(1))
    issue_lines = lines[header_index + 1:]
    if len(issue_lines) != total:
        raise EngineError(
            f"Pinned UVtools {UVTOOLS_VERSION} issue output is incomplete: declared {total} issues but emitted {len(issue_lines)} rows."
        )

    for line in issue_lines:
        issue_type, separator, _ = line.partition(",")
        if not separator or issue_type not in recognized_types:
            raise EngineError(
                f"Pinned UVtools {UVTOOLS_VERSION} emitted an unrecognized issue row; refusing to infer zero critical issues."
            )
        key = categories.get(issue_type)
        if key is not None:
            result[key] += 1
    return result


def critical_issue_count(summary: dict[str, int]) -> int:
    return sum(summary.get(k, 0) for k in ("islands", "resin_traps", "suction_cups", "touching_bounds", "empty_layers"))


def _require_uvtools_status(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != UVTOOLS_SUCCESS_EXIT:
        raise EngineError(
            f"Pinned UVtools {action} failed with status {result.returncode}: {result.stdout[-2000:]}"
        )


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
            raise EngineError(f"PrusaSlicer retained recipe export failed: {project.stdout[-2000:]}")
        if not project_path.is_file() or project_path.stat().st_size == 0:
            candidates = list(root.glob("*.3mf"))
            if len(candidates) == 1:
                project_path = candidates[0]
            else:
                raise EngineError("PrusaSlicer did not produce the expected review 3MF project.")
        if not effective_config_path.is_file() or effective_config_path.stat().st_size == 0:
            raise EngineError("PrusaSlicer did not produce the resolved effective resin configuration.")

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
                raise EngineError("PrusaSlicer did not produce the expected SLA archive from the retained recipe.")

        conversion = _run(
            [uvtools_cmd, "convert", str(intermediate_path), uvtools_target, str(native_path)],
            timeout=uvtools_timeout,
        )
        _require_uvtools_status(conversion, "conversion")
        if not native_path.is_file() or native_path.stat().st_size == 0:
            raise EngineError("Pinned UVtools reported normal completion but produced no native artifact.")

        inspection = _run(
            [uvtools_cmd, "print-issues", str(native_path), "--no-progress"],
            timeout=uvtools_timeout,
        )
        _require_uvtools_status(inspection, "issue inspection")
        issues = parse_uvtools_issues(inspection.stdout)
        issue_text = inspection.stdout[-12000:]
        if reject_critical and critical_issue_count(issues) > 0:
            raise EngineError("UVtools found critical resin-print issues; human review/correction is required.")

        project_bytes = project_path.read_bytes()
        effective_config = effective_config_path.read_bytes()
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
            effective_config_bytes=effective_config,
            effective_config_filename=f"{safe_stem}-workpiece-effective.ini",
            effective_config_sha256=_sha(effective_config),
        )


def _require_effective_config(artifact: NativeArtifact) -> None:
    if not artifact.effective_config_bytes or not artifact.effective_config_filename:
        raise EngineError("Native artifact is missing its retained effective resin configuration.")
    if artifact.effective_config_sha256 != _sha(artifact.effective_config_bytes):
        raise EngineError("Native artifact effective configuration hash does not match its bytes.")


def artifact_manifest(
    artifact: NativeArtifact,
    *,
    source_filename: str,
    authority: str,
    execution_environment: dict[str, object] | None = None,
) -> dict:
    _require_effective_config(artifact)
    manifest = {
        "schema": "workpiece-resin-bundle-v2",
        "authority": authority,
        "provenance_chain": [
            "source_stl",
            "review_3mf",
            "effective_config",
            "intermediate_sl1",
            "printer_native",
        ],
        "engine": {
            "prusaslicer": {"version": PRUSA_SLICER_VERSION, "commit": PRUSA_SLICER_COMMIT},
            "uvtools": {"version": UVTOOLS_VERSION, "normal_command_exit": UVTOOLS_SUCCESS_EXIT},
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
            "effective_config": {
                "name": artifact.effective_config_filename,
                "sha256": artifact.effective_config_sha256,
            },
            "intermediate_sl1": {"name": artifact.intermediate_filename, "sha256": artifact.intermediate_sha256},
            "printer_native": {"name": artifact.filename, "sha256": artifact.native_sha256},
            "uvtools_issues": {"name": "uvtools-issues.txt"},
        },
        "issues": artifact.issue_summary,
        "recipe_rule": (
            "Pinned PrusaSlicer CLI 2.9.6 does not embed print configuration in --export-3mf output; "
            "the review 3MF and effective config are therefore one indivisible retained recipe."
        ),
        "review_rule": (
            "The printer-native file is valid only for these exact review 3MF and effective-config hashes. "
            "If either is edited, do not print the bundled native file; regenerate the artifact chain."
        ),
    }
    if execution_environment is not None:
        manifest["execution_environment"] = dict(execution_environment)
    return manifest


def _zip_write(
    zf: zipfile.ZipFile,
    name: str,
    data: bytes,
    *,
    compress_type: int = zipfile.ZIP_DEFLATED,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compress_type
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def build_review_bundle(
    source: bytes,
    *,
    original_name: str,
    artifact: NativeArtifact,
    authority: str,
    execution_environment: dict[str, object] | None = None,
) -> tuple[bytes, str]:
    _require_effective_config(artifact)
    safe_stem = _safe_stem(original_name)
    source_filename = f"{safe_stem}-source.stl"
    manifest = artifact_manifest(
        artifact,
        source_filename=source_filename,
        authority=authority,
        execution_environment=execution_environment,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _zip_write(zf, source_filename, source)
        _zip_write(zf, artifact.project_filename, artifact.project_bytes, compress_type=zipfile.ZIP_STORED)
        _zip_write(zf, artifact.effective_config_filename, artifact.effective_config_bytes)
        _zip_write(zf, artifact.intermediate_filename, artifact.intermediate_bytes, compress_type=zipfile.ZIP_STORED)
        _zip_write(zf, artifact.filename, artifact.bytes, compress_type=zipfile.ZIP_STORED)
        _zip_write(zf, "uvtools-issues.txt", artifact.issue_text.encode("utf-8", errors="replace"))
        _zip_write(zf, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    return buffer.getvalue(), f"{safe_stem}-workpiece-resin-bundle.zip"


def compact_metadata(
    artifact: NativeArtifact,
    *,
    execution_environment: dict[str, object] | None = None,
) -> str:
    _require_effective_config(artifact)
    payload = {
        "engine": {
            "prusaslicer": PRUSA_SLICER_VERSION,
            "uvtools": UVTOOLS_VERSION,
            "uvtools_normal_command_exit": UVTOOLS_SUCCESS_EXIT,
        },
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
    if execution_environment is not None:
        payload["execution_environment"] = dict(execution_environment)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
