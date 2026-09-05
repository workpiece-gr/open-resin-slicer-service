import io
import json
import zipfile
from pathlib import Path

import pytest

from app.engine import (
    EngineError,
    NativeArtifact,
    Orientation,
    build_prusa_project_command,
    build_prusa_review_command,
    build_prusa_slice_command,
    build_review_bundle,
    critical_issue_count,
    parse_uvtools_issues,
)
from app.profiles import Profile


def profile(pid, kind, path, **metadata):
    return Profile(pid, kind, pid, True, True, Path(path), {"id": pid, **metadata})


def test_prusa_project_command_saves_effective_config_and_explicit_orientation():
    command = build_prusa_project_command(
        "/opt/prusa",
        Path("/tmp/in.stl"),
        Path("/tmp/oriented.3mf"),
        Path("/tmp/effective.ini"),
        profile("p", "printer", "/profiles/p.ini"),
        profile("r", "resin", "/profiles/r.ini"),
        profile("q", "quality", "/profiles/q.ini"),
        Orientation(10, -20, 35),
    )
    assert command[:7] == ["/opt/prusa", "--load", "/profiles/p.ini", "--load", "/profiles/r.ini", "--load", "/profiles/q.ini"]
    assert ["--rotate-x", "10"] == command[7:9]
    assert "--save" in command
    assert command[command.index("--save") + 1] == "/tmp/effective.ini"
    assert "--export-3mf" in command
    assert command[-1] == "/tmp/in.stl"


def test_prusa_review_command_centers_oriented_geometry_without_reapplying_rotation():
    command = build_prusa_review_command(
        "/opt/prusa",
        Path("/tmp/oriented.3mf"),
        Path("/tmp/effective.ini"),
        Path("/tmp/review.3mf"),
        41.31,
        65.28,
    )
    assert command == [
        "/opt/prusa",
        "--load", "/tmp/effective.ini",
        "--center", "41.31,65.28",
        "--export-3mf",
        "--output", "/tmp/review.3mf",
        "/tmp/oriented.3mf",
    ]
    assert "--rotate" not in command
    assert "--rotate-x" not in command
    assert "--rotate-y" not in command


def test_prusa_slice_command_uses_exact_effective_config_without_reapplying_transforms():
    command = build_prusa_slice_command(
        "/opt/prusa",
        Path("/tmp/review.3mf"),
        Path("/tmp/effective.ini"),
        Path("/tmp/out.sl1"),
    )
    assert command == [
        "/opt/prusa",
        "--load", "/tmp/effective.ini",
        "--export-sla",
        "--output", "/tmp/out.sl1",
        "/tmp/review.3mf",
    ]
    assert command.count("--load") == 1
    assert "--center" not in command
    assert "--rotate" not in command
    assert "--rotate-x" not in command
    assert "--rotate-y" not in command


def test_review_bundle_contains_recipe_provenance_and_manifest():
    artifact = NativeArtifact(
        project_bytes=b"project",
        project_filename="part-workpiece-review.3mf",
        project_sha256="project-sha",
        effective_config_bytes=b"printer_technology = SLA\n",
        effective_config_filename="part-workpiece-effective.ini",
        effective_config_sha256="config-sha",
        placement_center_x_mm=41.31,
        placement_center_y_mm=65.28,
        intermediate_bytes=b"sl1",
        intermediate_filename="part-workpiece-intermediate.sl1",
        bytes=b"ctb",
        filename="part-workpiece.ctb",
        media_type="application/octet-stream",
        source_sha256="source-sha",
        intermediate_sha256="sl1-sha",
        native_sha256="ctb-sha",
        issue_summary={"islands": 0},
        issue_text="Islands: 0\n",
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        orientation=Orientation(15, 0, 25),
    )
    bundle, filename = build_review_bundle(
        b"stl", original_name="part.stl", artifact=artifact, authority="acceptance-candidate-only"
    )
    assert filename == "part-workpiece-resin-bundle.zip"
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert names == {
            "part-source.stl",
            "part-workpiece-review.3mf",
            "part-workpiece-effective.ini",
            "part-workpiece-intermediate.sl1",
            "part-workpiece.ctb",
            "uvtools-issues.txt",
            "manifest.json",
        }
        assert zf.read("part-workpiece-effective.ini") == b"printer_technology = SLA\n"
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["provenance_chain"] == [
        "source_stl", "review_3mf", "intermediate_sl1", "printer_native"
    ]
    assert manifest["slice_recipe"]["geometry"] == "review_3mf"
    assert manifest["slice_recipe"]["configuration"] == "effective_config"
    assert manifest["slice_recipe"]["placement"] == {
        "method": "explicit-prusaslicer-center",
        "center_mm": {"x": 41.31, "y": 65.28},
    }
    assert "not treated as a self-contained Prusa configuration project" in manifest["slice_recipe"]["review_3mf_role"]
    assert manifest["files"]["review_3mf"]["sha256"] == "project-sha"
    assert manifest["files"]["effective_config"]["sha256"] == "config-sha"
    assert manifest["files"]["printer_native"]["sha256"] == "ctb-sha"
    assert "effective-config hash" in manifest["review_rule"]


def test_orientation_is_bounded():
    with pytest.raises(EngineError):
        Orientation(361, 0, 0).validate()


def test_issue_parser_extracts_critical_categories():
    text = "Islands: 7\nSuction cups: 2\nResin traps: 1\nEmpty layers: 0\n"
    issues = parse_uvtools_issues(text)
    assert issues["islands"] == 7
    assert issues["suction_cups"] == 2
    assert issues["resin_traps"] == 1
    assert critical_issue_count(issues) == 10
