# ELEGOO Mars 2 acceptance plan

Status: **source profile created; physical acceptance not started**.

## Known machine contract

- Printer: ELEGOO Mars 2 (not Mars 2 Pro)
- Technology: 405 nm MSLA
- LCD: 1620 x 2560 monochrome
- XY nominal resolution: about 0.05 mm
- Workpiece Z safety cap: 150 mm
- Candidate native format: CTB v4 via UVtools
- Display mapping seed: 82.62 x 130.56 mm, mirror X

The display/CTB/motion seed is derived from UVtools' Mars 2 Pro profile because Mars 2 and Mars 2 Pro share the mono LCD/exposure behavior. Workpiece does **not** inherit the Pro's 160 mm Z height; the service caps the Mars 2 at ELEGOO's currently published 150 mm build height.

## Resin family

ELEGOO Water Washable Resin at 0.05 mm. ELEGOO's published settings vary by color. Typical non-black colors are approximately 2.5-3.0 s normal exposure with 25-35 s bottom exposure; black is approximately 3.0-3.5 s normal and 30-35 s bottom. These are calibration ranges, not Workpiece production authority.

The first selected color is **Grey / Ceramic Grey**. The candidate profile `elegoo-water-washable-grey` uses the midpoint of ELEGOO's published Mars 2 / Mars 2 Pro range as its starting seed: **2.75 s normal exposure and 30 s initial exposure at 0.05 mm**. It is candidate-ready only and must be calibrated on the physical printer before production promotion.

## CP1 acceptance sequence

1. Build the pinned Docker image and verify `/health` reports both engines.
2. Use the candidate tuple `elegoo-mars-2 + elegoo-water-washable-grey + balanced-0p05-medium`; keep production disabled.
3. Slice a small benign calibration STL through `/v1/candidate`.
4. Verify the immutable source STL, oriented review 3MF, merged effective SLA config, SL1 and CTB are all present and match their manifest SHA-256 values. Confirm the bundled source bytes match the submitted STL and the manifest pins the exact requested profiles, orientation and engine versions.
5. Treat the retained review 3MF plus retained effective `.ini` as the slicing recipe: inspect the 3MF for oriented geometry and inspect the effective config for the exact resolved SLA settings. Do not assume the CLI-exported 3MF embeds Prusa configuration.
6. Open the CTB in UVtools desktop and verify resolution, physical dimensions, mirror/orientation, layer height, exposure, bottom exposure, lift/retract values, materialized support behavior and issue report.
7. Compare the generated file with an equivalent ChiTuBox slice for the same STL/settings.
8. Put only the reviewed candidate on USB and confirm the Mars 2 recognizes the file and previews it correctly before starting exposure.
9. Print a small calibration object. Record temperature, resin bottle/color/batch if available, exposure settings, measured dimensions, support behavior and print defects.
10. Calibrate exposure rather than blindly accepting the manufacturer midpoint.
11. Repeat until a documented stable profile exists.
12. Only then promote the exact printer/resin/quality tuple to `production_ready` and `production_combinations`.

## Fail-closed rules

- No substitution of grey settings for another resin color without its own candidate profile.
- No generic resin production profile.
- No Mars 2 Pro 160 mm Z assumption.
- No assumption that the CLI review 3MF is a self-contained Prusa configuration project.
- No printer-native artifact may remain valid after either the retained review 3MF or retained effective config changes.
- No production endpoint before physical acceptance.
- No automatic sending to the printer.
- No silent ignoring of UVtools critical issues in production.
