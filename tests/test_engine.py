from pathlib import Path

import pytest

from app.engine import EngineError, Orientation, build_prusa_command, critical_issue_count, parse_uvtools_issues
from app.profiles import Profile


def profile(pid, kind, path, **metadata):
    return Profile(pid, kind, pid, True, True, Path(path), {"id": pid, **metadata})


def test_prusa_command_loads_locked_profiles_and_explicit_orientation():
    command = build_prusa_command(
        "/opt/prusa",
        Path("/tmp/in.stl"),
        Path("/tmp/out.sl1"),
        profile("p", "printer", "/profiles/p.ini"),
        profile("r", "resin", "/profiles/r.ini"),
        profile("q", "quality", "/profiles/q.ini"),
        Orientation(10, -20, 35),
    )
    assert command[:7] == ["/opt/prusa", "--load", "/profiles/p.ini", "--load", "/profiles/r.ini", "--load", "/profiles/q.ini"]
    assert ["--rotate-x", "10"] == command[7:9]
    assert "--export-sla" in command
    assert command[-1] == "/tmp/in.stl"


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
