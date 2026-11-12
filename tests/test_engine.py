import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import pytest

import subprocess

import app.engine as engine
from app.engine import (
    UVTOOLS_SUCCESS_EXIT,
    EngineError,
    NativeArtifact,
    Orientation,
    build_prusa_project_command,
    build_prusa_slice_command,
    build_review_bundle,
    critical_issue_count,
    parse_uvtools_issues,
    resolve_uvtools_target,
    slice_native,
    uvtools_command,
    uvtools_native_output_path,
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


def test_issue_parser_matches_pinned_uvtools_7p1_output_contract():
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


# Verbatim stdout of `UVtoolsCmd --no-progress print-issues demo.ctb` from the UVtools 7.1.2
# Linux x64 release (demo file UVtools_demo_file.sl1s converted with the Chitubox encoder).
UVTOOLS_7_1_2_PRINT_ISSUES = (
    "Opening file demo.ctb: Done in 0.22s\n"
    "Detecting issues: Done in 0.13s\n"
    "Issues: 3\n"
    "SuctionCup, 0-9  (10), 290956px\u00b3, {X=820,Y=734,Width=161,Height=120}\n"
    "SuctionCup, 0-9  (10), 290923px\u00b3, {X=820,Y=1108,Width=161,Height=119}\n"
    "SuctionCup, 0-9  (10), 128918px\u00b3, {X=503,Y=1714,Width=107,Height=83}\n"
)


def test_issue_parser_accepts_real_uvtools_7p1p2_output():
    issues = parse_uvtools_issues(UVTOOLS_7_1_2_PRINT_ISSUES)
    assert issues["suction_cups"] == 3 and critical_issue_count(issues) == 3


def test_uvtools_7_exit_code_and_global_option_placement():
    assert UVTOOLS_SUCCESS_EXIT == 0
    assert uvtools_command("/opt/uvtools/UVtoolsCmd", "print-issues", "/tmp/part.ctb") == [
        "/opt/uvtools/UVtoolsCmd", "--no-progress", "print-issues", "/tmp/part.ctb",
    ]
    assert uvtools_command("UVtoolsCmd", "convert", "a.sl1", "auto", "a.ctb") == [
        "UVtoolsCmd", "--no-progress", "convert", "a.sl1", "auto", "a.ctb",
    ]


def test_uvtools_target_must_be_auto_or_strict_encoder_name():
    assert resolve_uvtools_target(profile("p", "printer", "/p.ini", native_format="ctb", uvtools_target="auto")) == ("ctb", "auto")
    assert resolve_uvtools_target(profile("p", "printer", "/p.ini", native_format="ctb", uvtools_target="Chitubox")) == ("ctb", "Chitubox")
    with pytest.raises(EngineError, match="no UVtools conversion target"):
        resolve_uvtools_target(profile("p", "printer", "/p.ini", native_format="ctb"))
    for ambiguous in ("ctb", "goo", ".CTB"):
        with pytest.raises(EngineError, match="shared by several"):
            resolve_uvtools_target(profile("p", "printer", "/p.ini", native_format="ctb", uvtools_target=ambiguous))
    with pytest.raises(EngineError, match="CTB or GOO"):
        resolve_uvtools_target(profile("p", "printer", "/p.ini", native_format="sl1", uvtools_target="auto"))


def test_auto_convert_output_path_follows_the_intermediate_stem():
    assert uvtools_native_output_path(Path("/tmp/x/production.sl1"), "ctb") == Path("/tmp/x/production.ctb")
    assert uvtools_native_output_path(Path("/tmp/x/other.sl1s"), "goo") == Path("/tmp/x/other.goo")


def test_slice_native_uses_uvtools_7_command_contract(tmp_path, monkeypatch):
    printer_ini = tmp_path / "printer.ini"
    printer_ini.write_text("printer_technology = SLA\n", encoding="utf-8")
    printer = profile(
        "p", "printer", printer_ini, native_format="ctb", uvtools_target="auto",
        display_width_mm=82.62, display_height_mm=130.56,
    )
    resin = profile("r", "resin", tmp_path / "r.ini")
    quality = profile("q", "quality", tmp_path / "q.ini")
    prusa = tmp_path / "prusa-slicer"
    uvtools = tmp_path / "UVtoolsCmd"
    prusa.write_text("fake", encoding="utf-8")
    uvtools.write_text("fake", encoding="utf-8")
    calls = []

    def fake_run(command, *, timeout):
        command = [str(item) for item in command]
        calls.append(command)
        if command[0] == str(prusa):
            if "--export-3mf" in command:
                Path(command[command.index("--output") + 1]).write_bytes(b"review")
                Path(command[command.index("--save") + 1]).write_bytes(b"effective")
            else:
                assert "--dont-arrange" in command
                Path(command[command.index("--output") + 1]).write_bytes(b"sl1")
            return subprocess.CompletedProcess(command, 0, "prusa")
        assert command[:2] == [str(uvtools), "--no-progress"]
        if command[2] == "convert":
            intermediate, target, output = Path(command[3]), command[4], Path(command[5])
            assert intermediate.read_bytes() == b"sl1" and target == "auto"
            # UVtools 7 "auto" ignores the output argument: it writes <input stem>.ctb beside the input.
            written = intermediate.with_suffix(".ctb")
            assert output == written
            written.write_bytes(b"ctb")
            return subprocess.CompletedProcess(command, 0, "converted")
        if command[2] == "print-issues":
            assert command[3:] == [str(Path(calls[-2][5]))]
            return subprocess.CompletedProcess(command, 0, UVTOOLS_7_1_2_PRINT_ISSUES)
        raise AssertionError(command)

    monkeypatch.setattr(engine, "_run", fake_run)
    artifact = slice_native(
        b"solid", original_name="part.stl", printer=printer, resin=resin, quality=quality,
        orientation=Orientation(), prusa_bin=str(prusa), uvtools_cmd=str(uvtools),
        slice_timeout=5, uvtools_timeout=5, reject_critical=False,
    )
    assert [c[2] for c in calls if c[0] == str(uvtools)] == ["convert", "print-issues"]
    assert artifact.bytes == b"ctb" and artifact.issue_summary["suction_cups"] == 3

    # A non-zero UVtools exit is a failure under 7.x (6.x returned 1 after normal commands).
    def failing_run(command, *, timeout):
        command = [str(item) for item in command]
        if command[0] == str(prusa):
            return fake_run(command, timeout=timeout)
        return subprocess.CompletedProcess(command, 1, "Unrecognized command or argument")

    monkeypatch.setattr(engine, "_run", failing_run)
    with pytest.raises(EngineError, match="conversion failed with status 1"):
        slice_native(
            b"solid", original_name="part.stl", printer=printer, resin=resin, quality=quality,
            orientation=Orientation(), prusa_bin=str(prusa), uvtools_cmd=str(uvtools),
            slice_timeout=5, uvtools_timeout=5, reject_critical=False,
        )
