# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing (PrusaSlicer 2.9.7 + UVtools 7.1.2). It is isolated from the production FDM OrcaSlicer service and has two jobs:

- **`POST /v1/estimate` — per part.** Measures one STL in a proposed orientation for an instant quote: layers, print time, resin volume, footprint, UVtools issues. JSON only, nothing retained.
- **`POST /v1/project` — per order.** Orients the part automatically, packs the ordered quantity on as few ELEGOO Mars 2 plates as possible, slices every plate and returns a ZIP with one PrusaSlicer 3MF and one CTB per plate plus `project.json`. The workshop opens and reviews every plate project in PrusaSlicer before printing.

Neither route prices, and nothing is ever sent to a printer.

## How a part becomes a plate

```
STL -> PrusaSlicer review 3MF + effective config -> SLA slice (.sl1) -> UVtools convert/inspect/measure -> .ctb
```

`engine.slice_native()` runs that chain once per orientation. PrusaSlicer's CLI does not embed the print config in `--export-3mf` output, so the review 3MF and the effective config are always kept together; later slices load that exact pair with `--dont-arrange`.

`/v1/project` adds, in order (`app/project.py`):

1. **Orientation.** Geometry-proxy screening ranks ~30 candidate rotations (`orientation_*.py`); the top `finalist_limit` (default 5) are really sliced and ranked on UVtools metrics (layer area, volume, footprint, height), with islands, suction cups, resin traps, touching bounds and empty layers as hard blockers. If every finalist is blocked the best proxy orientation is used and `project.json` says so.
2. **Plate plan.** The winner's supported/padded footprint (UVtools bounding rectangle) is packed row-major on the 80 x 129 mm manufacturing envelope with 5 mm spacing and a 3 mm margin; a common 90 degree rotation is used when it fits more per plate (`plate.py`).
3. **Materialize.** The winner's review 3MF has one build item; it is cloned once per instance at the planned position, so every instance shares the winner's support points (`prusa_3mf_instances.py`).
4. **Slice each plate** with the retained effective config, convert to CTB, inspect and measure with UVtools (`materialized_plate_slice.py`).

Instance positions are PrusaSlicer bed millimetres from the lower-left corner, with the envelope centred in the 82.62 x 130.56 mm bed. The footprint is the measured cured rectangle (UVtools), converted from raster to bed millimetres (X mirrored, Y downward: measured on a real Mars 2 project, other mirror combinations are refused) and never assumed to be centred on the bed; instances whose footprint would leave the bed are refused before PrusaSlicer is called. `scripts/diagnose_project_slice.py` prints what PrusaSlicer was given and said for one STL and quantity.

## API

All POST routes require `Authorization: Bearer $WORKPIECE_RESIN_PROJECT_API_TOKEN`, an `.stl` upload up to `MAX_UPLOAD_BYTES` (30 MB) and the profile tuple `printer_profile` / `resin_profile` / `quality`, which must be listed in `profiles/compatibility.json` (today only `elegoo-mars-2` / `elegoo-water-washable-grey` / `balanced-0p05-medium`).

- `GET /health` — engine versions and availability, limits, `profiles_ready`, `project_api_enabled`
- `GET /source` — redirect to this repository (AGPL source offer)
- `GET /v1/profiles` — printers, resins, qualities and approved tuples
- `POST /v1/orientation/proxy` — geometry-only orientation screening, no slicing (`finalist_limit`)
- `POST /v1/candidate` — one direct slice in a proposed orientation; returns the review bundle ZIP (STL, 3MF, effective config, SL1, CTB, UVtools issues, manifest)
- `POST /v1/estimate` — per-part measurements, JSON
- `POST /v1/project` — per-order plates, ZIP

### `POST /v1/estimate`

Optional `rotate_x` / `rotate_y` / `rotate_z` degrees. Response: `engine`, `profile` (tuple, `layerHeightMm`, exposure and motion values actually used), `summary` (`layer_count`, `bottom_layer_count`, `print_time_seconds`, `model_volume_ml`, `support_volume_ml`, `total_resin_ml`, `bounding_box_mm`, `print_footprint_mm`, `fits_envelope`, `orientation_deg`), `time_breakdown`, `issues`, `warnings`, `hashes`.

`print_time_seconds = bottom_layers x (initial_exposure + lift + retract + rest) + remaining_layers x (exposure + lift + retract + rest)` from the exact effective config and the resin profile's `motion_contract`; the exposure values are calibration seeds, so treat the time as an estimate. `model_volume_ml` is the closed-mesh integral; `total_resin_ml` is the UVtools cured volume; `support_volume_ml` is the difference (includes the pad).

Errors: `{"detail": {"code", "message"}}` with `INVALID_STL` (400/413/422), `UNSUPPORTED_PROFILE`, `INVALID_ORIENTATION`, `OUTSIDE_ENVELOPE` (bare model exceeds 80 x 129 x 150 mm, nothing sliced), `SLICE_FAILED` (422), `SLICE_TIMEOUT` (504), `BUSY` (429, `Retry-After`). Waits up to `ESTIMATE_QUEUE_TIMEOUT_SECONDS` (60) for the slice slot.

### `POST /v1/project`

Fields: `requested_quantity` 1–`MAX_PROJECT_QUANTITY` (100), optional `finalist_limit` 1–8 (default 5). Orientation is automatic; there are no rotate fields. Response `200`: `application/zip` with headers `X-Workpiece-Plate-Count`, `X-Workpiece-Instance-Count`, `X-Workpiece-Source-SHA256`, containing

- `<stem>-plate-NN.3mf` — open in PrusaSlicer with `<stem>-effective.ini` loaded to review or edit the plate;
- `<stem>-plate-NN.ctb` — the sliced plate as generated;
- `<stem>-plate-NN-uvtools-issues.txt` — UVtools issue report for that plate;
- `<stem>-effective.ini` — the exact PrusaSlicer config the plates were sliced with;
- `project.json` — `source` (name, sha256, quantity), `engine`, `profile` (cycle values), `orientation` (`deg`, `selection` = `sliced-winner` | `proxy-best-fallback`, `finalists_sliced`), `part` (model volume, bbox, supported footprint, height), `layout` (bed, envelope offset, spacing, margin, grid, `plate_count`), `plates[]` (files with sha256, `instances[]` with `x_mm`/`y_mm`/`rotation_z_deg`, `layer_count`, `print_time_seconds` by the same formula as `/v1/estimate`, `resin_ml`, `issues`, `observed_footprint_mm` from UVtools), `totals`, `warnings`.

Errors use the same shape: `INVALID_QUANTITY`, `INVALID_FINALIST_LIMIT`, `INVALID_STL`, `UNSUPPORTED_PROFILE`, `ORIENTATION_FAILED`, `DOES_NOT_FIT` (the supported footprint does not fit one plate even rotated), `MATERIALIZE_FAILED`, `SLICE_FAILED`, `SLICE_TIMEOUT` (504), `BUSY` (429). A project is a background job: up to `finalist_limit` + `plate_count` real slices, each bounded by `SLICE_TIMEOUT_SECONDS` (240) + `UVTOOLS_TIMEOUT_SECONDS` (120), and it waits up to `PROJECT_QUEUE_TIMEOUT_SECONDS` (600) for the single slice slot it shares with `/v1/estimate`. Callers should allow 30–60 minutes for a 15-piece order.

Example:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -F file=@part.stl -F printer_profile=elegoo-mars-2 \
  -F resin_profile=elegoo-water-washable-grey -F quality=balanced-0p05-medium \
  -F requested_quantity=15 -o part-project.zip http://127.0.0.1:18084/v1/project
```

## Mars 2 profile

`profiles/printers/elegoo-mars-2.*`: 1620 x 2560 mono LCD, 82.62 x 130.56 mm display (UVtools Mars 2 Pro basis), X mirroring, CTB v4, 405 nm, Z capped at ELEGOO's published 150 mm. The conservative manufacturing envelope is 80 x 129 mm. `elegoo-water-washable-grey` at 0.05 mm starts at 2.75 s normal / 30 s initial exposure; these are seeds until a calibration print on the real printer. `docs/MARS2_ACCEPTANCE.md` lists the calibration sequence.

## Pinned engines and container

- PrusaSlicer `2.9.7`, source commit `450d70dd81c9b8990bc6bc8e690e31f82ddceb11` (tag `version_2.9.7`)
- UVtools `7.1.2`, Linux x64 ZIP SHA-256 `4d06ec7e86043c409d81126311c5c8e6f4629eb5bb9d3f092d8ec2401d157c1c`

`Dockerfile.toolchain` builds both from those pins (1–2 h). `Dockerfile` builds the service on top of that image (`--build-arg TOOLCHAIN_IMAGE=...`, optional `SERVICE_COMMIT`). The toolchain workflow verifies the toolchain image builds and runs a Mars 2 smoke slice; the service CI runs the unit tests on every push and, on dispatch with a toolchain image, a real estimate + two-plate project.

Environment: `WORKPIECE_RESIN_PROJECT_API_TOKEN`, `PROFILE_ROOT`, `PRUSA_SLICER_BIN`, `UVTOOLS_CMD`, `MAX_UPLOAD_BYTES`, `SLICE_TIMEOUT_SECONDS`, `UVTOOLS_TIMEOUT_SECONDS`, `ESTIMATE_QUEUE_TIMEOUT_SECONDS`, `PROJECT_QUEUE_TIMEOUT_SECONDS`, `MAX_PROJECT_QUANTITY`, `MAX_CONCURRENT_SLICES` (1), `MAX_CONCURRENT_PROXY_JOBS` (1), `WORKPIECE_RESIN_SERVICE_COMMIT`, `SOURCE_CODE_URL`.

## Local unit tests

```bash
python -m pytest -q
```

Unit tests mock the external engines. With the toolchain installed, `WORKPIECE_RESIN_INTEGRATION=1 python -m pytest -q tests/test_estimate.py tests/test_project.py` additionally slices a real 20 mm cube through `/v1/estimate` and a real multi-plate order through `/v1/project`.

## License/source boundary

This service is intended to remain public under AGPL-3.0-or-later, like Workpiece's existing open FDM slicer service. The deployed `/source` endpoint must point to the exact public source used by the running service.
