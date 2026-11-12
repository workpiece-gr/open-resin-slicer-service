# Upstream research / pin record

Recorded 2026-09-04 for the initial Workpiece resin-service scaffold; pins moved to PrusaSlicer 2.9.7 and UVtools 7.1.2 on 2026-11-12, before the first toolchain image build.

## PrusaSlicer

- Repository: https://github.com/prusa3d/PrusaSlicer
- Stable version selected: 2.9.7 (the 3.0.0 alphas are not used)
- Annotated tag: `version_2.9.7` (tag object `4b8477da3eee0254e4ecf57212a9aa89f68e0c10`)
- Exact source commit: `450d70dd81c9b8990bc6bc8e690e31f82ddceb11`
- 2.9.6 -> 2.9.7 is a seven-commit security/maintenance release (3MF negative bed index, placeholder `digits()` overflow, downloader hardening, libbgcode bump, FFF-only `used_filament_compensation`). No SLA, SL1 export, `--dont-arrange`, supports/pad or CLI change; the `deps/+GMP` mirror patch in `Dockerfile.toolchain` still applies.
- License: GNU AGPL v3
- Relevant CLI contract: `--export-sla`, repeated `--load`, `--rotate-x`, `--rotate-y`, `--rotate`, and SLA support options including `--supports-enable`.
- Linux production plan: build the pinned commit from source using Prusa's dependency bundle rather than depending on an unpinned distribution package/Flatpak runtime.

PrusaSlicer can slice mSLA layers and supports third-party/custom printers, but it does not currently provide native Elegoo GOO output for all modern Elegoo machines. Do not treat `SL1` as the final machine file for an Elegoo printer.

## UVtools

- Repository: https://github.com/sn4k3/UVtools
- Version selected: 7.1.2 (major upgrade from 6.2.0)
- Linux x64 ZIP: `UVtools_linux-x64_v7.1.2.zip` (self-contained .NET 10; needs `libicu74` and `zlib1g` on ubuntu:24.04)
- SHA-256: `4d06ec7e86043c409d81126311c5c8e6f4629eb5bb9d3f092d8ec2401d157c1c`
- License: GNU AGPL v3
- Used for explicit `SL1 -> native` conversion, `print-issues` inspection and `print-properties` metrics.
- 7.x CLI contract verified against the 7.1.2 Linux build and its bundled demo file (`UVtools_demo_file.sl1s`):
  - `UVtoolsCmd` now exits 0 after a normal command (7.0.0 fixed "it always return 1 exit code"); 1 is a usage/parse error and 255 a runtime error. `app.engine.UVTOOLS_SUCCESS_EXIT` is 0.
  - The global `--no-progress` option is accepted only before the subcommand (`UVtoolsCmd --no-progress print-issues file.ctb`); after the subcommand it is "Unrecognized command or argument" for `print-issues`.
  - `convert <in> ctb <out>` fails: the bare extension is shared by the Chitubox and CTBEncrypted encoders (`goo` by Goo and GooV5). Profiles declare `uvtools_target` as `auto` or a strict encoder name; both honour `FILEFORMAT_xxx`/`FILEVERSION_x` from `printer_notes`.
  - With `auto`, UVtools ignores the output-file argument and writes `<input stem>.<ext>` beside the input; the engine derives the native path from the intermediate `production.sl1` so both agree.
  - `print-issues` output (`Issues: N` then `Type, layers, area, {X=,Y=,Width=,Height=}` rows, `MainIssue.IssueType` names) and `print-properties` output (`Name: value`, `# Layer: N` sections) are unchanged from 6.x.
  - New optional `print-issues --group-islands` (7.1.0) is not used; default detection is unchanged.
- UVtools documents support for SL1/SL1S, CTB and Elegoo GOO among many MSLA formats.

## Known Elegoo evidence

UVtools publishes PrusaSlicer profiles for multiple Elegoo models (for example Mars 2 Pro and Saturn 2), demonstrating the architecture is practical. Those profiles are reference material only. Workpiece must create/validate the profile for the exact physical printer before production use.

## Important limitation

PrusaSlicer CLI can apply explicit orientation transforms and generate SLA supports when configured, but stock CLI does not provide the same automatic orientation workflow as the GUI. Workpiece therefore implements orientation itself (geometry screening, then real slices of the finalists ranked on UVtools metrics) and lets `/v1/estimate` take an explicit orientation proposal.
