from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.toolchain import ToolchainProvenanceError


VALID_TOOLCHAIN = "ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:" + "a" * 64
SOURCE_SHA = "b" * 64
SELECTED_NATIVE_SHA = "c" * 64


def _client(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "test-token")

    async def acquire_capacity(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "_acquire_capacity", acquire_capacity)
    return TestClient(main.app)


def test_project_endpoint_executes_selected_production_pipeline(monkeypatch):
    client = _client(monkeypatch)
    registry = object()
    captured = {}

    monkeypatch.setattr(main, "resolve_toolchain_ref", lambda **kwargs: VALID_TOOLCHAIN)
    monkeypatch.setattr(main, "validate_stl_bytes", lambda data: {"triangles": 12})
    monkeypatch.setattr(main, "_registry_snapshot", lambda: registry)

    result = SimpleNamespace(
        source_sha256=SOURCE_SHA,
        toolchain_image_ref=VALID_TOOLCHAIN,
        plates=(object(), object()),
        selected_orientation_plan=SimpleNamespace(native_sha256=SELECTED_NATIVE_SHA),
        order_manifest={
            "schema": "workpiece-resin-order-manifest-v4",
            "authority": "production-authoritative",
            "profiles": {
                "printer": "printer-a",
                "resin": "resin-a",
                "quality": "quality-a",
            },
        },
    )

    def execute_selected_production_order(**kwargs):
        captured.update(kwargs)
        return result

    def build_selected_production_bundle(**kwargs):
        assert kwargs["source_stl"] == b"stl-bytes"
        assert kwargs["original_name"] == "part.stl"
        assert kwargs["result"] is result
        return b"production-bundle", "part-workpiece-resin-production.zip"

    monkeypatch.setattr(main, "execute_selected_production_order", execute_selected_production_order)
    monkeypatch.setattr(main, "build_selected_production_bundle", build_selected_production_bundle)

    response = client.post(
        "/v1/project",
        files={"file": ("part.stl", b"stl-bytes", "application/octet-stream")},
        data={
            "printer_profile": "printer-a",
            "resin_profile": "resin-a",
            "quality": "quality-a",
            "requested_quantity": "7",
            "finalist_limit": "3",
            "rotate_x": "0",
            "rotate_y": "0",
            "rotate_z": "0",
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.content == b"production-bundle"
    assert response.headers["x-workpiece-authority"] == "production-authoritative"
    assert response.headers["x-workpiece-source-sha256"] == SOURCE_SHA
    assert response.headers["x-workpiece-selected-native-sha256"] == SELECTED_NATIVE_SHA
    assert response.headers["x-workpiece-plate-count"] == "2"
    assert response.headers["x-workpiece-toolchain-ref"] == VALID_TOOLCHAIN
    assert response.headers["x-workpiece-order-schema"] == "workpiece-resin-order-manifest-v4"
    assert response.headers["content-disposition"] == 'attachment; filename="part-workpiece-resin-production.zip"'

    assert captured["source_stl"] == b"stl-bytes"
    assert captured["original_name"] == "part.stl"
    assert captured["requested_quantity"] == 7
    assert captured["registry"] is registry
    assert captured["printer_profile_id"] == "printer-a"
    assert captured["resin_profile_id"] == "resin-a"
    assert captured["quality_profile_id"] == "quality-a"
    assert captured["finalist_limit"] == 3


def test_project_endpoint_rejects_manual_orientation_before_toolchain_or_slicing(monkeypatch):
    client = _client(monkeypatch)

    def should_not_resolve(**kwargs):
        raise AssertionError("toolchain must not be consulted for a rejected manual orientation")

    monkeypatch.setattr(main, "resolve_toolchain_ref", should_not_resolve)
    response = client.post(
        "/v1/project",
        files={"file": ("part.stl", b"stl-bytes", "application/octet-stream")},
        data={
            "printer_profile": "printer-a",
            "resin_profile": "resin-a",
            "quality": "quality-a",
            "rotate_z": "90",
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert "selected automatically" in response.json()["detail"]


def test_project_endpoint_requires_immutable_toolchain_before_upload_work(monkeypatch):
    client = _client(monkeypatch)

    def missing_toolchain(**kwargs):
        raise ToolchainProvenanceError("immutable toolchain required")

    monkeypatch.setattr(main, "resolve_toolchain_ref", missing_toolchain)
    monkeypatch.setattr(
        main,
        "validate_stl_bytes",
        lambda data: (_ for _ in ()).throw(AssertionError("STL validation must not run")),
    )

    response = client.post(
        "/v1/project",
        files={"file": ("part.stl", b"stl-bytes", "application/octet-stream")},
        data={
            "printer_profile": "printer-a",
            "resin_profile": "resin-a",
            "quality": "quality-a",
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 503
    assert "immutable toolchain required" in response.json()["detail"]


def test_project_endpoint_caps_requested_quantity(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(main, "MAX_PRODUCTION_QUANTITY", 5)
    monkeypatch.setattr(
        main,
        "resolve_toolchain_ref",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("toolchain must not be consulted")),
    )

    response = client.post(
        "/v1/project",
        files={"file": ("part.stl", b"stl-bytes", "application/octet-stream")},
        data={
            "printer_profile": "printer-a",
            "resin_profile": "resin-a",
            "quality": "quality-a",
            "requested_quantity": "6",
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert "between 1 and 5" in response.json()["detail"]
