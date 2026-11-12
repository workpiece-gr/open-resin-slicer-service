#!/usr/bin/env python3
"""Runs the real /v1/project pipeline on one STL and prints what PrusaSlicer was given and said. No token, no network, nothing is stored outside the temporary directory it prints.

Usage (inside the service container, which has the real PrusaSlicer and UVtools):
    docker exec -i <container> python3 - STL QUANTITY < scripts/diagnose_project_slice.py
To try modified code against the container's real toolchain without rebuilding the image, put a copy of the `app` directory in /tmp/x/app and set RESIN_APP_ROOT=/tmp/x (docker exec -e).
Or from a checkout with the toolchain installed:
    python3 scripts/diagnose_project_slice.py STL QUANTITY

For every plate slice it prints the exact PrusaSlicer command, its return code, the last 2000 characters of its output, the files it left in the (kept) temporary directory, the bed shape of the effective config, and the object/instance transforms of the materialized 3MF. It exists because the fake-PrusaSlicer unit tests cannot show why a real slice produced no archive.
"""
import contextlib
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

import os
sys.path.insert(0, os.environ.get("RESIN_APP_ROOT") or (str(pathlib.Path(__file__).resolve().parent.parent) if "__file__" in globals() else "/app"))

from app import main as service_main  # noqa: E402  (module constants: profile root, binaries, timeouts)
from app import materialized_plate_slice as plate_slice  # noqa: E402
from app import project  # noqa: E402

KEPT = []


@contextlib.contextmanager
def keep_directory(prefix="workpiece-resin-plate-"):
    path = tempfile.mkdtemp(prefix=prefix)
    KEPT.append(path)
    yield path


def describe_3mf(path):
    with zipfile.ZipFile(path) as archive:
        model = archive.read("3D/3dmodel.model").decode("utf-8", "replace")
    print("  3MF build items:")
    for item in re.findall(r"<item [^>]*>", model):
        print("   ", item[:200])
    configs = [name for name in zipfile.ZipFile(path).namelist() if name.endswith(".config")]
    print("  3MF config files:", configs)


original_run = plate_slice._run
original_materialize = project.materialize_prusa_project_instances


def describe_source(project_bytes):
    import io
    archive = zipfile.ZipFile(io.BytesIO(project_bytes))
    model = archive.read("3D/3dmodel.model").decode("utf-8", "replace")
    print("  SOURCE 3MF build items:", [item[:200] for item in re.findall(r"<item [^>]*>", model)])
    xs, ys, zs = [], [], []
    for name in archive.namelist():
        if name.startswith("3D/Objects/") and name.endswith(".model"):
            text = archive.read(name).decode("utf-8", "replace")
        elif name == "3D/3dmodel.model":
            text = model
        else:
            continue
        for x, y, z in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', text):
            xs.append(float(x)); ys.append(float(y)); zs.append(float(z))
    if xs:
        print("  SOURCE mesh vertex bounds (object coordinates): x %.3f..%.3f y %.3f..%.3f z %.3f..%.3f" % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))


original_finalists = project.execute_real_sliced_finalists


def traced_finalists(**kwargs):
    result = original_finalists(**kwargs)
    print("--- selected orientation:", getattr(getattr(result.selected_result, "artifact", None), "orientation", None))
    print("--- selected native (UVtools display) rectangle:", result.selected_native_rectangle)
    return result


def traced_materialize(project_bytes, *, source_project_sha256, source_display_envelope, placements):
    print("--- materialize: source_display_envelope", source_display_envelope)
    print("--- materialize: placements", [(p.instance_index, p.target_display_x_mm, p.target_display_y_mm, p.rotation_z_deg) for p in placements])
    describe_source(project_bytes)
    return original_materialize(project_bytes, source_project_sha256=source_project_sha256, source_display_envelope=source_display_envelope, placements=placements)


def traced_run(command, *, timeout):
    result = original_run(command, timeout=timeout)
    print("--- command:", " ".join(str(part) for part in command))
    print("--- return code:", result.returncode)
    print("--- output (last 2000):", result.stdout[-2000:])
    directory = pathlib.Path(KEPT[-1]) if KEPT else None
    if directory and directory.is_dir():
        print("--- directory:", sorted(f"{item.name} ({item.stat().st_size})" for item in directory.iterdir()))
        threemf = directory / "materialized.3mf"
        if threemf.is_file():
            describe_3mf(threemf)
        config = directory / "effective.ini"
        if config.is_file():
            for line in config.read_text(errors="replace").splitlines():
                if re.match(r"(bed_shape|display_width|display_height|display_orientation|sla_archive_format|printer_technology|max_print_height|printer_model|sla_material_settings_id)\b", line):
                    print("  config:", line[:200])
    return result


def main(argv):
    if len(argv) != 3:
        raise SystemExit("usage: diagnose_project_slice.py STL QUANTITY")
    data = pathlib.Path(argv[1]).read_bytes()
    plate_slice.tempfile = type("T", (), {"TemporaryDirectory": staticmethod(keep_directory)})
    plate_slice._run = traced_run
    project.materialize_prusa_project_instances = traced_materialize
    project.execute_real_sliced_finalists = traced_finalists
    registry = service_main._registry_snapshot()
    prepared = project.prepare_order_project(
        data, original_name=pathlib.Path(argv[1]).name, requested_quantity=int(argv[2]), registry=registry,
        printer_id="elegoo-mars-2", resin_id="elegoo-water-washable-grey", quality_id="balanced-0p05-medium",
    )
    try:
        result = project.build_order_project(
            prepared, prusa_bin=service_main.PRUSA_BIN, uvtools_cmd=service_main.UVTOOLS_CMD,
            slice_timeout=service_main.SLICE_TIMEOUT, uvtools_timeout=service_main.UVTOOLS_TIMEOUT,
        )
        print("OK", result.zip_filename, len(result.zip_bytes), "bytes")
        import io
        print("ZIP members:", [(i.filename, i.file_size) for i in zipfile.ZipFile(io.BytesIO(result.zip_bytes)).infolist()])
    except Exception as error:  # diagnostic tool: show everything
        print("FAILED:", type(error).__name__, str(error)[:3000])
    print("kept temporary directories:", KEPT)


main(sys.argv if "__file__" in globals() else ["diagnose_project_slice.py", *sys.argv[1:]])
