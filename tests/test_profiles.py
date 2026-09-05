import json
from pathlib import Path

import pytest

from app.profiles import ProfileError, ProfileRegistry


VALIDATED_TRANSFORM = {
    "origin_display_x_mm": 1.0,
    "origin_display_y_mm": 0.5,
    "x_axis_display_x": 1.0,
    "x_axis_display_y": 0.0,
    "y_axis_display_x": 0.0,
    "y_axis_display_y": 1.0,
}


def write_profile(root: Path, kind_dir: str, pid: str, candidate=True, production=False, **extra):
    directory = root / kind_dir
    directory.mkdir(parents=True, exist_ok=True)
    ini = directory / f"{pid}.ini"
    ini.write_text("printer_technology = SLA\n", encoding="utf-8")
    (directory / f"{pid}.json").write_text(json.dumps({
        "id": pid,
        "label": pid,
        "candidate_ready": candidate,
        "production_ready": production,
        "config": f"{kind_dir}/{pid}.ini",
        **extra,
    }), encoding="utf-8")


def registry_fixture(tmp_path: Path, *, candidate_combos=None, production_combos=None, production=False):
    printer_extra = {
        "native_format": "ctb",
        "display_width_mm": 82.62,
        "display_height_mm": 130.56,
        "manufacturing_envelope_width_mm": 80,
        "manufacturing_envelope_depth_mm": 129,
        "manufacturing_envelope_coordinate_mapping": "validated" if production else "unverified",
    }
    if production:
        printer_extra["manufacturing_to_display_transform"] = VALIDATED_TRANSFORM
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        production=production,
        **printer_extra,
    )
    write_profile(tmp_path, "resins", "resin-a", production=production)
    write_profile(tmp_path, "quality", "balanced", production=production)
    (tmp_path / "compatibility.json").write_text(json.dumps({
        "candidate_combinations": candidate_combos or [],
        "production_combinations": production_combos or [],
    }), encoding="utf-8")
    return ProfileRegistry(tmp_path)


def test_candidate_registry_fails_closed_until_exact_tuple_is_approved(tmp_path):
    registry = registry_fixture(tmp_path)
    with pytest.raises(ProfileError, match="not approved for acceptance"):
        registry.resolve_candidate("printer-a", "resin-a", "balanced")


def test_candidate_registry_accepts_candidate_tuple_but_production_stays_blocked(tmp_path):
    combo = [["printer-a", "resin-a", "balanced"]]
    registry = registry_fixture(tmp_path, candidate_combos=combo)
    profiles = registry.resolve_candidate("printer-a", "resin-a", "balanced")
    assert [p.id for p in profiles] == ["printer-a", "resin-a", "balanced"]
    envelope = registry.printer_manufacturing_envelope("printer-a")
    assert (envelope.width_mm, envelope.depth_mm, envelope.coordinate_mapping) == (80.0, 129.0, "unverified")
    assert envelope.display_transform is None
    assert registry.candidate_ready is True
    assert registry.production_ready is False
    with pytest.raises(ProfileError, match="not validated for production"):
        registry.resolve_production("printer-a", "resin-a", "balanced")


def test_production_tuple_requires_candidate_tuple_too(tmp_path):
    with pytest.raises(ProfileError, match="must also be approved as a candidate"):
        registry_fixture(tmp_path, production_combos=[["printer-a", "resin-a", "balanced"]], production=True)


def test_registry_accepts_separately_promoted_production_tuple(tmp_path):
    combo = [["printer-a", "resin-a", "balanced"]]
    registry = registry_fixture(tmp_path, candidate_combos=combo, production_combos=combo, production=True)
    assert [p.id for p in registry.resolve_production("printer-a", "resin-a", "balanced")] == ["printer-a", "resin-a", "balanced"]
    envelope = registry.printer_manufacturing_envelope("printer-a", require_coordinate_mapping=True)
    assert envelope.display_transform is not None
    transform = registry.printer_manufacturing_display_transform("printer-a")
    assert transform.to_display(0, 0) == (1.0, 0.5)
    assert transform.to_display(80, 129) == (81.0, 129.5)
    assert registry.production_ready is True


def test_registry_rejects_path_escape(tmp_path):
    d = tmp_path / "printers"
    d.mkdir()
    (d / "evil.json").write_text(json.dumps({
        "id": "evil", "label": "evil", "candidate_ready": True, "production_ready": False,
        "config": "../../etc/passwd"
    }), encoding="utf-8")
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="escapes"):
        ProfileRegistry(tmp_path)


def test_registry_rejects_manufacturing_envelope_larger_than_display(tmp_path):
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        display_width_mm=82.62,
        display_height_mm=130.56,
        manufacturing_envelope_width_mm=83,
        manufacturing_envelope_depth_mm=129,
        manufacturing_envelope_coordinate_mapping="unverified",
    )
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="width exceeds display width"):
        ProfileRegistry(tmp_path)


def test_validated_mapping_requires_explicit_rigid_transform(tmp_path):
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        display_width_mm=82.62,
        display_height_mm=130.56,
        manufacturing_envelope_width_mm=80,
        manufacturing_envelope_depth_mm=129,
        manufacturing_envelope_coordinate_mapping="validated",
    )
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="requires manufacturing_to_display_transform"):
        ProfileRegistry(tmp_path)


def test_validated_transform_must_map_entire_envelope_inside_display(tmp_path):
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        display_width_mm=82.62,
        display_height_mm=130.56,
        manufacturing_envelope_width_mm=80,
        manufacturing_envelope_depth_mm=129,
        manufacturing_envelope_coordinate_mapping="validated",
        manufacturing_to_display_transform={
            **VALIDATED_TRANSFORM,
            "origin_display_x_mm": 5.0,
        },
    )
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="outside the printer display"):
        ProfileRegistry(tmp_path)


def test_validated_transform_can_swap_and_reflect_display_axes(tmp_path):
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        display_width_mm=130,
        display_height_mm=82,
        manufacturing_envelope_width_mm=80,
        manufacturing_envelope_depth_mm=129,
        manufacturing_envelope_coordinate_mapping="validated",
        manufacturing_to_display_transform={
            "origin_display_x_mm": 129,
            "origin_display_y_mm": 0,
            "x_axis_display_x": 0,
            "x_axis_display_y": 1,
            "y_axis_display_x": -1,
            "y_axis_display_y": 0,
        },
    )
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    registry = ProfileRegistry(tmp_path)
    transform = registry.printer_manufacturing_display_transform("printer-a")
    assert transform.to_display(0, 0) == (129.0, 0.0)
    assert transform.to_display(80, 129) == (0.0, 80.0)


def test_production_printer_requires_validated_coordinate_mapping(tmp_path):
    write_profile(
        tmp_path,
        "printers",
        "printer-a",
        production=True,
        display_width_mm=82.62,
        display_height_mm=130.56,
        manufacturing_envelope_width_mm=80,
        manufacturing_envelope_depth_mm=129,
        manufacturing_envelope_coordinate_mapping="unverified",
    )
    (tmp_path / "compatibility.json").write_text('{"candidate_combinations": [], "production_combinations": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="requires physically validated"):
        ProfileRegistry(tmp_path)


def test_mars2_profile_separates_display_and_manufacturing_envelope():
    root = Path(__file__).resolve().parents[1] / "profiles"
    registry = ProfileRegistry(root)
    mars = registry.get("printer", "elegoo-mars-2")
    envelope = registry.printer_manufacturing_envelope("elegoo-mars-2")
    assert mars.metadata["display_width_mm"] == 82.62
    assert mars.metadata["display_height_mm"] == 130.56
    assert (envelope.width_mm, envelope.depth_mm) == (80.0, 129.0)
    assert envelope.coordinate_mapping == "unverified"
    assert envelope.display_transform is None
    with pytest.raises(ProfileError, match="not physically validated"):
        registry.printer_manufacturing_envelope(
            "elegoo-mars-2",
            require_coordinate_mapping=True,
        )
    with pytest.raises(ProfileError, match="not physically validated"):
        registry.printer_manufacturing_display_transform("elegoo-mars-2")
