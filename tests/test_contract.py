import json
from pathlib import Path

import pytest

from app.profiles import ProfileError, ProfileRegistry


def test_mars2_grey_tuple_is_candidate_only():
    root = Path(__file__).parents[1]
    printer = json.loads((root / "profiles/printers/elegoo-mars-2.json").read_text("utf-8"))
    resin = json.loads((root / "profiles/resins/elegoo-water-washable-grey.json").read_text("utf-8"))
    compatibility = json.loads((root / "profiles/compatibility.json").read_text("utf-8"))
    combo = ["elegoo-mars-2", "elegoo-water-washable-grey", "balanced-0p05-medium"]

    assert printer["candidate_ready"] is True
    assert printer["production_ready"] is False
    assert printer["display_pixels_x"] == 1620
    assert printer["display_pixels_y"] == 2560
    assert printer["max_print_height_mm"] == 150

    assert resin["candidate_ready"] is True
    assert resin["production_ready"] is False
    assert resin["candidate_seed"]["normal_exposure_s"] == 2.75
    assert resin["candidate_seed"]["initial_exposure_s"] == 30
    assert resin["manufacturer_range"]["normal_exposure_s"] == [2.5, 3.0]
    assert resin["manufacturer_range"]["initial_exposure_s"] == [25, 35]

    assert combo in compatibility["candidate_combinations"]
    assert compatibility["production_combinations"] == []

    registry = ProfileRegistry(root / "profiles")
    profiles = registry.resolve_candidate(*combo)
    assert [profile.id for profile in profiles] == combo
    with pytest.raises(ProfileError, match="not validated for production"):
        registry.resolve_production(*combo)


def test_grey_prusaslicer_material_seed_matches_metadata():
    root = Path(__file__).parents[1]
    ini = (root / "profiles/resins/elegoo-water-washable-grey.ini").read_text("utf-8")
    assert "exposure_time = 2.75" in ini
    assert "initial_exposure_time = 30" in ini
    assert "initial_layer_height = 0.05" in ini


def test_water_washable_reference_preserves_other_color_ranges():
    root = Path(__file__).parents[1]
    reference = json.loads((root / "profiles/reference/elegoo-water-washable-v1.json").read_text("utf-8"))
    assert reference["status"] == "reference-only-color-required"
    assert reference["manufacturer_ranges"]["black"]["normal_exposure_s"] == [3.0, 3.5]
    assert reference["manufacturer_ranges"]["ceramic-grey"]["normal_exposure_s"] == [2.5, 3.0]
    assert reference["manufacturer_ranges"]["clear-blue"]["normal_exposure_s"] == [2.5, 3.0]


def test_split_toolchain_keeps_engine_pins_out_of_normal_service_build():
    root = Path(__file__).parents[1]
    service_dockerfile = (root / "Dockerfile").read_text("utf-8")
    toolchain_dockerfile = (root / "Dockerfile.toolchain").read_text("utf-8")

    assert "ARG TOOLCHAIN_IMAGE=" in service_dockerfile
    assert "FROM ${TOOLCHAIN_IMAGE}" in service_dockerfile
    assert "git clone https://github.com/prusa3d/PrusaSlicer.git" not in service_dockerfile
    assert "make -j" not in service_dockerfile

    assert "PRUSA_SLICER_VERSION=2.9.7" in toolchain_dockerfile
    assert "450d70dd81c9b8990bc6bc8e690e31f82ddceb11" in toolchain_dockerfile
    assert "UVTOOLS_VERSION=7.1.2" in toolchain_dockerfile
    assert "4d06ec7e86043c409d81126311c5c8e6f4629eb5bb9d3f092d8ec2401d157c1c" in toolchain_dockerfile
    assert "sha256sum -c" in toolchain_dockerfile


def test_project_route_uses_candidate_tuple_without_authority_gates():
    root = Path(__file__).parents[1]
    main_source = (root / "app/main.py").read_text("utf-8")
    project_source = (root / "app/project.py").read_text("utf-8")
    assert "build_order_project" in main_source
    assert "resolve_candidate" in project_source
    assert "resolve_production" not in main_source and "resolve_production" not in project_source
    assert "toolchain" not in main_source.lower()
