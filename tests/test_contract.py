import json
from pathlib import Path


def test_mars2_is_candidate_only_and_no_resin_tuple_is_approved_yet():
    root = Path(__file__).parents[1]
    printer = json.loads((root / "profiles/printers/elegoo-mars-2.json").read_text("utf-8"))
    compatibility = json.loads((root / "profiles/compatibility.json").read_text("utf-8"))
    assert printer["candidate_ready"] is True
    assert printer["production_ready"] is False
    assert printer["display_pixels_x"] == 1620
    assert printer["display_pixels_y"] == 2560
    assert printer["max_print_height_mm"] == 150
    assert compatibility["candidate_combinations"] == []
    assert compatibility["production_combinations"] == []


def test_water_washable_reference_requires_color():
    root = Path(__file__).parents[1]
    reference = json.loads((root / "profiles/reference/elegoo-water-washable-v1.json").read_text("utf-8"))
    assert reference["status"] == "reference-only-color-required"
    assert reference["manufacturer_ranges"]["black"]["normal_exposure_s"] == [3.0, 3.5]
    assert reference["manufacturer_ranges"]["ceramic-grey"]["normal_exposure_s"] == [2.5, 3.0]


def test_dockerfile_pins_both_engines_and_uvtools_hash():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text("utf-8")
    assert "b028299c770b8380ee81c921a2867d522f288123" in dockerfile
    assert "UVTOOLS_VERSION=6.2.0" in dockerfile
    assert "cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f" in dockerfile
    assert "sha256sum -c" in dockerfile
