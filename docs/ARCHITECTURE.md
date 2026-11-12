# Resin service architecture

## Isolation boundary

The FDM OrcaSlicer service serves the live site. Resin has its own container, port, bearer token, resource limits and profile set, so a resin failure or dependency upgrade cannot alter FDM behaviour.

## Why PrusaSlicer + UVtools

PrusaSlicer provides the pinned SLA/MSLA slicer, SLA supports and the editable 3MF project the workshop reviews. UVtools converts the SL1 archive to the printer-native CTB, reports print issues and measures the native file (layer count, cured volume, bounding rectangle, height). Neither tool is modified; `Dockerfile.toolchain` builds both from pinned sources.

The CLI does not embed the print config in an exported 3MF, so every slice keeps the review 3MF and the effective config together, and every later slice loads that exact pair with `--dont-arrange`.

## Two entry points

| | `/v1/estimate` | `/v1/project` |
|---|---|---|
| when | instant quote, per part | after an order, per ordered part |
| orientation | caller's proposal (default none) | automatic |
| slices | 1 | `finalist_limit` + plates |
| output | JSON measurements | ZIP: per-plate 3MF + CTB, effective config, `project.json` |
| caller waits | seconds to a few minutes | a background job; tens of minutes |

Both share `engine.slice_native`, `stl.validate_stl_bytes`, `profiles.ProfileRegistry`, `uvtools_metrics` and the print-time formula in `estimate.py`, so the quote and the order project report the same time for the same layer count.

## Order project pipeline (`app/project.py`)

1. **Prepare** (cheap, before the slice slot): STL structure, profile tuple from `profiles/compatibility.json`, manufacturing envelope and bed size from the printer profile.
2. **Orientation** (`orientation_pipeline`, `orientation_adapter`): proxy screening of ~30 rotations on the mesh alone, then real slices of the finalists ranked on UVtools metrics. Critical UVtools findings are hard blockers in ranking; one blocked orientation does not abort the others. If all are blocked, the best proxy orientation is used with a warning in `project.json` — the reviewer decides.
3. **Plate plan** (`plate.plan_printer_profile_instances`): deterministic row-major grid of the winner's supported/padded footprint on the 80 x 129 mm envelope, 5 mm spacing, 3 mm margin, common 90 degree rotation when it increases capacity.
4. **Materialize** (`prusa_3mf_instances.materialize_prusa_project_instances`): clone the winner's single build item once per instance; object geometry, resolved metadata and SLA support points are copied unchanged, so all instances share the winner's supports.
5. **Slice each plate** (`materialized_plate_slice.slice_materialized_plate_native`): PrusaSlicer with the retained config and `--dont-arrange`, UVtools convert, `print-issues` and metrics. Critical issues are reported per plate, not refused.
6. **ZIP + `project.json`** with the SHA-256 of every file it contains.

## Coordinates

Plate positions are PrusaSlicer bed millimetres (lower-left origin, `bed_shape` 82.62 x 130.56). The 80 x 129 mm envelope is centred in the bed (offset 1.31 / 0.78 mm). The winner's review 3MF keeps wherever PrusaSlicer put the object (on the Mars 2 test part the item sat at (21.31, 50.28), not at the bed centre (41.31, 65.28)), so the footprint is NOT assumed to be centred on the bed: the supported footprint (UVtools' whole-print rectangle, which includes pad and supports) is converted to bed millimetres by `bed_envelope_from_native_rectangle` (raster origin top-left, Y down, `display_mirror_x = 1` mirrors X: `display_x = bed_width - bed_x`, `display_y = bed_depth - bed_y`; measured 2026-11-12, only that combination is accepted) and every instance is moved so that rectangle's centre lands on the planned target. Before a plate is written, `_require_footprints_inside_bed` refuses (MATERIALIZE_FAILED, with the numbers) any instance whose footprint leaves the bed, because PrusaSlicer then exits 0 with "Nothing to print" and no archive. `project.json` records the planned positions and each plate's observed CTB rectangle; `scripts/calibrate_mars2_mapping.py` compares them. The reviewer sees the real layout in PrusaSlicer either way.

## Concurrency and limits

One slice slot (`MAX_CONCURRENT_SLICES`, 1–4) is shared by `/v1/candidate`, `/v1/estimate` and `/v1/project`; `/v1/orientation/proxy` has its own CPU slot. Estimates wait up to 60 s for the slot and then answer `BUSY`; projects wait up to 600 s. Each external process is bounded by `SLICE_TIMEOUT_SECONDS` / `UVTOOLS_TIMEOUT_SECONDS`. Uploads are capped by `MAX_UPLOAD_BYTES`; proxy screening by a triangle/layer work budget; quantities by `MAX_PROJECT_QUANTITY`.

## Profiles

`profiles/printers`, `profiles/resins`, `profiles/quality` hold PrusaSlicer `.ini` files with a JSON sidecar (ids, display geometry, envelope, motion values). A tuple must appear in `compatibility.json` to be sliced. The registry also understands an optional `manufacturing_envelope_coordinate_mapping: validated` + rigid `manufacturing_to_display_transform` on a printer (`coordinate_mapping.py`); the project path does not need it, and no profile sets it today.

## Mars 2 status

Profile seeded from ELEGOO's spec and UVtools' Mars 2 Pro profile; exposure values are manufacturer midpoints. Still to do on the real machine: build the toolchain image on the host, run one project, measure the bed-to-CTB mapping with the calibration script, print a calibration piece, calibrate exposure. `docs/MARS2_ACCEPTANCE.md` has the sequence.

## Website integration

Server to server only, never from browser JavaScript. The website stores the estimate JSON with the quote and the project ZIP with the order; the workshop downloads the ZIP, opens each plate 3MF in PrusaSlicer, reviews or edits it, and prints the CTB (or re-slices after editing). Nothing in this repository sends a job to a printer.
