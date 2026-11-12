# Printer profiles

A real printer profile consists of:

- `<id>.json` — safe Workpiece metadata and native-format declaration. `uvtools_target` must be `auto` (UVtools reads `FILEFORMAT_xxx`/`FILEVERSION_x` from the INI `printer_notes`) or a strict UVtools encoder name such as `Chitubox`; bare extensions like `ctb`/`goo` are refused because UVtools 7 shares them between encoders;
- `<id>.ini` — pinned PrusaSlicer SLA printer configuration.

Do not enable a profile until screen dimensions/resolution, mirroring/orientation, Z height and native output format have been checked against the exact physical printer and a known calibration file.
