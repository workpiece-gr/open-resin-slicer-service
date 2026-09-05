from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

import app.materialized_plate_slice as plate_slice
from app.materialized_plate_slice import (
    MaterializedPlateSliceError,
    slice_materialized_plate_native,
)
from app.profiles import Profile


def _profile(tmp_path: Path) -> Profile:
    config = tmp_path / "printer.ini"
    config.write_text("printer_technology = SLA\n", encoding="utf-8")
    return Profile(
        id="printer-a",
        kind="printer",
        label="Printer A",
        candidate_ready=True,
        production_ready=False,
        config=config,
        metadata={
            "native_format": "ctb",
            "uvtools_target": "ctb",
        },
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fake_engine(monkeypatch, *, issue_text: str = "Issues: 0\n"):
    calls: list[list[str]] = []

    def fake_run(command, *, timeout):
        command = [str(item) for item in command]
        calls.append(command)
        if "--export-sla" in command:
            assert "--dont-arrange" in command
            assert "--center" not in command
            assert "--rotate" not in command
            assert "--rotate-x" not in command
            assert "--rotate-y" not in command
            output = Path(command[command.index("--output") + 1])
            project = Path(command[-1])
            config = Path(command[command.index("--load") + 1])
            assert project.name == "materialized.3mf"
            assert project.read_bytes() == b"exact materialized project"
            assert config.name == "effective.ini"
            assert config.read_bytes() == b"exact selected config"
            output.write_bytes(b"exact plate sl1")
            return subprocess.CompletedProcess(command, 0, "sliced")
        if len(command) >= 2 and command[1] == "convert":
            assert Path(command[2]).read_bytes() == b"exact plate sl1"
            Path(command[-1]).write_bytes(b"exact plate ctb")
            return subprocess.CompletedProcess(command, 1, "converted")
        if len(command) >= 2 and command[1] == "print-issues":
            assert Path(command[2]).read_bytes() == b"exact plate ctb"
            return subprocess.CompletedProcess(command, 1, issue_text)
        if len(command) >= 2 and command[1] == "print-properties" and "-r" not in command:
            assert Path(command[2]).read_bytes() == b"exact plate ctb"
            return subprocess.CompletedProcess(
                command,
                1,
                "LayerCount: 2\n"
                "PrintHeight: 0.1\n"
                "BoundingRectangleMillimeters: X=10 Y=20 Width=30 Height=40\n",
            )
        if len(command) >= 2 and command[1] == "print-properties" and "-r" in command:
            assert Path(command[2]).read_bytes() == b"exact plate ctb"
            assert command[command.index("-r") + 1] == "0:1"
            return subprocess.CompletedProcess(
                command,
                1,
                "# Layer: 0\nArea: 100\nVolume: 0.5\n"
                "# Layer: 1\nArea: 200\nVolume: 0.7\n",
            )
        raise AssertionError(f"Unexpected engine command: {command}")

    monkeypatch.setattr(plate_slice, "_run", fake_run)
    return calls


def _call(tmp_path: Path, monkeypatch, *, reject_critical: bool = True, issue_text: str = "Issues: 0\n"):
    project = b"exact materialized project"
    config = b"exact selected config"
    prusa = tmp_path / "prusaslicer"
    uvtools = tmp_path / "UVtoolsCmd"
    prusa.write_text("fake", encoding="utf-8")
    uvtools.write_text("fake", encoding="utf-8")
    calls = _fake_engine(monkeypatch, issue_text=issue_text)
    result = slice_materialized_plate_native(
        project_bytes=project,
        project_sha256=_sha(project),
        effective_config_bytes=config,
        effective_config_sha256=_sha(config),
        printer=_profile(tmp_path),
        prusa_bin=str(prusa),
        uvtools_cmd=str(uvtools),
        slice_timeout=10,
        uvtools_timeout=10,
        reject_critical=reject_critical,
    )
    return result, calls


def test_slices_exact_materialized_project_without_rebuild_or_rearrange(tmp_path, monkeypatch):
    result, calls = _call(tmp_path, monkeypatch)

    assert len(calls) == 5
    assert result.project_bytes == b"exact materialized project"
    assert result.project_sha256 == _sha(result.project_bytes)
    assert result.effective_config_bytes == b"exact selected config"
    assert result.effective_config_sha256 == _sha(result.effective_config_bytes)
    assert result.intermediate_bytes == b"exact plate sl1"
    assert result.intermediate_sha256 == _sha(result.intermediate_bytes)
    assert result.native_bytes == b"exact plate ctb"
    assert result.native_sha256 == _sha(result.native_bytes)
    assert result.issue_summary == {
        "islands": 0,
        "overhangs": 0,
        "resin_traps": 0,
        "suction_cups": 0,
        "touching_bounds": 0,
        "empty_layers": 0,
    }
    assert result.native_metrics.layer_count == 2
    assert result.native_metrics.max_layer_area_mm2 == 200
    assert result.native_metrics.material_volume_mm3 == 1.2
    assert result.native_metrics.footprint_area_mm2 == 1200
    assert result.native_metrics.z_height_mm == 0.1
    rectangle = result.native_metrics.bounding_rectangle
    assert (rectangle.x_mm, rectangle.y_mm, rectangle.width_mm, rectangle.height_mm) == (10, 20, 30, 40)
    assert result.printer_profile_id == "printer-a"


def test_wrong_project_hash_fails_before_any_external_engine_call(tmp_path, monkeypatch):
    project = b"exact materialized project"
    config = b"exact selected config"
    called = False

    def forbidden_run(command, *, timeout):
        nonlocal called
        called = True
        raise AssertionError("engine must not run after provenance mismatch")

    monkeypatch.setattr(plate_slice, "_run", forbidden_run)
    prusa = tmp_path / "prusaslicer"
    uvtools = tmp_path / "UVtoolsCmd"
    prusa.write_text("fake", encoding="utf-8")
    uvtools.write_text("fake", encoding="utf-8")

    with pytest.raises(MaterializedPlateSliceError, match="do not match"):
        slice_materialized_plate_native(
            project_bytes=project,
            project_sha256="0" * 64,
            effective_config_bytes=config,
            effective_config_sha256=_sha(config),
            printer=_profile(tmp_path),
            prusa_bin=str(prusa),
            uvtools_cmd=str(uvtools),
            slice_timeout=10,
            uvtools_timeout=10,
            reject_critical=True,
        )
    assert called is False


def test_wrong_selected_config_hash_fails_before_any_external_engine_call(tmp_path, monkeypatch):
    project = b"exact materialized project"
    config = b"exact selected config"
    called = False

    def forbidden_run(command, *, timeout):
        nonlocal called
        called = True
        raise AssertionError("engine must not run after provenance mismatch")

    monkeypatch.setattr(plate_slice, "_run", forbidden_run)
    prusa = tmp_path / "prusaslicer"
    uvtools = tmp_path / "UVtoolsCmd"
    prusa.write_text("fake", encoding="utf-8")
    uvtools.write_text("fake", encoding="utf-8")

    with pytest.raises(MaterializedPlateSliceError, match="do not match"):
        slice_materialized_plate_native(
            project_bytes=project,
            project_sha256=_sha(project),
            effective_config_bytes=config,
            effective_config_sha256="0" * 64,
            printer=_profile(tmp_path),
            prusa_bin=str(prusa),
            uvtools_cmd=str(uvtools),
            slice_timeout=10,
            uvtools_timeout=10,
            reject_critical=True,
        )
    assert called is False


def test_critical_uvtools_issue_blocks_authoritative_plate_slice(tmp_path, monkeypatch):
    with pytest.raises(MaterializedPlateSliceError, match="critical resin-print issues"):
        _call(
            tmp_path,
            monkeypatch,
            reject_critical=True,
            issue_text="Issues: 1\nIsland,1\n",
        )


def test_candidate_mode_retains_critical_issue_evidence_without_hiding_it(tmp_path, monkeypatch):
    result, calls = _call(
        tmp_path,
        monkeypatch,
        reject_critical=False,
        issue_text="Issues: 1\nIsland,1\n",
    )
    assert len(calls) == 5
    assert result.issue_summary["islands"] == 1
    assert "Island,1" in result.issue_text
    assert result.native_metrics.bounding_rectangle.width_mm == 30
