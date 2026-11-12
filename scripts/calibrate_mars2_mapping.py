#!/usr/bin/env python3
"""Calibration aid: how does a plate's CTB raster relate to the PrusaSlicer bed layout?

`/v1/project` places instances in PrusaSlicer bed millimetres (lower-left origin) and
assumes the supported footprint is centred on the model centre. The Mars 2 profile sets
`display_mirror_x = 1`, so the CTB raster is a mirrored image of that layout; which axis
is mirrored and whether the pad grows symmetrically has not been measured. Run this once
on a plate from a real project ZIP:

    python3 scripts/calibrate_mars2_mapping.py project.json part-plate-01.ctb \\
        --uvtools /opt/uvtools/UVtoolsCmd

It measures the CTB's whole-print bounding rectangle with UVtools, computes the union of
the planned instance footprints in bed coordinates, and prints the residual under each
mirror hypothesis (none, mirror X, mirror Y, both). The hypothesis with residuals within
two display pixels is the bed-to-CTB mapping; a constant leftover offset is the pad/support
asymmetry around the model centre. Record the finding in the printer profile notes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import UVTOOLS_SUCCESS_EXIT  # noqa: E402
from app.uvtools_metrics import base_property_command, parse_native_bounding_rectangle  # noqa: E402


def planned_union(manifest: dict, plate_index: int) -> tuple[float, float, float, float]:
    footprint = manifest["part"]["supported_footprint_mm"]
    plate = next(p for p in manifest["plates"] if p["plate_index"] == plate_index)
    xs: list[float] = []
    ys: list[float] = []
    for item in plate["instances"]:
        w, d = (footprint["width"], footprint["depth"])
        if item["rotation_z_deg"] in (90, -90):
            w, d = d, w
        xs += [item["x_mm"] - w / 2, item["x_mm"] + w / 2]
        ys += [item["y_mm"] - d / 2, item["y_mm"] + d / 2]
    return min(xs), max(xs), min(ys), max(ys)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_json", type=Path)
    parser.add_argument("plate_ctb", type=Path)
    parser.add_argument("--plate", type=int, default=None, help="plate_index (default: parsed from the CTB name)")
    parser.add_argument("--uvtools", default=os.environ.get("UVTOOLS_CMD", "/opt/uvtools/UVtoolsCmd"))
    parser.add_argument("--pixels", type=float, default=2.0, help="tolerance in display pixels")
    args = parser.parse_args()

    manifest = json.loads(args.project_json.read_text("utf-8"))
    plate_index = args.plate or int(args.plate_ctb.stem.rsplit("-", 1)[-1])
    bed = manifest["layout"]["bed_mm"]
    bed_w, bed_d = float(bed["width"]), float(bed["depth"])

    result = subprocess.run(
        list(base_property_command(args.uvtools, str(args.plate_ctb))),
        check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    if result.returncode != UVTOOLS_SUCCESS_EXIT:
        print(result.stdout[-2000:], file=sys.stderr)
        return 2
    observed = parse_native_bounding_rectangle(result.stdout)
    obs = (observed.x_mm, observed.max_x_mm, observed.y_mm, observed.max_y_mm)

    min_x, max_x, min_y, max_y = planned_union(manifest, plate_index)
    hypotheses = {
        "none": (min_x, max_x, min_y, max_y),
        "mirror-x": (bed_w - max_x, bed_w - min_x, min_y, max_y),
        "mirror-y": (min_x, max_x, bed_d - max_y, bed_d - min_y),
        "mirror-xy": (bed_w - max_x, bed_w - min_x, bed_d - max_y, bed_d - min_y),
    }
    # Mars 2: 82.62 mm / 1620 px and 130.56 mm / 2560 px, both 0.051 mm
    tol = args.pixels * 0.051

    print(f"plate {plate_index}: observed CTB rectangle x=[{obs[0]:.3f}, {obs[1]:.3f}] y=[{obs[2]:.3f}, {obs[3]:.3f}] mm")
    print(f"planned union in bed coordinates   x=[{min_x:.3f}, {max_x:.3f}] y=[{min_y:.3f}, {max_y:.3f}] mm")
    print(f"tolerance {tol:.3f} mm ({args.pixels:g} px)")
    match = None
    for name, expected in hypotheses.items():
        residuals = [o - e for o, e in zip(obs, expected)]
        ok = all(abs(r) <= tol for r in residuals)
        shift = ((residuals[0] + residuals[1]) / 2, (residuals[2] + residuals[3]) / 2)
        print(f"  {name:9s} residuals min_x={residuals[0]:+.3f} max_x={residuals[1]:+.3f} "
              f"min_y={residuals[2]:+.3f} max_y={residuals[3]:+.3f}  centre shift=({shift[0]:+.3f}, {shift[1]:+.3f}) {'MATCH' if ok else ''}")
        if ok and match is None:
            match = name
    if match is None:
        print("no hypothesis matches within tolerance; the residual centre shift of the closest one is the footprint asymmetry")
        return 1
    print(f"bed-to-CTB mapping: {match}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
