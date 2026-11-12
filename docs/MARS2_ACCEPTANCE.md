# ELEGOO Mars 2 acceptance plan

Status: **source profile created; toolchain image not yet built on the host; physical acceptance not started**.

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

## Acceptance sequence

1. Build the toolchain image from `Dockerfile.toolchain` on the host, then the service image; verify `/health` reports both engines available.
2. Use the tuple `elegoo-mars-2 + elegoo-water-washable-grey + balanced-0p05-medium`.
3. Run `/v1/estimate` and one `/v1/project` (a few pieces of a small part) and open each plate 3MF in PrusaSlicer with the bundled effective config.
4. Run `scripts/calibrate_mars2_mapping.py` on plate 1 to establish the bed-to-CTB mirror mapping and the footprint asymmetry; note the result in the printer profile.
5. Open a plate CTB in UVtools desktop and verify resolution, physical dimensions, mirror/orientation, layer height, exposure, bottom exposure, lift/retract values and the issue report.
6. Compare with an equivalent ChiTuBox slice of the same STL/settings.
7. Put the reviewed CTB on USB and confirm the Mars 2 recognizes and previews it before starting exposure.
8. Print a small calibration object. Record temperature, resin bottle/color/batch, exposure settings, measured dimensions, support behaviour and defects.
9. Calibrate exposure rather than accepting the manufacturer midpoint; update the resin `.ini` and JSON seeds.
10. Repeat until a documented stable profile exists.

## Rules

- No substitution of grey settings for another resin color without its own profile.
- No generic resin profile.
- No Mars 2 Pro 160 mm Z assumption.
- Every plate project is opened and reviewed by a person before printing.
- No automatic sending to the printer.
