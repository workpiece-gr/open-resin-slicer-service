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
    build_prusa_slice_command,
    build_review_bundle,
    critical_issue_count,
    parse_uvtools_issues,
)
from app.profiles import Profile


def profile(pid, kind, path, **metadata):
    return Profile(pid, kind, pid, True, True, Path(path), {"id": pid, **metadata})


def test_prusa_project_command_loads_locked_profiles_and_explicit_orientation():
    command = build_prusa_project_command(
        "/opt/prusa",
        Path("/tmp/in.stl"),
        Path("/tmp/review.3mf"),
        profile("p", "printer", "/profiles/p.ini"),
        profile("r", "resin", "/profiles/r.ini"),
        profile("q", "quality", "/profiles/q.ini"),
        Orientation(10, -20, 35),
    )
    assert command[:7] == ["/opt/prusa", "--load", "/profiles/p.ini", "--load", "/profiles/r.ini", "--load", "/profiles/q.ini"]
    assert ["--rotate-x", "10"] == command[7:9]
    assert "--export-3mf" in command
    assert command[-1] == "/tmp/in.stl"


def test_prusa_slice_command_uses_exact_project_without_profile_reload():
    command = build_prusa_slice_command(
        "/opt/prusa", Path("/tmp/review.3mf"), Path("/tmp/out.sl1")
    )
    assert command == [
        "/opt/prusa", "--export-sla", "--output", "/tmp/out.sl1", "/tmp/review.3mf"
    ]
    assert "--load" not in command
    assert "--rotate" not in command


def _artifact() -> NativeArtifact:
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
        issue_text="Islands: 0\n",
        printer_profile="elegoo-mars-2",
        resin_profile="elegoo-water-washable-grey",
        quality_profile="balanced-0p05-medium",
        orientation=Orientation(15, 0, 25),
    )


def test_review_bundle_contains_provenance_artifacts_manifest_and_runtime_in_one_pass():
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
            "part-workpiece-intermediate.sl1",
            "part-workpiece.ctb",
            "uvtools-issues.txt",
            "manifest.json",
        }
        manifest = json.loads(zf.read("manifest.json"))
        assert zf.getinfo("part-workpiece-review.3mf").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("part-workpiece-intermediate.sl1").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("part-workpiece.ctb").compress_type == zipfile.ZIP_STORED
    assert manifest["provenance_chain"] == [
        "source_stl", "review_3mf", "intermediate_sl1", "printer_native"
    ]
    assert manifest["files"]["review_3mf"]["sha256"] == "project-sha"
    assert manifest["files"]["printer_native"]["sha256"] == "ctb-sha"
    assert manifest["execution_environment"] == execution_environment
    assert "If the 3MF is edited" in manifest["review_rule"]


def test_orientation_is_bounded():
    with pytest.raises(EngineError):
        Orientation(361, 0, 0).validate()


def test_issue_parser_extracts_critical_categories():
    text = "Islands: 7\nSuction cups: 2\nResin traps: 1\nTouching bounds: 3\nEmpty layers: 4\n"
    issues = parse_uvtools_issues(text)
    assert issues["islands"] == 7
    assert issues["suction_cups"] == 2
    assert issues["resin_traps"] == 1
    assert issues["touching_bounds"] == 3
    assert issues["empty_layers"] == 4
    assert critical_issue_count(issues) == 17
