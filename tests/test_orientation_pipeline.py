import hashlib
import struct

import pytest

from app.orientation_pipeline import (
    build_proxy_orientation_plan,
    proxy_orientation_plan_manifest,
)
from app.orientation_proxy import OrientationProxyError


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
    payload = bytearray(b"Workpiece orientation proxy fixture".ljust(80, b"\0"))
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


def test_proxy_pipeline_binds_complete_screening_to_exact_source_stl():
    source = _cube_stl()
    plan = build_proxy_orientation_plan(
        source,
        tilt_degrees=(15,),
        include_cardinal=False,
        finalist_limit=3,
        max_sample_layers=8,
    )
    manifest = proxy_orientation_plan_manifest(plan)

    assert plan.source_sha256 == hashlib.sha256(source).hexdigest()
    assert plan.triangle_count == 12
    assert len(plan.candidates) == 9
    assert manifest["schema"] == "workpiece-resin-orientation-proxy-plan-v1"
    assert manifest["source_sha256"] == plan.source_sha256
    assert manifest["triangle_count"] == 12
    assert manifest["candidate_count"] == 9
    assert manifest["automatic_production_authority"] is False
    assert manifest["screening"]["automatic_production_authority"] is False
    candidate_keys = {item.spec.canonical_key for item in plan.candidates}
    assert all(
        finalist.candidate.spec.canonical_key in candidate_keys
        for finalist in plan.screening.finalists
    )


def test_proxy_pipeline_rejects_invalid_stl_before_candidate_generation():
    with pytest.raises(OrientationProxyError):
        build_proxy_orientation_plan(b"not an stl", tilt_degrees=(15,), include_cardinal=False)
