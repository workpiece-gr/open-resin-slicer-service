#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


IMAGE = "workpiece-resin-toolchain:candidate"
ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".toolchain-smoke"
PROFILES = ROOT / "profiles"
MAX_OUTPUT = 20_000
MARS2_DISPLAY_CENTER = "41.31,65.28"
UVTOOLS_SUCCESS_EXIT = 1  # pinned UVtools 6.2.0 Program.Main returns 1 after normal command execution


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_cube_stl(path: Path) -> None:
    vertices = {
        "000": (0, 0, 0), "100": (10, 0, 0), "110": (10, 10, 0), "010": (0, 10, 0),
        "001": (0, 0, 10), "101": (10, 0, 10), "111": (10, 10, 10), "011": (0, 10, 10),
    }
    faces = [
        ("000", "110", "100"), ("000", "010", "110"),
        ("001", "101", "111"), ("001", "111", "011"),
        ("000", "100", "101"), ("000", "101", "001"),
        ("010", "011", "111"), ("010", "111", "110"),
        ("000", "001", "011"), ("000", "011", "010"),
        ("100", "110", "111"), ("100", "111", "101"),
    ]
    lines = ["solid workpiece-smoke"]
    for face in faces:
        lines.extend(["  facet normal 0 0 0", "    outer loop"])
        for key in face:
            x, y, z = vertices[key]
            lines.append(f"      vertex {x} {y} {z}")
        lines.extend(["    endloop", "  endfacet"])
    lines.append("endsolid workpiece-smoke")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def docker_command(entrypoint: str, args: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm",
        "-v", f"{WORK}:/work",
        "-v", f"{PROFILES}:/profiles:ro",
        "--entrypoint", entrypoint,
        IMAGE,
        *args,
    ]


def run_step(
    report: dict,
    name: str,
    entrypoint: str,
    args: list[str],
    *,
    expected_returncode: int = 0,
) -> bool:
    completed = subprocess.run(
        docker_command(entrypoint, args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = completed.stdout or ""
    report["steps"].append({
        "name": name,
        "returncode": completed.returncode,
        "expected_returncode": expected_returncode,
        "output_tail": output[-MAX_OUTPUT:],
    })
    return completed.returncode == expected_returncode


def require_file(report: dict, name: str) -> bool:
    path = WORK / name
    ok = path.is_file() and path.stat().st_size > 0
    report["files"][name] = (
        {"size": path.stat().st_size, "sha256": sha256(path)} if ok else None
    )
    if not ok:
        report["errors"].append(f"Missing or empty output: {name}")
    return ok


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    write_cube_stl(WORK / "source.stl")

    report: dict = {
        "schema": "workpiece-resin-toolchain-smoke-v2",
        "image": IMAGE,
        "recipe": "review-3mf-plus-effective-config",
        "uvtools_success_exit": UVTOOLS_SUCCESS_EXIT,
        "ok": False,
        "steps": [],
        "files": {},
        "errors": [],
    }

    project_ok = run_step(report, "review-recipe", "/opt/prusaslicer/prusa-slicer", [
        "--load", "/profiles/printers/elegoo-mars-2.ini",
        "--load", "/profiles/resins/elegoo-water-washable-grey.ini",
        "--load", "/profiles/quality/balanced-0p05-medium.ini",
        "--center", MARS2_DISPLAY_CENTER,
        "--rotate-x", "15", "--rotate-y", "0", "--rotate", "25",
        "--save", "/work/effective.ini",
        "--export-3mf", "--output", "/work/review.3mf", "/work/source.stl",
    ])
    project_ok = require_file(report, "review.3mf") and project_ok
    project_ok = require_file(report, "effective.ini") and project_ok

    slice_ok = False
    if project_ok:
        slice_ok = run_step(report, "intermediate-sl1", "/opt/prusaslicer/prusa-slicer", [
            "--load", "/work/effective.ini",
            "--dont-arrange",
            "--export-sla", "--output", "/work/production.sl1", "/work/review.3mf",
        ])
        slice_ok = require_file(report, "production.sl1") and slice_ok

    native_ok = False
    if slice_ok:
        native_ok = run_step(
            report,
            "native-ctb",
            "/opt/uvtools/UVtoolsCmd",
            ["convert", "/work/production.sl1", "CTB", "/work/production.ctb"],
            expected_returncode=UVTOOLS_SUCCESS_EXIT,
        )
        native_ok = require_file(report, "production.ctb") and native_ok

    metrics_ok = False
    if native_ok:
        base_ok = run_step(
            report,
            "native-base-properties",
            "/opt/uvtools/UVtoolsCmd",
            [
                "print-properties", "/work/production.ctb", "-n",
                "LayerCount", "PrintHeight", "BoundingRectangleMillimeters", "--no-progress",
            ],
            expected_returncode=UVTOOLS_SUCCESS_EXIT,
        )
        base_output = report["steps"][-1]["output_tail"]
        (WORK / "base-properties.txt").write_text(base_output, encoding="utf-8")
        layer_match = re.search(r"(?m)^LayerCount:\s*(\d+)\s*$", base_output)
        if not base_ok or layer_match is None:
            report["errors"].append("Unable to read LayerCount from UVtools base properties.")
        else:
            layer_count = int(layer_match.group(1))
            if not (1 <= layer_count <= 10_000):
                report["errors"].append(f"LayerCount out of smoke-test bounds: {layer_count}")
            else:
                layer_ok = run_step(
                    report,
                    "native-layer-properties",
                    "/opt/uvtools/UVtoolsCmd",
                    [
                        "print-properties", "/work/production.ctb", "-r", f"0:{layer_count - 1}",
                        "-n", "Area", "Volume", "--no-progress",
                    ],
                    expected_returncode=UVTOOLS_SUCCESS_EXIT,
                )
                layer_output = report["steps"][-1]["output_tail"]
                (WORK / "layer-properties.txt").write_text(layer_output, encoding="utf-8")
                metrics_ok = (
                    layer_ok
                    and "PrintHeight:" in base_output
                    and "BoundingRectangleMillimeters:" in base_output
                    and "# Layer: 0" in layer_output
                    and "Area:" in layer_output
                    and "Volume:" in layer_output
                )
                if not metrics_ok:
                    report["errors"].append("UVtools native metric properties are incomplete.")

        issues_ok = run_step(
            report,
            "native-issues",
            "/opt/uvtools/UVtoolsCmd",
            ["print-issues", "/work/production.ctb", "--no-progress"],
            expected_returncode=UVTOOLS_SUCCESS_EXIT,
        )
        (WORK / "issues.txt").write_text(report["steps"][-1]["output_tail"], encoding="utf-8")
        metrics_ok = metrics_ok and issues_ok

    report["ok"] = bool(project_ok and slice_ok and native_ok and metrics_ok)
    (WORK / "smoke-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Toolchain smoke report written: ok={report['ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
