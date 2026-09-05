from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import app.materialized_plate_execution as plate_execution
from app.materialization import prepare_printer_plate_materialization
from app.materialization_selected import (
    SelectedPlateMaterializationSpec,
    SelectedPlateProjectMaterialization,
)
from app.materialized_plate_execution import (
    SelectedPlateExecutionError,
    execute_selected_materialized_plate_native,
    selected_materialized_plate_native_manifest,
)
from app.materialized_plate_slice import MaterializedPlateNativeArtifact
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances
from app.profiles import Profile
from app.prusa_3mf_instances import Materialized3MFProject


SOURCE_SHA = "d" * 64
REVIEW_SHA = "a" * 64
WINNER_INTERMEDIATE_SHA = "b" * 64
WINNER_NATIVE_SHA = "c" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _printer(tmp_path: Path, *, profile_id: str = "printer-a") -> Profile:
    config = tmp_path / f"{profile_id}.ini"
    config.write_text("printer_technology = SLA\n", encoding="utf-8")
    return Profile(
        id=profile_id,
        kind="printer",
        label=profile_id,
        candidate_ready=True,
        production_ready=False,
        config=config,
        metadata={"native_format": "ctb", "uvtools_target": "ctb"},
    )


def _materialization(config_bytes: bytes) -> SelectedPlateProjectMaterialization:
    plan = plan_rectangular_instances(
        footprint_width_mm=20,
        footprint_depth_mm=20,
        quantity=1,
        plate_width_mm=80,
        plate_depth_mm=60,
        spacing_mm=5,
        edge_margin_mm=5,
        allow_rotate_90=False,
    )
    plate_spec = prepare_printer_plate_materialization(
        PrinterPlatePlan(
            printer_profile_id="printer-a",
            manufacturing_envelope_coordinate_mapping="validated",
            plan=plan,
        ),
        plate_index=1,
        pretranslation_envelope=Envelope2D(10, 30, 20, 40),
        require_validated_mapping=True,
    )
    project_bytes = b"exact selected materialized 3mf"
    project_sha = _sha(project_bytes)
    project = Materialized3MFProject(
        bytes=project_bytes,
        sha256=project_sha,
        instance_count=1,
        instance_indices=(1,),
        display_transforms=((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 30.0, 30.0, 0.0),),
    )
    spec = SelectedPlateMaterializationSpec(
        source_sha256=SOURCE_SHA,
        selected_orientation_deg=(15.0, 0.0, 0.0),
        selected_review_3mf_sha256=REVIEW_SHA,
        selected_effective_config_sha256=_sha(config_bytes),
        selected_intermediate_sl1_sha256=WINNER_INTERMEDIATE_SHA,
        selected_printer_native_sha256=WINNER_NATIVE_SHA,
        plate_spec=plate_spec,
    )
    return SelectedPlateProjectMaterialization(
        spec=spec,
        display_placements=(),
        project=project,
    )


def test_selected_execution_passes_only_exact_bound_project_and_config(tmp_path, monkeypatch):
    config_bytes = b"exact selected effective config"
    materialization = _materialization(config_bytes)
    captured = {}

    def fake_slice(**kwargs):
        captured.update(kwargs)
        return MaterializedPlateNativeArtifact(
            project_bytes=kwargs["project_bytes"],
            project_sha256=kwargs["project_sha256"],
            effective_config_bytes=kwargs["effective_config_bytes"],
            effective_config_sha256=kwargs["effective_config_sha256"],
            intermediate_bytes=b"plate sl1",
            intermediate_sha256=_sha(b"plate sl1"),
            native_bytes=b"plate ctb",
            native_sha256=_sha(b"plate ctb"),
            intermediate_filename="plate.sl1",
            native_filename="plate.ctb",
            issue_summary={
                "islands": 0,
                "overhangs": 0,
                "resin_traps": 0,
                "suction_cups": 0,
                "touching_bounds": 0,
                "empty_layers": 0,
            },
            issue_text="Issues: 0\n",
            printer_profile_id=kwargs["printer"].id,
        )

    monkeypatch.setattr(plate_execution, "slice_materialized_plate_native", fake_slice)
    printer = _printer(tmp_path)
    result = execute_selected_materialized_plate_native(
        materialization,
        selected_effective_config_bytes=config_bytes,
        printer=printer,
        prusa_bin="prusaslicer",
        uvtools_cmd="UVtoolsCmd",
        slice_timeout=10,
        uvtools_timeout=10,
        reject_critical=True,
    )

    assert captured["project_bytes"] == materialization.project.bytes
    assert captured["project_sha256"] == materialization.project.sha256
    assert captured["effective_config_bytes"] == config_bytes
    assert captured["effective_config_sha256"] == materialization.spec.selected_effective_config_sha256
    assert captured["printer"] is printer
    assert result.materialized_project_sha256 == materialization.project.sha256
    assert result.artifact.native_bytes == b"plate ctb"
    manifest = selected_materialized_plate_native_manifest(result)
    assert manifest["schema"] == "workpiece-resin-selected-plate-native-v1"
    assert manifest["plate_index"] == 1
    assert manifest["selected_sliced_artifacts"]["effective_config_sha256"] == _sha(config_bytes)
    assert manifest["materialized_plate_slice"]["printer_native_sha256"] == _sha(b"plate ctb")


def test_selected_execution_rejects_wrong_effective_config_before_slice(tmp_path, monkeypatch):
    materialization = _materialization(b"exact selected effective config")
    called = False

    def forbidden_slice(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("generic slicer must not run after selected-config drift")

    monkeypatch.setattr(plate_execution, "slice_materialized_plate_native", forbidden_slice)
    with pytest.raises(SelectedPlateExecutionError, match="effective config bytes do not match"):
        execute_selected_materialized_plate_native(
            materialization,
            selected_effective_config_bytes=b"different config",
            printer=_printer(tmp_path),
            prusa_bin="prusaslicer",
            uvtools_cmd="UVtoolsCmd",
            slice_timeout=10,
            uvtools_timeout=10,
            reject_critical=True,
        )
    assert called is False


def test_selected_execution_rejects_printer_mismatch_before_slice(tmp_path, monkeypatch):
    config_bytes = b"exact selected effective config"
    materialization = _materialization(config_bytes)
    called = False

    def forbidden_slice(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("generic slicer must not run with a different printer")

    monkeypatch.setattr(plate_execution, "slice_materialized_plate_native", forbidden_slice)
    with pytest.raises(SelectedPlateExecutionError, match="Printer profile does not match"):
        execute_selected_materialized_plate_native(
            materialization,
            selected_effective_config_bytes=config_bytes,
            printer=_printer(tmp_path, profile_id="printer-b"),
            prusa_bin="prusaslicer",
            uvtools_cmd="UVtoolsCmd",
            slice_timeout=10,
            uvtools_timeout=10,
            reject_critical=True,
        )
    assert called is False


def test_selected_execution_rejects_materialized_project_hash_drift_before_slice(tmp_path, monkeypatch):
    config_bytes = b"exact selected effective config"
    materialization = _materialization(config_bytes)
    broken = SelectedPlateProjectMaterialization(
        spec=materialization.spec,
        display_placements=materialization.display_placements,
        project=Materialized3MFProject(
            bytes=b"tampered",
            sha256=materialization.project.sha256,
            instance_count=1,
            instance_indices=(1,),
            display_transforms=materialization.project.display_transforms,
        ),
    )
    called = False

    def forbidden_slice(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("generic slicer must not run after project drift")

    monkeypatch.setattr(plate_execution, "slice_materialized_plate_native", forbidden_slice)
    with pytest.raises(SelectedPlateExecutionError, match="project SHA-256"):
        execute_selected_materialized_plate_native(
            broken,
            selected_effective_config_bytes=config_bytes,
            printer=_printer(tmp_path),
            prusa_bin="prusaslicer",
            uvtools_cmd="UVtoolsCmd",
            slice_timeout=10,
            uvtools_timeout=10,
            reject_critical=True,
        )
    assert called is False
