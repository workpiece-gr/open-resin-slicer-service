import hashlib
from pathlib import Path

import pytest

import app.orientation_adapter as adapter
from app.engine import NativeArtifact, Orientation
from app.orientation_candidates import OrientationSpec
from app.orientation_execute import SlicedFinalistExecutionError
from app.orientation_pipeline import ProxyOrientationPlan
from app.orientation_proxy import GeometryProxyMetrics
from app.orientation_screen import ProxyCandidate, screen_geometry_proxies
from app.profiles import Profile


SOURCE = b"exact source stl bytes for real adapter"
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()


def _proxy_metrics(area: float, moment: float, height: float) -> GeometryProxyMetrics:
    return GeometryProxyMetrics(
        triangle_count=12,
        sampled_layer_count=10,
        full_layer_count=10,
        layer_sampling_stride=1,
        max_sampled_layer_area_mm2=area,
        z_height_mm=height,
        xy_width_mm=20,
        xy_depth_mm=20,
        downward_projected_area_mm2=10,
        downward_support_moment_mm3=moment,
        open_contour_sample_count=0,
    )


def _plan() -> ProxyOrientationPlan:
    candidates = (
        ProxyCandidate(OrientationSpec(0, 0, 0), _proxy_metrics(300, 900, 20)),
        ProxyCandidate(OrientationSpec(15, 0, 0), _proxy_metrics(220, 700, 24)),
    )
    return ProxyOrientationPlan(
        source_sha256=SOURCE_SHA,
        triangle_count=12,
        candidates=candidates,
        screening=screen_geometry_proxies(candidates, finalist_limit=2),
    )


def _profile(pid: str, kind: str) -> Profile:
    return Profile(
        id=pid,
        kind=kind,
        label=pid,
        candidate_ready=True,
        production_ready=False,
        config=Path(f"/profiles/{pid}.ini"),
        metadata={},
    )


PRINTER = _profile("elegoo-mars-2", "printer")
RESIN = _profile("elegoo-water-washable-grey", "resin")
QUALITY = _profile("balanced-0p05-medium", "quality")


def _artifact(
    orientation: Orientation,
    *,
    islands: int = 0,
    bad_project_hash: bool = False,
) -> NativeArtifact:
    token = "a" if float(orientation.x) == 0 else "b"
    project = f"project-{token}".encode()
    config = f"config-{token}".encode()
    intermediate = f"sl1-{token}".encode()
    native = f"ctb-{token}".encode()
    return NativeArtifact(
        project_bytes=project,
        project_filename=f"{token}-review.3mf",
        project_sha256="0" * 64 if bad_project_hash else hashlib.sha256(project).hexdigest(),
        intermediate_bytes=intermediate,
        intermediate_filename=f"{token}.sl1",
        bytes=native,
        filename=f"{token}.ctb",
        media_type="application/octet-stream",
        source_sha256=SOURCE_SHA,
        intermediate_sha256=hashlib.sha256(intermediate).hexdigest(),
        native_sha256=hashlib.sha256(native).hexdigest(),
        issue_summary={
            "islands": islands,
            "overhangs": 0,
            "resin_traps": 0,
            "suction_cups": 0,
            "touching_bounds": 0,
            "empty_layers": 0,
        },
        issue_text=f"Issues: {islands}\n",
        printer_profile=PRINTER.id,
        resin_profile=RESIN.id,
        quality_profile=QUALITY.id,
        orientation=orientation,
        effective_config_bytes=config,
        effective_config_filename=f"{token}.ini",
        effective_config_sha256=hashlib.sha256(config).hexdigest(),
    )


def _install_fakes(
    monkeypatch,
    *,
    first_islands: int = 0,
    all_islands: int | None = None,
    bad_project_hash: bool = False,
    malformed_metrics: bool = False,
):
    slice_calls = []
    uvtools_calls = []

    def fake_slice_native(source, **kwargs):
        assert source == SOURCE
        orientation = kwargs["orientation"]
        key = OrientationSpec(orientation.x, orientation.y, orientation.z).canonical_key
        slice_calls.append((key, kwargs["reject_critical"]))
        if all_islands is not None:
            islands = all_islands
        else:
            islands = first_islands if float(orientation.x) == 0 else 0
        return _artifact(
            orientation,
            islands=islands,
            bad_project_hash=bad_project_hash,
        )

    def fake_uvtools(command, *, timeout, action):
        uvtools_calls.append((tuple(command), timeout, action))
        native_path = next(item for item in command if "printer-native" in item)
        second = "finalist-002" in native_path
        if malformed_metrics:
            return "LayerCount: not-an-integer\n"
        if "-r" not in command:
            if second:
                return (
                    "LayerCount: 2\n"
                    "PrintHeight: 10\n"
                    "BoundingRectangleMillimeters: {X=3,Y=4,Width=10,Height=10}\n"
                )
            return (
                "LayerCount: 2\n"
                "PrintHeight: 20\n"
                "BoundingRectangleMillimeters: {X=7,Y=8,Width=20,Height=20}\n"
            )
        if second:
            return (
                "# Layer: 0\nArea: 100\nVolume: 5\n"
                "# Layer: 1\nArea: 80\nVolume: 5\n"
            )
        return (
            "# Layer: 0\nArea: 200\nVolume: 10\n"
            "# Layer: 1\nArea: 150\nVolume: 10\n"
        )

    monkeypatch.setattr(adapter, "slice_native", fake_slice_native)
    monkeypatch.setattr(adapter, "_run_uvtools_text", fake_uvtools)
    return slice_calls, uvtools_calls


def _execute():
    return adapter.execute_real_sliced_finalists(
        proxy_plan=_plan(),
        source_stl=SOURCE,
        original_name="part.stl",
        printer=PRINTER,
        resin=RESIN,
        quality=QUALITY,
        prusa_bin="/opt/prusa",
        uvtools_cmd="/opt/uvtools",
        slice_timeout=240,
        uvtools_timeout=120,
    )


def test_real_adapter_executes_finalists_sequentially_and_retains_only_exact_winner(monkeypatch):
    slice_calls, uvtools_calls = _install_fakes(monkeypatch)
    result = _execute()

    expected = [item.candidate.spec.canonical_key for item in _plan().screening.finalists]
    expected_winner = expected[1]
    expected_token = "a" if expected_winner[0] == 0 else "b"
    assert [item[0] for item in slice_calls] == expected
    assert all(reject_critical is False for _, reject_critical in slice_calls)
    assert result.executed_finalist_count == 2
    assert result.validation.selected_evidence is not None
    assert result.validation.selected_evidence.canonical_key == expected_winner
    assert result.selected_result is not None
    assert result.selected_native_envelope is not None

    selected = result.selected_result.artifact
    envelope = result.selected_native_envelope
    assert selected.orientation.x == expected_winner[0]
    assert selected.project_bytes == f"project-{expected_token}".encode()
    assert selected.effective_config_bytes == f"config-{expected_token}".encode()
    assert selected.intermediate_bytes == f"sl1-{expected_token}".encode()
    assert selected.bytes == f"ctb-{expected_token}".encode()
    assert hashlib.sha256(selected.project_bytes).hexdigest() == selected.project_sha256
    assert hashlib.sha256(selected.bytes).hexdigest() == selected.native_sha256
    assert envelope.printer_native_sha256 == selected.native_sha256
    assert envelope.printer_profile_id == PRINTER.id
    assert envelope.coordinate_space == "uvtools-native-display-millimetres"
    assert envelope.envelope.min_x_mm == 3
    assert envelope.envelope.max_x_mm == 13
    assert envelope.envelope.min_y_mm == 4
    assert envelope.envelope.max_y_mm == 14
    assert envelope.automatic_materialization_authority is False

    layer_commands = [command for command, _, _ in uvtools_calls if "-r" in command]
    assert len(layer_commands) == 2
    assert all("0:1" in command for command in layer_commands)


def test_critical_issue_on_one_finalist_does_not_abort_other_real_slices(monkeypatch):
    slice_calls, _ = _install_fakes(monkeypatch, first_islands=1)
    result = _execute()
    assert len(slice_calls) == 2
    assert result.selected_result is not None
    assert result.selected_result.artifact.orientation.x == 15
    assert result.selected_native_envelope is not None
    assert result.selected_native_envelope.printer_native_sha256 == result.selected_result.artifact.native_sha256
    blocked = next(
        item
        for item in result.validation.decision.ranked
        if item.candidate.canonical_key == (0.0, 0.0, 0.0)
    )
    assert "unresolved-islands" in blocked.blocked_reasons


def test_all_blocked_finalists_return_manual_review_without_retaining_heavy_artifacts(monkeypatch):
    slice_calls, _ = _install_fakes(monkeypatch, all_islands=1)
    result = _execute()
    assert len(slice_calls) == 2
    assert result.validation.decision.manual_review_required is True
    assert result.selected_result is None
    assert result.selected_native_envelope is None
    assert result.executed_finalist_count == 2


def test_native_metric_format_change_fails_closed(monkeypatch):
    _install_fakes(monkeypatch, malformed_metrics=True)
    with pytest.raises(adapter.SlicedFinalistAdapterError, match="metrics were incomplete or unparseable"):
        _execute()


def test_spool_rejects_artifact_bytes_that_do_not_match_recorded_hash(monkeypatch):
    _install_fakes(monkeypatch, bad_project_hash=True)
    with pytest.raises(adapter.SlicedFinalistAdapterError, match="review 3MF bytes do not match"):
        _execute()


def test_source_mismatch_is_rejected_before_real_slicer_runs(monkeypatch):
    slice_calls, _ = _install_fakes(monkeypatch)
    with pytest.raises(SlicedFinalistExecutionError, match="source-bound proxy"):
        adapter.execute_real_sliced_finalists(
            proxy_plan=_plan(),
            source_stl=b"different source",
            original_name="part.stl",
            printer=PRINTER,
            resin=RESIN,
            quality=QUALITY,
            prusa_bin="/opt/prusa",
            uvtools_cmd="/opt/uvtools",
            slice_timeout=240,
            uvtools_timeout=120,
        )
    assert slice_calls == []
