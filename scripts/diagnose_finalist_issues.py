#!/usr/bin/env python3
"""Prints, for every orientation finalist of one STL, what UVtools reported (issue counts and the issue text), and the support lift (print height minus model height), so 'critical issues on every finalist' can be judged: are the islands on support tips?

Run in the service container (real PrusaSlicer + UVtools), like scripts/diagnose_project_slice.py:
    docker exec -i <container> python3 - STL < scripts/diagnose_finalist_issues.py
Read-only apart from the container's /tmp. No token, no network.
"""
import os
import pathlib
import sys

sys.path.insert(0, os.environ.get("RESIN_APP_ROOT") or (str(pathlib.Path(__file__).resolve().parent.parent) if "__file__" in globals() else "/app"))

from app import main as service_main  # noqa: E402
from app import orientation_adapter as adapter  # noqa: E402
from app.engine import critical_issue_count  # noqa: E402
from app.estimate import mesh_stats  # noqa: E402
from app.orientation_pipeline import build_proxy_orientation_plan  # noqa: E402
from app.engine import Orientation  # noqa: E402

original = adapter.slice_native
seen = []


def traced(source, **kwargs):
    artifact = original(source, **kwargs)
    o = kwargs["orientation"]
    mesh = mesh_stats(source, Orientation(o.x, o.y, o.z))
    seen.append((o, artifact.issue_summary, artifact.issue_text, mesh.dims_mm))
    return artifact


def main(path):
    data = pathlib.Path(path).read_bytes()
    registry = service_main._registry_snapshot()
    printer, resin, quality = registry.resolve_candidate("elegoo-mars-2", "elegoo-water-washable-grey", "balanced-0p05-medium")
    plan = build_proxy_orientation_plan(data)
    adapter.slice_native = traced
    result = adapter.execute_real_sliced_finalists(
        proxy_plan=plan, source_stl=data, original_name=pathlib.Path(path).name, printer=printer, resin=resin, quality=quality,
        prusa_bin=service_main.PRUSA_BIN, uvtools_cmd=service_main.UVTOOLS_CMD, slice_timeout=service_main.SLICE_TIMEOUT,
        uvtools_timeout=service_main.UVTOOLS_TIMEOUT, fallback_to_proxy_best=True,
    )
    print("selection:", result.selection, "| finalists sliced:", result.executed_finalist_count)
    for orientation, summary, text, dims in seen:
        print("=" * 70)
        print("orientation", (orientation.x, orientation.y, orientation.z), "| model dims (mm)", dims)
        print("issue summary:", summary, "| critical count:", critical_issue_count(summary))
        print("issue text:")
        print(text[:3000])


main(sys.argv[1] if "__file__" not in globals() else sys.argv[1])
