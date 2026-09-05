import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import pytest

from app.engine import (
    EngineError,
    NativeArtifact,
    Orientation,
    build_prusa_project_command,
    build_prusa_slice_command,
    build_review_bundle,
    critical_issue_count,
    parse_uvtools_issues,
)
from app.profiles import Profile


def profile(pid, kind, path, **metadata):
    return Profile(pid, kind, pid, True, True, Path(path), {"id": pid, **metadata})


def test_prusa_project_command_saves_effective_config_centers_and_orients_recipe():
    command = build_prusa_project_command(
        "/opt/prusa",
        Path("/tmp/in.stl"),
        Path("/tmp/review.3mf"),
        Path("/tmp/effective.ini"),
        profile(
            "p",
            "printer",
            "/profiles/p.ini",
            display_width_mm=82.62,
            display_height_mm=130.56,
        ),
        profile("r", "resin", "/profiles/r.ini"),
        profile("q", "quality", "/profiles/q.ini"),
        Orientation(10, -20, 35),
    )
    assert command[:7] == ["/opt/prusa", "--load", "/profiles/p.ini", "--load", "/profiles/r.ini", "--load", "/profiles/q.ini"]
    assert command[7:9] == ["--center", "41.31,65.28"]
    assert command[9:11] == ["--rotate-x", "10"]
    assert ["--save", "/tmp/effective.ini"] == command[15:17]
    assert "--export-3mf" in command
    assert command[-1] == "/tmp/in.stl"


def test_prusa_slice_command_uses_exact_recipe_pair_without_rearrangement():
    command = build_prusa_slice_command(
        "/opt/prusa",
        Path("/tmp/review.3mf"),
        Path("/tmp/effective.ini"),
        Path("/tmp/out.sl1"),
    )
    assert command == [
        "/opt/prusa",
        "--load", "/tmp/effective.ini",
        "--dont-arrange",
        "--export-sla",
        "--output", "/tmp/out.sl1",
        "/tmp/review.3mf",
    ]
    assert "--rotate" not in command
    assert "--rotate-x" not in command
    assert "--rotate-y" not in command


def _artifact() -> NativeArtifact:
    effective = b"printer_technology = SLA\n"
    return NativeArtifact(
        project_bytes=b"project",
        project_filename="part-workpiece-review.3mf",
        project_sha256="project-sha",
        intermediate_bytes=b"sl1",
        intermediate_filename="part-workpiece-intermediate.sl1",
        bytes=b"ctb",
        filename="part-workpiece.ctb",
        media_type="application/octet-stream",
        source_sha256="source-sha",
        intermediate_sha256="sl1-sha",
        native_sha256="ctb-sha",
        issue_summary={"islands": 0},
        issue_text="Issues: 0\n",
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        orientation=Orientation(15, 0, 25),
        effective_config_bytes=effective,
        effective_config_filename="part-workpiece-effective.ini",
        effective_config_sha256=hashlib.sha256(effective).hexdigest(),
    )


def test_review_bundle_contains_complete_recipe_provenance_and_runtime_in_one_pass():
    execution_environment = {
        "toolchain_image_ref": "ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:" + "a" * 64,
        "immutable": True,
    }
    bundle, filename = build_review_bundle(
        b"stl",
        original_name="part.stl",
        artifact=_artifact(),
        authority="acceptance-candidate-only",
        execution_environment=execution_environment,
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
        manifest = json.loads(zf.read("manifest.json"))
        assert zf.getinfo("part-workpiece-review.3mf").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("part-workpiece-intermediate.sl1").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("part-workpiece.ctb").compress_type == zipfile.ZIP_STORED
    assert manifest["schema"] == "workpiece-resin-bundle-v2"
    assert manifest["provenance_chain"] == [
        "source_stl", "review_3mf", "effective_config", "intermediate_sl1", "printer_native"
    ]
    assert manifest["files"]["review_3mf"]["sha256"] == "project-sha"
    assert manifest["files"]["effective_config"]["sha256"] == _artifact().effective_config_sha256
    assert manifest["files"]["printer_native"]["sha256"] == "ctb-sha"
    assert manifest["execution_environment"] == execution_environment
    assert "indivisible retained recipe" in manifest["recipe_rule"]
    assert "effective-config" in manifest["review_rule"]


def test_bundle_rejects_missing_or_mismatched_effective_config():
    artifact = _artifact()
    broken = NativeArtifact(**{
        **artifact.__dict__,
        "effective_config_sha256": "0" * 64,
    })
    with pytest.raises(EngineError, match="configuration hash"):
        build_review_bundle(
            b"stl",
            original_name="part.stl",
            artifact=broken,
            authority="acceptance-candidate-only",
        )


def test_orientation_is_bounded_and_finite():
    with pytest.raises(EngineError):
        Orientation(361, 0, 0).validate()
    with pytest.raises(EngineError):
        Orientation(math.inf, 0, 0).validate()
    with pytest.raises(EngineError):
        Orientation(True, 0, 0).validate()


def test_issue_parser_matches_pinned_uvtools_6p2_output_contract():
    text = (
        "Opening file production.ctb: Done in 0.02s\n"
        "Detecting issues: Done in 0.11s\n"
        "Issues: 7\n"
        "Island, 5, 20px², {X=1,Y=2,Width=3,Height=4}\n"
        "Island, 9, 15px², {X=1,Y=2,Width=3,Height=4}\n"
        "ResinTrap, 10-15  (6), 30px³, {X=1,Y=2,Width=3,Height=4}\n"
        "SuctionCup, 20-25  (6), 40px³, {X=1,Y=2,Width=3,Height=4}\n"
        "TouchingBound, 3, 5px², {X=0,Y=0,Width=1,Height=1}\n"
        "EmptyLayer, 30, 0px², {X=0,Y=0,Width=0,Height=0}\n"
        "PrintHeight, 31, 1px², {X=0,Y=0,Width=1,Height=1}\n"
    )
    issues = parse_uvtools_issues(text)
    assert issues == {
        "islands": 2,
        "overhangs": 0,
        "resin_traps": 1,
        "suction_cups": 1,
        "touching_bounds": 1,
        "empty_layers": 1,
    }
    assert critical_issue_count(issues) == 6


def test_issue_parser_accepts_confident_zero_and_fails_closed_on_changed_format():
    assert critical_issue_count(parse_uvtools_issues("Issues: 0\n")) == 0
    with pytest.raises(EngineError, match="incomplete"):
        parse_uvtools_issues("Issues: 2\nIsland, 5, 20px², {X=1,Y=2,Width=3,Height=4}\n")
    with pytest.raises(EngineError, match="unrecognized"):
        parse_uvtools_issues("Issues: 1\nFutureIssueType, 5, 20px², {X=1,Y=2,Width=3,Height=4}\n")
