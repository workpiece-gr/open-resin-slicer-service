import asyncio
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.engine import EngineError, NativeArtifact, Orientation
from app.estimate import mesh_stats
from app.uvtools_metrics import NativeArtifactMetrics, NativeBoundingRectangle

PROFILES = Path(__file__).resolve().parents[1] / "profiles"
TUPLE = {"printer_profile": "elegoo-mars-2", "resin_profile": "elegoo-water-washable-grey", "quality": "balanced-0p05-medium"}
AUTH = {"Authorization": "Bearer test-token"}
EFFECTIVE_CONFIG = b"layer_height = 0.05\nexposure_time = 2.75\ninitial_exposure_time = 30\nfaded_layers = 5\n"
MOTION_S = 5 * 60 / 80 + 5 * 60 / 210 + 0.5  # lift 3.75 s + retract 1.4286 s + rest 0.5 s


def box_stl(x: float, y: float, z: float) -> bytes:
    """Closed binary STL box with outward-facing triangles."""
    v = [(i & 1 and x, i & 2 and y, i & 4 and z) for i in range(8)]
    v = [tuple(float(c) for c in p) for p in v]
    faces = [
        (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6),  # bottom, top
        (0, 1, 5), (0, 5, 4), (2, 6, 7), (2, 7, 3),  # front, back
        (0, 4, 6), (0, 6, 2), (1, 3, 7), (1, 7, 5),  # left, right
    ]
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0) for a, b, c in faces)
    return b"\0" * 80 + struct.pack("<I", len(faces)) + body


def artifact(**issues) -> NativeArtifact:
    summary = {"islands": 0, "overhangs": 0, "resin_traps": 0, "suction_cups": 0, "touching_bounds": 0, "empty_layers": 0, **issues}
    return NativeArtifact(
        project_bytes=b"3mf", project_filename="r.3mf", project_sha256="p" * 64,
        intermediate_bytes=b"sl1", intermediate_filename="i.sl1", bytes=b"ctb", filename="part-workpiece.ctb",
        media_type="application/octet-stream", source_sha256="s" * 64, intermediate_sha256="i" * 64,
        native_sha256="n" * 64, issue_summary=summary, issue_text="Issues: 0", printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey", quality_profile="balanced-0p05-medium",
        orientation=Orientation(), effective_config_bytes=EFFECTIVE_CONFIG, effective_config_filename="e.ini",
        effective_config_sha256="e" * 64,
    )


def metrics(layer_count=600, volume_mm3=9800.0, width=25.0, depth=25.0, z=30.0) -> NativeArtifactMetrics:
    return NativeArtifactMetrics(
        layer_count=layer_count, max_layer_area_mm2=400.0, material_volume_mm3=volume_mm3,
        footprint_area_mm2=width * depth, z_height_mm=z,
        bounding_rectangle=NativeBoundingRectangle(x_mm=20.0, y_mm=50.0, width_mm=width, height_mm=depth),
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "test-token")
    monkeypatch.setattr(main, "PROFILE_ROOT", PROFILES)
    monkeypatch.setattr(main, "SLICE_CAPACITY", asyncio.Semaphore(1))
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: artifact())
    monkeypatch.setattr(main, "measure_native", lambda *a, **k: metrics())
    return TestClient(main.app)


def post(client, stl=None, headers=AUTH, name="cube.stl", **form):
    return client.post("/v1/estimate", files={"file": (name, stl or box_stl(20, 20, 20), "model/stl")}, data={**TUPLE, **form}, headers=headers)


def test_mesh_stats_measures_volume_and_oriented_bounding_box():
    stats = mesh_stats(box_stl(10, 20, 30), Orientation())
    assert stats.dims_mm == (10.0, 20.0, 30.0)
    assert stats.volume_mm3 == 6000.0
    rotated = mesh_stats(box_stl(10, 20, 30), Orientation(90, 0, 0))
    assert rotated.dims_mm == (10.0, 30.0, 20.0)
    assert rotated.volume_mm3 == 6000.0
    assert mesh_stats(box_stl(10, 20, 30), Orientation(0, 0, 90)).dims_mm == (20.0, 10.0, 30.0)


def test_estimate_requires_bearer_token(client):
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_estimate_rejects_unsupported_profile_tuple(client):
    response = post(client, resin_profile="unknown-resin")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNSUPPORTED_PROFILE"


def test_estimate_rejects_invalid_stl_before_slicing(client, monkeypatch):
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: pytest.fail("must not slice"))
    assert post(client, stl=b"not an stl at all, really").json()["detail"]["code"] == "INVALID_STL"
    assert post(client, name="cube.obj").json()["detail"]["code"] == "INVALID_STL"
    assert post(client, rotate_x="400").json()["detail"]["code"] == "INVALID_ORIENTATION"


def test_estimate_refuses_models_outside_the_envelope_before_slicing(client, monkeypatch):
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: pytest.fail("must not slice"))
    response = post(client, stl=box_stl(20, 20, 151))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "OUTSIDE_ENVELOPE"
    # A 100 mm part fits only along the 129 mm depth axis: orientation decides.
    assert post(client, stl=box_stl(100, 20, 20)).json()["detail"]["code"] == "OUTSIDE_ENVELOPE"
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: artifact())
    assert post(client, stl=box_stl(100, 20, 20), rotate_z="90").status_code == 200


def test_estimate_response_shape_and_time_formula(client):
    response = post(client, rotate_x="0", rotate_y="0", rotate_z="0")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "workpiece-resin-estimate-v1"
    assert body["engine"]["prusaslicer"] == "2.9.7" and body["engine"]["uvtools"] == "7.1.2"
    assert body["profile"] == {
        "printer": "elegoo-mars-2", "resin": "elegoo-water-washable-grey", "quality": "balanced-0p05-medium",
        "layerHeightMm": 0.05, "normalExposureS": 2.75, "initialExposureS": 30.0, "initialLayers": 5,
        "liftMm": 5.0, "liftSpeedMmMin": 80.0, "retractSpeedMmMin": 210.0, "restAfterRetractS": 0.5,
    }
    summary = body["summary"]
    assert summary["layer_count"] == 600 and summary["bottom_layer_count"] == 5
    expected = 5 * (30 + MOTION_S) + 595 * (2.75 + MOTION_S)
    assert summary["print_time_seconds"] == pytest.approx(expected, abs=0.1)
    assert summary["model_volume_ml"] == 8.0
    assert summary["total_resin_ml"] == 9.8
    assert summary["support_volume_ml"] == pytest.approx(1.8)
    assert summary["bounding_box_mm"] == [20.0, 20.0, 20.0]
    assert summary["print_footprint_mm"] == {"width": 25.0, "depth": 25.0, "height": 30.0}
    assert summary["fits_envelope"] is True and summary["orientation_deg"] == [0.0, 0.0, 0.0]
    assert body["issues"] == {"islands": 0, "overhangs": 0, "resin_traps": 0, "suction_cups": 0, "touching_bounds": 0, "empty_layers": 0}
    assert body["hashes"]["source_sha256"] == "s" * 64
    assert body["time_breakdown"]["normal_layer_cycle_s"] == pytest.approx(2.75 + MOTION_S, abs=1e-3)
    for banned in ("zip", "3mf", "ctb"):
        assert banned not in str(body["hashes"].keys()) and "bundle" not in body
    assert response.headers["cache-control"] == "private, no-store"


def test_estimate_flags_oversized_supported_print_and_critical_issues(client, monkeypatch):
    monkeypatch.setattr(main, "measure_native", lambda *a, **k: metrics(z=151.0))
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: artifact(islands=2))
    body = post(client).json()
    assert body["summary"]["fits_envelope"] is False
    assert body["issues"]["islands"] == 2
    assert any("manual review" in w for w in body["warnings"])


def test_estimate_maps_timeout_failure_and_busy(client, monkeypatch):
    def timed_out(*a, **k):
        try:
            raise subprocess.TimeoutExpired(["prusa-slicer"], 240)
        except subprocess.TimeoutExpired as exc:
            raise EngineError("External resin engine timed out after 240 seconds.") from exc

    monkeypatch.setattr(main, "slice_native", timed_out)
    response = post(client)
    assert response.status_code == 504 and response.json()["detail"]["code"] == "SLICE_TIMEOUT"

    monkeypatch.setattr(main, "slice_native", lambda *a, **k: (_ for _ in ()).throw(EngineError("PrusaSlicer SLA export failed")))
    response = post(client)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "SLICE_FAILED"

    monkeypatch.setattr(main, "SLICE_CAPACITY", asyncio.Semaphore(0))
    monkeypatch.setattr(main, "ESTIMATE_QUEUE_TIMEOUT", 0.01)
    response = post(client)
    assert response.status_code == 429 and response.json()["detail"]["code"] == "BUSY"
    assert response.headers["retry-after"] == "30"


TOOLCHAIN_PRESENT = all(shutil.which(p) or Path(p).is_file() for p in (main.PRUSA_BIN, main.UVTOOLS_CMD))


@pytest.mark.skipif(
    not (os.environ.get("WORKPIECE_RESIN_INTEGRATION") == "1" and TOOLCHAIN_PRESENT),
    reason="opt-in: set WORKPIECE_RESIN_INTEGRATION=1 with the pinned PrusaSlicer/UVtools toolchain installed",
)
def test_estimate_real_slice_of_20mm_cube(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "test-token")
    monkeypatch.setattr(main, "PROFILE_ROOT", PROFILES)
    response = post(TestClient(main.app))
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    # 20 mm cube + 5 mm support elevation (+ pad) at 0.05 mm: ~500 layers plus elevation/pad layers.
    assert 500 <= summary["layer_count"] <= 700
    assert 8.0 <= summary["total_resin_ml"] <= 14.0
    assert summary["model_volume_ml"] == 8.0
    assert summary["fits_envelope"] is True


# ---- plate plan in /v1/estimate (requested_quantity) and orientation=auto -------------------------------------------------
# FAILURE MODE (seen 2026-11-12): the quote said 3 plates (its own heuristic) while the project build made 4 plates for the
# same 15 parts, so the quote under-priced the print time. The estimate now reports the plan from the planner the build uses.

def test_estimate_without_a_quantity_has_no_plate_plan(client):
    assert "plate_plan" not in post(client).json()


def test_estimate_with_a_quantity_reports_the_build_planners_plate_plan(client, monkeypatch):
    # the real measurement of the 40 x 30 x 20 box standing on its 40 x 20 face: supported footprint 44.12 x 26.83 mm
    monkeypatch.setattr(main, "measure_native", lambda *a, **k: metrics(width=44.12, depth=26.83, z=30.0))
    body = post(client, requested_quantity="15").json()
    plan = body["plate_plan"]
    assert plan["status"] == "ok" and plan["quantity"] == 15
    assert plan["parts_per_plate"] == 4 and plan["plate_count"] == 4
    assert [plate["instances"] for plate in plan["plates"]] == [4, 4, 4, 3]     # exactly what the real project build produced
    assert plan["spacing_mm"] == 5.0 and plan["edge_margin_mm"] == 3.0
    assert plan["plate_print_seconds"] == body["summary"]["print_time_seconds"]
    assert plan["total_print_seconds"] == pytest.approx(4 * body["summary"]["print_time_seconds"], abs=0.2)
    # and it IS the planner's own answer, not a copy of its rules
    from app.profiles import ProfileRegistry
    from app.project import plan_order_plates
    envelope = ProfileRegistry(PROFILES).printer_manufacturing_envelope("elegoo-mars-2")
    assert plan_order_plates(printer_id="elegoo-mars-2", envelope=envelope, footprint_width_mm=44.12, footprint_depth_mm=26.83, quantity=15).plate_count == plan["plate_count"]


def test_estimate_plate_plan_reports_a_footprint_that_does_not_fit(client, monkeypatch):
    monkeypatch.setattr(main, "measure_native", lambda *a, **k: metrics(width=200.0, depth=25.0, z=30.0))
    plan = post(client, requested_quantity="3").json()["plate_plan"]
    assert plan["status"] == "does_not_fit" and "plate_count" not in plan


def test_estimate_rejects_a_bad_quantity_or_orientation_mode_before_slicing(client, monkeypatch):
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: pytest.fail("must not slice"))
    for form in ({"requested_quantity": "0"}, {"requested_quantity": "100000"}, {"orientation": "sideways"}, {"orientation": "auto", "rotate_x": "90"}):
        response = post(client, **form)
        assert response.status_code in (422,), form
    assert post(client, requested_quantity="0").json()["detail"]["code"] == "INVALID_QUANTITY"
    assert post(client, orientation="auto", rotate_x="90").json()["detail"]["code"] == "INVALID_ORIENTATION"


def test_orientation_auto_measures_in_the_proxy_best_orientation(client, monkeypatch):
    from types import SimpleNamespace
    seen = []
    spec = SimpleNamespace(x_deg=90.0, y_deg=0.0, z_deg=0.0)
    plan = SimpleNamespace(screening=SimpleNamespace(finalists=(SimpleNamespace(candidate=SimpleNamespace(spec=spec)),)))
    asked = []
    monkeypatch.setattr(main, "build_proxy_orientation_plan", lambda data, finalist_limit: asked.append(finalist_limit) or plan)
    monkeypatch.setattr(main, "slice_native", lambda *a, **k: seen.append(k["orientation"]) or artifact())
    body = post(client, orientation="auto", requested_quantity="2").json()
    assert asked == [1] and (seen[0].x, seen[0].y, seen[0].z) == (90.0, 0.0, 0.0)
    assert body["summary"]["orientation_deg"] == [90.0, 0.0, 0.0] and body["plate_plan"]["status"] == "ok"


def test_orientation_auto_without_a_finalist_measures_as_uploaded_and_says_so(client, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(main, "build_proxy_orientation_plan", lambda data, finalist_limit: SimpleNamespace(screening=SimpleNamespace(finalists=())))
    body = post(client, orientation="auto").json()
    assert body["summary"]["orientation_deg"] == [0.0, 0.0, 0.0]
    assert any("measured as uploaded" in warning for warning in body["warnings"])
