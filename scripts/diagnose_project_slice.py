#!/usr/bin/env python3
"""Runs the real /v1/project pipeline on one STL and prints what PrusaSlicer was given and said. No token, no network, nothing is stored outside the temporary directory it prints.

Usage (inside the service container, which has the real PrusaSlicer and UVtools):
    docker exec -i <container> python3 - STL QUANTITY < scripts/diagnose_project_slice.py
or from a checkout with the toolchain installed:
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent) if "__file__" in globals() else "/app")

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
    except Exception as error:  # diagnostic tool: show everything
        print("FAILED:", type(error).__name__, str(error)[:3000])
    print("kept temporary directories:", KEPT)


main(sys.argv if "__file__" in globals() else ["diagnose_project_slice.py", *sys.argv[1:]])
