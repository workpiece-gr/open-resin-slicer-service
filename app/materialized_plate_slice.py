from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .engine import (
    UVTOOLS_SUCCESS_EXIT,
    EngineError,
    build_prusa_slice_command,
    critical_issue_count,
    parse_uvtools_issues,
)
from .profiles import Profile


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MaterializedPlateSliceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaterializedPlateNativeArtifact:
    project_bytes: bytes
    project_sha256: str
    effective_config_bytes: bytes
    effective_config_sha256: str
    intermediate_bytes: bytes
    intermediate_sha256: str
    native_bytes: bytes
    native_sha256: str
    intermediate_filename: str
    native_filename: str
    issue_summary: dict[str, int]
    issue_text: str
    printer_profile_id: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_sha(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise MaterializedPlateSliceError(
            f"{name} must be a lowercase 64-character SHA-256 digest."
        )
    return normalized


def _verify_exact_bytes(name: str, data: bytes, expected_sha256: str) -> str:
    if not isinstance(data, bytes) or not data:
        raise MaterializedPlateSliceError(f"{name} must contain exact non-empty bytes.")
    expected = _expected_sha(f"{name} sha256", expected_sha256)
    if _sha(data) != expected:
        raise MaterializedPlateSliceError(
            f"{name} bytes do not match their exact upstream SHA-256 receipt."
        )
    return expected


def _run(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise MaterializedPlateSliceError("External resin-engine timeout must be a positive integer number of seconds.")
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
        raise MaterializedPlateSliceError(
            f"External resin engine timed out after {timeout} seconds."
        ) from exc


def _require_uvtools_status(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != UVTOOLS_SUCCESS_EXIT:
        raise MaterializedPlateSliceError(
            f"Pinned UVtools {action} failed with status {result.returncode}: {result.stdout[-2000:]}"
        )


def slice_materialized_plate_native(
    *,
    project_bytes: bytes,
    project_sha256: str,
    effective_config_bytes: bytes,
    effective_config_sha256: str,
    printer: Profile,
    prusa_bin: str,
    uvtools_cmd: str,
    slice_timeout: int,
    uvtools_timeout: int,
    reject_critical: bool,
) -> MaterializedPlateNativeArtifact:
    """Slice an exact materialized plate 3MF without rebuilding or rearranging it.

    The project and effective config are both SHA-bound upstream artifacts. PrusaSlicer is
    invoked only with the retained recipe and ``--dont-arrange`` through the existing
    pinned command builder. UVtools then converts and inspects the exact resulting SLA
    archive. No STL import, orientation transform, centering, or profile re-resolution is
    performed on this path.
    """
    project_hash = _verify_exact_bytes("materialized plate 3MF", project_bytes, project_sha256)
    config_hash = _verify_exact_bytes(
        "selected effective config",
        effective_config_bytes,
        effective_config_sha256,
    )
    native_format = str(printer.metadata.get("native_format", "")).lower().lstrip(".")
    uvtools_target = str(printer.metadata.get("uvtools_target") or native_format).strip()
    if native_format not in {"ctb", "goo"}:
        raise MaterializedPlateSliceError(
            "Printer profile must declare a validated CTB or GOO native format."
        )
    if not uvtools_target:
        raise MaterializedPlateSliceError("Printer profile has no UVtools conversion target.")
    if not shutil.which(prusa_bin) and not Path(prusa_bin).is_file():
        raise MaterializedPlateSliceError("Pinned PrusaSlicer binary is unavailable.")
    if not shutil.which(uvtools_cmd) and not Path(uvtools_cmd).is_file():
        raise MaterializedPlateSliceError("Pinned UVtoolsCmd binary is unavailable.")

    with tempfile.TemporaryDirectory(prefix="workpiece-resin-plate-") as temp:
        root = Path(temp)
        project_path = root / "materialized.3mf"
        config_path = root / "effective.ini"
        intermediate_path = root / "production.sl1"
        native_path = root / f"production.{native_format}"
        project_path.write_bytes(project_bytes)
        config_path.write_bytes(effective_config_bytes)

        sliced = _run(
            build_prusa_slice_command(
                prusa_bin,
                project_path,
                config_path,
                intermediate_path,
            ),
            timeout=slice_timeout,
        )
        if sliced.returncode != 0:
            raise MaterializedPlateSliceError(
                f"PrusaSlicer SLA export from exact materialized plate failed: {sliced.stdout[-2000:]}"
            )
        if not intermediate_path.is_file() or intermediate_path.stat().st_size == 0:
            candidates = list(root.glob("*.sl1")) + list(root.glob("*.sl1s"))
            if len(candidates) == 1:
                intermediate_path = candidates[0]
            else:
                raise MaterializedPlateSliceError(
                    "PrusaSlicer did not produce the expected SLA archive from the exact materialized plate."
                )

        conversion = _run(
            [uvtools_cmd, "convert", str(intermediate_path), uvtools_target, str(native_path)],
            timeout=uvtools_timeout,
        )
        _require_uvtools_status(conversion, "plate conversion")
        if not native_path.is_file() or native_path.stat().st_size == 0:
            raise MaterializedPlateSliceError(
                "Pinned UVtools reported normal completion but produced no plate-native artifact."
            )

        inspection = _run(
            [uvtools_cmd, "print-issues", str(native_path), "--no-progress"],
            timeout=uvtools_timeout,
        )
        _require_uvtools_status(inspection, "plate issue inspection")
        try:
            issues = parse_uvtools_issues(inspection.stdout)
        except EngineError as exc:
            raise MaterializedPlateSliceError(str(exc)) from exc
        issue_text = inspection.stdout[-12000:]
        if reject_critical and critical_issue_count(issues) > 0:
            raise MaterializedPlateSliceError(
                "UVtools found critical resin-print issues on the materialized plate; review/correction is required."
            )

        intermediate = intermediate_path.read_bytes()
        native = native_path.read_bytes()

    return MaterializedPlateNativeArtifact(
        project_bytes=project_bytes,
        project_sha256=project_hash,
        effective_config_bytes=effective_config_bytes,
        effective_config_sha256=config_hash,
        intermediate_bytes=intermediate,
        intermediate_sha256=_sha(intermediate),
        native_bytes=native,
        native_sha256=_sha(native),
        intermediate_filename="workpiece-materialized-plate-intermediate.sl1",
        native_filename=f"workpiece-materialized-plate.{native_format}",
        issue_summary=issues,
        issue_text=issue_text,
        printer_profile_id=printer.id,
    )
