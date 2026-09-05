import hashlib
import struct

import pytest

import app.orientation_pipeline as orientation_pipeline
from app.orientation_pipeline import (
    build_proxy_orientation_plan,
    proxy_orientation_plan_manifest,
)
from app.orientation_proxy import OrientationProxyError
from app.orientation_screen import OrientationScreenError


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
    assert manifest["proxy_work_budget"]["estimated_triangle_layer_evaluations"] <= (
        manifest["proxy_work_budget"]["triangle_layer_evaluation_cap"]
    )
    candidate_keys = {item.spec.canonical_key for item in plan.candidates}
    assert all(
        finalist.candidate.spec.canonical_key in candidate_keys
        for finalist in plan.screening.finalists
    )


def test_invalid_finalist_limit_is_rejected_before_stl_parsing(monkeypatch):
    def should_not_parse(_source):
        raise AssertionError("STL parsing must not run for an invalid finalist limit")

    monkeypatch.setattr(orientation_pipeline, "parse_proxy_triangles", should_not_parse)
    with pytest.raises(OrientationScreenError, match="safety cap"):
        build_proxy_orientation_plan(_cube_stl(), finalist_limit=9)


def test_proxy_pipeline_scales_sampling_to_fixed_work_budget(monkeypatch):
    monkeypatch.setattr(orientation_pipeline, "MAX_PROXY_TRIANGLE_LAYER_EVALUATIONS", 108)
    plan = build_proxy_orientation_plan(
        _cube_stl(),
        tilt_degrees=(15,),
        include_cardinal=False,
        finalist_limit=2,
        max_sample_layers=8,
    )
    assert len(plan.candidates) == 9
    assert all(item.metrics.sampled_layer_count == 1 for item in plan.candidates)
    manifest = proxy_orientation_plan_manifest(plan)
    assert manifest["proxy_work_budget"]["estimated_triangle_layer_evaluations"] == 108


def test_proxy_pipeline_rejects_invalid_stl_before_candidate_generation():
    with pytest.raises(OrientationProxyError):
        build_proxy_orientation_plan(b"not an stl", tilt_degrees=(15,), include_cardinal=False)
