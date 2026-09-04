# Upstream research / pin record

Recorded 2026-09-04 for the initial Workpiece resin-service scaffold.

## PrusaSlicer

- Repository: https://github.com/prusa3d/PrusaSlicer
- Stable version selected: 2.9.6
- Annotated tag: `version_2.9.6`
- Exact source commit: `b028299c770b8380ee81c921a2867d522f288123`
- License: GNU AGPL v3
- Relevant CLI contract: `--export-sla`, repeated `--load`, `--rotate-x`, `--rotate-y`, `--rotate`, and SLA support options including `--supports-enable`.
- Linux production plan: build the pinned commit from source using Prusa's dependency bundle rather than depending on an unpinned distribution package/Flatpak runtime.

PrusaSlicer can slice mSLA layers and supports third-party/custom printers, but it does not currently provide native Elegoo GOO output for all modern Elegoo machines. Do not treat `SL1` as the final machine file for an Elegoo printer.

## UVtools

- Repository: https://github.com/sn4k3/UVtools
- Version selected: 6.2.0
- Linux x64 ZIP: `UVtools_linux-x64_v6.2.0.zip`
- SHA-256: `cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f`
- License: GNU AGPL v3
- Used for explicit `SL1 -> native` conversion and `print-issues` inspection.
- UVtools documents support for SL1/SL1S, CTB and Elegoo GOO among many MSLA formats.

## Known Elegoo evidence

UVtools publishes PrusaSlicer profiles for multiple Elegoo models (for example Mars 2 Pro and Saturn 2), demonstrating the architecture is practical. Those profiles are reference material only. Workpiece must create/validate the profile for the exact physical printer before production use.

## Important limitation

PrusaSlicer CLI can apply explicit orientation transforms and generate SLA supports when configured, but stock CLI does not provide the same automatic orientation workflow as the GUI. Workpiece therefore records an explicit orientation proposal for the first implementation rather than claiming automatic orientation authority.
