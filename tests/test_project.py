"""`POST /v1/project` with the engine boundary mocked.

Real: STL validation, profile registry, proxy orientation screening, plate planning,
3MF build-item materialization, ZIP/JSON shape. Mocked: `slice_native` and UVtools text
output inside the orientation adapter, and the per-plate slicer.
"""
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.orientation_adapter as adapter
import app.project as project_module
from app.engine import NativeArtifact
from app.materialized_plate_slice import MaterializedPlateNativeArtifact
from app.uvtools_metrics import NativeArtifactMetrics, NativeBoundingRectangle
from tests.test_estimate import EFFECTIVE_CONFIG, PROFILES, box_stl
from app.prusa_3mf_instances import _to_matrix
from tests.test_prusa_3mf_instances import _project as review_3mf, _transforms

TUPLE = {"printer_profile": "elegoo-mars-2", "resin_profile": "elegoo-water-washable-grey", "quality": "balanced-0p05-medium"}
AUTH = {"Authorization": "Bearer test-token"}
REVIEW_3MF = review_3mf()


def _fake_engines(monkeypatch, *, footprint=(20.0, 20.0), islands=0, layers_per_plate=600, source_project=REVIEW_3MF, native_origin=(31.31, 55.28)):
    """Install fakes for the two external-engine calls; return the recorded plate slices."""
    width, depth = footprint
    plate_calls: list[dict] = []

    def fake_slice_native(source, **kwargs):
        summary = {"islands": islands, "overhangs": 0, "resin_traps": 0, "suction_cups": 0, "touching_bounds": 0, "empty_layers": 0}
        o = kwargs["orientation"]
        native = f"ctb-{o.x}-{o.y}-{o.z}".encode()
        return NativeArtifact(
            project_bytes=source_project, project_filename="r.3mf", project_sha256=hashlib.sha256(source_project).hexdigest(),
            intermediate_bytes=b"sl1", intermediate_filename="i.sl1", bytes=native, filename="part.ctb",
            media_type="application/octet-stream", source_sha256=hashlib.sha256(source).hexdigest(),
            intermediate_sha256=hashlib.sha256(b"sl1").hexdigest(), native_sha256=hashlib.sha256(native).hexdigest(),
            issue_summary=summary, issue_text=f"Issues: {islands}\n", printer_profile=kwargs["printer"].id,
            resin_profile=kwargs["resin"].id, quality_profile=kwargs["quality"].id, orientation=o,
            effective_config_bytes=EFFECTIVE_CONFIG, effective_config_filename="e.ini",
            effective_config_sha256=hashlib.sha256(EFFECTIVE_CONFIG).hexdigest(),
        )

    def fake_uvtools(command, *, timeout, action):
        if "-r" in command:
            return "# Layer: 0\nArea: 400\nVolume: 20\n# Layer: 1\nArea: 400\nVolume: 20\n"
        return f"LayerCount: 2\nPrintHeight: 30\nBoundingRectangleMillimeters: {{X={native_origin[0]},Y={native_origin[1]},Width={width},Height={depth}}}\n"

    def fake_plate_slice(**kwargs):
        plate_calls.append(kwargs)
        count = len(_transforms(kwargs["project_bytes"]))
        native = b"plate-ctb-" + kwargs["project_sha256"].encode()
        return MaterializedPlateNativeArtifact(
            project_bytes=kwargs["project_bytes"], project_sha256=kwargs["project_sha256"],
            effective_config_bytes=kwargs["effective_config_bytes"], effective_config_sha256=kwargs["effective_config_sha256"],
            intermediate_bytes=b"sl1", intermediate_sha256="i" * 64, native_bytes=native,
            native_sha256=hashlib.sha256(native).hexdigest(), intermediate_filename="p.sl1", native_filename="p.ctb",
            issue_summary={"islands": 0, "overhangs": 0, "resin_traps": 0, "suction_cups": 0, "touching_bounds": 0, "empty_layers": 0},
            issue_text="Issues: 0\n",
            native_metrics=NativeArtifactMetrics(
                layer_count=layers_per_plate, max_layer_area_mm2=400.0 * count, material_volume_mm3=9800.0 * count,
                footprint_area_mm2=width * depth * count, z_height_mm=30.0,
                bounding_rectangle=NativeBoundingRectangle(x_mm=3.0, y_mm=3.0, width_mm=width * count, height_mm=depth),
            ),
            printer_profile_id=kwargs["printer"].id,
        )

    monkeypatch.setattr(adapter, "slice_native", fake_slice_native)
    monkeypatch.setattr(adapter, "_run_uvtools_text", fake_uvtools)
    monkeypatch.setattr(project_module, "slice_materialized_plate_native", fake_plate_slice)
    return plate_calls


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "test-token")
    monkeypatch.setattr(main, "PROFILE_ROOT", PROFILES)
    return TestClient(main.app)


def post(client, stl=None, quantity=15, headers=AUTH, **fields):
    return client.post(
        "/v1/project",
        files={"file": ("part.stl", stl or box_stl(20, 20, 20), "model/stl")},
        data={**TUPLE, "requested_quantity": str(quantity), "finalist_limit": "2", **fields},
        headers=headers,
    )


def _unzip(response) -> tuple[dict[str, bytes], dict]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    return members, json.loads(members["project.json"])


def test_fifteen_small_parts_fill_one_plate(client, monkeypatch):
    plate_calls = _fake_engines(monkeypatch, footprint=(20.0, 20.0))
    response = post(client, quantity=15)
    assert response.status_code == 200, response.text
    assert response.headers["x-workpiece-plate-count"] == "1"
    assert response.headers["x-workpiece-instance-count"] == "15"
    assert response.headers["content-disposition"] == 'attachment; filename="part-workpiece-resin-project.zip"'

    members, manifest = _unzip(response)
    assert set(members) == {"part-plate-01.3mf", "part-plate-01.ctb", "part-plate-01-uvtools-issues.txt", "part-effective.ini", "project.json"}
    assert manifest["schema"] == "workpiece-resin-project-v1"
    assert manifest["layout"] == {
        **manifest["layout"],
        "columns": 3, "rows": 5, "capacity_per_plate": 15, "plate_count": 1, "rotation_z_deg": 0,
        "spacing_mm": 5.0, "edge_margin_mm": 3.0,
        "bed_mm": {"width": 82.62, "depth": 130.56},
        "envelope_mm": {"width": 80.0, "depth": 129.0, "offset_x": 1.31, "offset_y": 0.78},
    }
    plate = manifest["plates"][0]
    assert plate["instance_count"] == 15 and len(plate["instances"]) == 15
    assert [i["instance_index"] for i in plate["instances"]] == list(range(1, 16))
    # first slot: margin 3 + half footprint 10, shifted by the envelope offset into bed coordinates
    assert plate["instances"][0] == {"instance_index": 1, "x_mm": 14.31, "y_mm": 13.78, "rotation_z_deg": 0}
    assert plate["instances"][14]["x_mm"] == pytest.approx(14.31 + 2 * 25)
    assert plate["instances"][14]["y_mm"] == pytest.approx(13.78 + 4 * 25)
    assert all(3 <= i["x_mm"] - 10 and i["x_mm"] + 10 <= 82.62 - 3 for i in plate["instances"])

    # the plate 3MF really has 15 build items, and the plate slicer was fed exactly that project + the winner's config
    assert len(_transforms(members["part-plate-01.3mf"])) == 15
    assert len(plate_calls) == 1
    assert plate_calls[0]["project_bytes"] == members["part-plate-01.3mf"]
    assert plate_calls[0]["effective_config_bytes"] == EFFECTIVE_CONFIG == members["part-effective.ini"]
    assert plate_calls[0]["reject_critical"] is False

    # numbers: same cycle formula as /v1/estimate (5 bottom layers at 30 s, rest at 2.75 s, + motion 5.6786 s)
    motion = 5 * 60 / 80 + 5 * 60 / 210 + 0.5
    assert plate["layer_count"] == 600
    assert plate["print_time_seconds"] == pytest.approx(5 * (30 + motion) + 595 * (2.75 + motion), abs=0.1)
    assert plate["resin_ml"] == pytest.approx(9.8 * 15)
    assert manifest["totals"] == {"instances": 15, "plates": 1, "print_time_seconds": plate["print_time_seconds"], "resin_ml": plate["resin_ml"]}
    assert manifest["orientation"]["selection"] == "sliced-winner"
    assert manifest["orientation"]["finalists_sliced"] == 2
    assert manifest["part"]["supported_footprint_mm"] == {"width": 20.0, "depth": 20.0}
    assert manifest["part"]["model_volume_ml"] == 8.0
    assert manifest["engine"]["prusaslicer"] == "2.9.7" and manifest["engine"]["uvtools"] == "7.1.2"
    assert manifest["profile"]["normalExposureS"] == 2.75 and manifest["profile"]["initialLayers"] == 5
    assert manifest["source"] == {"filename": "part.stl", "sha256": hashlib.sha256(box_stl(20, 20, 20)).hexdigest(), "requested_quantity": 15}

    # every hash in project.json matches the member it names
    for record in (*(plate["files"].values()), manifest["files"]["effective_config"]):
        assert hashlib.sha256(members[record["name"]]).hexdigest() == record["sha256"]
        assert record["bytes"] == len(members[record["name"]])
    assert not any("critical" in w for w in manifest["warnings"])


def test_fifteen_large_parts_need_fifteen_plates(client, monkeypatch):
    plate_calls = _fake_engines(monkeypatch, footprint=(62.0, 102.0))
    response = post(client, stl=box_stl(60, 100, 20), quantity=15)
    assert response.status_code == 200, response.text
    members, manifest = _unzip(response)
    assert manifest["layout"]["capacity_per_plate"] == 1
    assert manifest["layout"]["rotation_z_deg"] == 0
    assert manifest["totals"] == {**manifest["totals"], "instances": 15, "plates": 15}
    assert len(plate_calls) == 15
    assert [p["plate_index"] for p in manifest["plates"]] == list(range(1, 16))
    assert [p["instances"][0]["instance_index"] for p in manifest["plates"]] == list(range(1, 16))
    assert sorted(n for n in members if n.endswith(".3mf")) == [f"part-plate-{i:02d}.3mf" for i in range(1, 16)]
    assert all(len(_transforms(members[f"part-plate-{i:02d}.3mf"])) == 1 for i in range(1, 16))


def test_part_that_only_fits_rotated_is_placed_rotated(client, monkeypatch):
    _fake_engines(monkeypatch, footprint=(100.0, 70.0))  # 100 wide does not fit 80; rotated 70 x 100 does
    response = post(client, stl=box_stl(98, 68, 20), quantity=2)
    assert response.status_code == 200, response.text
    members, manifest = _unzip(response)
    assert manifest["layout"]["rotation_z_deg"] == 90
    assert manifest["layout"]["capacity_per_plate"] == 1
    assert manifest["totals"]["plates"] == 2
    instance = manifest["plates"][0]["instances"][0]
    assert instance["rotation_z_deg"] == 90
    # rotated footprint 70 x 100 centred at margin + half: (3 + 35, 3 + 50) plus the envelope offset
    assert instance["x_mm"] == pytest.approx(38 + 1.31) and instance["y_mm"] == pytest.approx(53 + 0.78)
    # the 3MF transform rotates +90 about the source footprint centre (bed centre), then moves it to the slot
    (transform,) = _transforms(members["part-plate-01.3mf"])
    assert transform[:2] == (0.0, 1.0) and transform[3:5] == (-1.0, 0.0)


def test_part_too_large_for_the_plate_is_refused(client, monkeypatch):
    _fake_engines(monkeypatch, footprint=(90.0, 140.0))
    response = post(client, quantity=1)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DOES_NOT_FIT"


def test_all_blocked_finalists_use_the_least_critical_one_with_warning(client, monkeypatch):
    _fake_engines(monkeypatch, islands=1)
    response = post(client, quantity=1)
    assert response.status_code == 200, response.text
    _, manifest = _unzip(response)
    assert manifest["orientation"]["selection"] == "sliced-least-critical"
    assert any("the one with the fewest" in w for w in manifest["warnings"])


def test_auth_and_input_validation(client, monkeypatch):
    _fake_engines(monkeypatch)
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.setattr(main, "PROJECT_TOKEN", "")
    assert post(client).status_code == 503
    monkeypatch.setattr(main, "PROJECT_TOKEN", "test-token")

    for quantity in (0, 101):
        response = post(client, quantity=quantity)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_QUANTITY"
    response = post(client, finalist_limit="9")
    assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_FINALIST_LIMIT"
    response = post(client, resin_profile="unknown-resin")
    assert response.status_code == 422 and response.json()["detail"]["code"] == "UNSUPPORTED_PROFILE"
    response = client.post("/v1/project", files={"file": ("part.obj", b"x", "model/obj")}, data=TUPLE, headers=AUTH)
    assert response.status_code == 400 and response.json()["detail"]["code"] == "INVALID_STL"
    response = post(client, stl=b"not an stl at all")
    assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_STL"


def test_health_reports_project_limits():
    payload = main.health()
    assert payload["resource_limits"]["max_project_quantity"] == main.MAX_PROJECT_QUANTITY
    assert "toolchain" not in payload["engines"]


TOOLCHAIN_PRESENT = all(shutil.which(p) or Path(p).is_file() for p in (main.PRUSA_BIN, main.UVTOOLS_CMD))


@pytest.mark.skipif(
    not (os.environ.get("WORKPIECE_RESIN_INTEGRATION") == "1" and TOOLCHAIN_PRESENT),
    reason="opt-in: set WORKPIECE_RESIN_INTEGRATION=1 with the pinned PrusaSlicer/UVtools toolchain installed",
)
def test_project_real_two_plate_run_of_20mm_cubes(client):
    """Real toolchain: 20 of a 20 mm cube. With pad/supports (~24 mm footprint) 8 fit per plate -> 3 plates."""
    response = post(client, quantity=20, finalist_limit="2")
    assert response.status_code == 200, response.text
    members, manifest = _unzip(response)
    assert manifest["totals"]["instances"] == 20
    assert 2 <= manifest["totals"]["plates"] <= 4
    for plate in manifest["plates"]:
        assert plate["layer_count"] >= 500
        assert members[plate["files"]["project_3mf"]["name"]][:2] == b"PK"
        assert len(members[plate["files"]["printer_native"]["name"]]) > 1000
        observed = plate["observed_footprint_mm"]
        assert observed["width"] <= 82.62 and observed["depth"] <= 130.56


# Real numbers measured on the host (2026-11-12, Mars 2, 40 x 30 x 20 mm box standing on its 40 x 20 face): the retained 3MF
# item sits at (21.31, 50.28), NOT at the bed centre (41.31, 65.28), and UVtools measured the cured footprint at
# x 38.5, y 66.25, 44.12 x 26.83 in raster millimetres. The old code assumed the footprint was centred on the bed and
# placed every instance 20 mm / 15 mm off, outside the bed, so PrusaSlicer printed "Nothing to print".
BED = (82.62, 130.56)


def transform_matrix(values):
    return _to_matrix(values)
MEASURED_PROJECT = review_3mf(transform="1 0 0 0 1 0 0 0 1 21.31 50.28 15")


def test_native_rectangle_is_converted_to_bed_millimetres_with_the_measured_mirror():
    rectangle = NativeBoundingRectangle(x_mm=38.5, y_mm=66.25, width_mm=44.12, height_mm=26.83)
    envelope = project_module.bed_envelope_from_native_rectangle(rectangle, bed_width_mm=BED[0], bed_depth_mm=BED[1], mirror_x=True, mirror_y=False)
    assert (envelope.min_x_mm, envelope.max_x_mm, envelope.min_y_mm, envelope.max_y_mm) == (0.0, 44.12, 37.48, 64.31)
    for mirror_x, mirror_y in ((False, False), (True, True), (False, True)):
        with pytest.raises(project_module.ProjectError, match="only measured"):
            project_module.bed_envelope_from_native_rectangle(rectangle, bed_width_mm=BED[0], bed_depth_mm=BED[1], mirror_x=mirror_x, mirror_y=mirror_y)


@pytest.mark.parametrize("quantity", [1, 6, 15])
def test_every_instance_footprint_lies_inside_the_bed_when_the_source_part_is_off_centre(client, monkeypatch, quantity):
    plate_calls = _fake_engines(monkeypatch, footprint=(44.12, 26.83), source_project=MEASURED_PROJECT, native_origin=(38.5, 66.25))
    response = post(client, quantity=quantity)
    assert response.status_code == 200, response.text
    # Where the cured footprint's centre sits in the source object's own frame: raster rectangle -> bed millimetres, minus the item translation.
    local_x, local_y = (BED[0] - (38.5 + 44.12 / 2)) - 21.31, (BED[1] - (66.25 + 26.83 / 2)) - 50.28
    seen = 0
    for call in plate_calls:
        for transform in _transforms(call["project_bytes"]):
            matrix = transform_matrix(transform)
            # the footprint centre after the materialized transform (this is the planned target)
            centre_x = matrix[0][0] * local_x + matrix[0][1] * local_y + matrix[0][3]
            centre_y = matrix[1][0] * local_x + matrix[1][1] * local_y + matrix[1][3]
            rotated = abs(matrix[0][0]) < 1e-9
            width, depth = (26.83, 44.12) if rotated else (44.12, 26.83)
            assert width / 2 - 1e-6 <= centre_x <= BED[0] - width / 2 + 1e-6, (transform, centre_x)
            assert depth / 2 - 1e-6 <= centre_y <= BED[1] - depth / 2 + 1e-6, (transform, centre_y)
            seen += 1
    assert seen == quantity


def test_a_plate_with_a_footprint_outside_the_bed_is_refused_with_the_numbers():
    placements = (project_module.DisplayInstancePlacement(instance_index=1, target_display_x_mm=6.37, target_display_y_mm=2.195, rotation_z_deg=0),)
    rectangle = NativeBoundingRectangle(x_mm=38.5, y_mm=66.25, width_mm=44.12, height_mm=26.83)
    prepared = type("P", (), {"bed_width_mm": BED[0], "bed_depth_mm": BED[1]})()
    with pytest.raises(project_module.ProjectError, match=r"MATERIALIZE|outside the 82.62 x 130.56 mm bed") as caught:
        project_module._require_footprints_inside_bed(1, placements, rectangle, prepared)
    assert caught.value.code == "MATERIALIZE_FAILED" and "x -15.69..28.43" in str(caught.value)
