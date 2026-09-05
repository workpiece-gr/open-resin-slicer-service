import hashlib
import struct

from fastapi.testclient import TestClient

import app.main as main


FACES = [
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
    (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
]


def _cube_stl(size: float = 20.0) -> bytes:
    vertices = [
        (0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0),
        (0, 0, size), (size, 0, size), (size, size, size), (0, size, size),
    ]
    payload = bytearray(b"Workpiece proxy endpoint fixture".ljust(80, b"\0"))
    payload.extend(struct.pack("<I", len(FACES)))
    for face in FACES:
        points = [vertices[index] for index in face]
        payload.extend(
            struct.pack(
                "<12fH",
                0, 0, 1,
                *points[0], *points[1], *points[2],
                0,
            )
        )
    return bytes(payload)


def test_proxy_orientation_endpoint_is_authenticated_source_bound_and_non_authoritative(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "proxy-test-token")
    client = TestClient(main.app)
    source = _cube_stl()

    response = client.post(
        "/v1/orientation/proxy",
        headers={"Authorization": "Bearer proxy-test-token"},
        files={"file": ("part.stl", source, "model/stl")},
        data={"finalist_limit": "2"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == "workpiece-resin-orientation-proxy-plan-v1"
    assert payload["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert payload["triangle_count"] == 12
    assert payload["candidate_count"] == 30
    assert payload["automatic_production_authority"] is False
    assert payload["screening"]["automatic_production_authority"] is False
    assert payload["screening"]["finalist_limit"] == 2
    assert response.headers["cache-control"] == "private, no-store"


def test_proxy_orientation_endpoint_requires_authentication(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "proxy-test-token")
    client = TestClient(main.app)
    response = client.post(
        "/v1/orientation/proxy",
        files={"file": ("part.stl", _cube_stl(), "model/stl")},
    )
    assert response.status_code == 401


def test_proxy_orientation_endpoint_rejects_unsafe_finalist_limit(monkeypatch):
    monkeypatch.setattr(main, "PROJECT_TOKEN", "proxy-test-token")
    client = TestClient(main.app)
    response = client.post(
        "/v1/orientation/proxy",
        headers={"Authorization": "Bearer proxy-test-token"},
        files={"file": ("part.stl", _cube_stl(), "model/stl")},
        data={"finalist_limit": "9"},
    )
    assert response.status_code == 422
    assert "safety cap" in response.json()["detail"]
