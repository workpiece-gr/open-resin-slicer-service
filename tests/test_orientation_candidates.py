import math

import pytest

from app.orientation import OrientationMetrics, OrientationPlanError
from app.orientation_candidates import (
    MAX_ORIENTATION_SPECS,
    OrientationSpec,
    generate_orientation_specs,
)


def test_default_generator_is_deterministic_bounded_and_identity_first():
    first = generate_orientation_specs()
    second = generate_orientation_specs()
    assert first == second
    assert len(first) == 30
    assert len(first) <= MAX_ORIENTATION_SPECS
    assert first[0].canonical_key == (0.0, 0.0, 0.0)
    assert len({item.canonical_key for item in first}) == len(first)
    assert all(item.z_deg == 0.0 for item in first)


def test_default_generator_covers_principal_build_directions():
    keys = {item.canonical_key for item in generate_orientation_specs()}
    assert (90.0, 0.0, 0.0) in keys
    assert (270.0, 0.0, 0.0) in keys
    assert (0.0, 90.0, 0.0) in keys
    assert (0.0, 270.0, 0.0) in keys
    assert (180.0, 0.0, 0.0) in keys


def test_tilt_order_is_canonical_independent_of_input_order():
    assert generate_orientation_specs(tilt_degrees=(45, 15, 30)) == generate_orientation_specs()


def test_cardinal_orientations_can_be_excluded_for_proxy_screening():
    specs = generate_orientation_specs(include_cardinal=False)
    assert len(specs) == 25
    keys = {item.canonical_key for item in specs}
    assert (90.0, 0.0, 0.0) not in keys
    assert (180.0, 0.0, 0.0) not in keys


def test_invalid_tilts_and_flags_fail_closed():
    for invalid in (0, 90, -15, math.inf, True):
        with pytest.raises(OrientationPlanError):
            generate_orientation_specs(tilt_degrees=(invalid,))
    with pytest.raises(OrientationPlanError, match="unique"):
        generate_orientation_specs(tilt_degrees=(15, 15))
    with pytest.raises(OrientationPlanError, match="boolean"):
        generate_orientation_specs(include_cardinal=1)


def test_candidate_safety_limit_is_enforced():
    with pytest.raises(OrientationPlanError, match="safety limit"):
        generate_orientation_specs(tilt_degrees=tuple(range(1, 9)))


def test_spec_attaches_metric_provenance_without_changing_rotation():
    spec = OrientationSpec(-30, 15)
    metrics = OrientationMetrics(
        max_layer_area_mm2=100,
        support_volume_mm3=20,
        support_contact_area_mm2=5,
        z_height_mm=40,
        source="geometry-proxy",
    )
    candidate = spec.with_metrics(metrics)
    assert candidate.canonical_key == (330.0, 15.0, 0.0)
    assert candidate.metrics is metrics
