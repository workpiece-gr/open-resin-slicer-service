# Resin service architecture

## Isolation boundary

The FDM OrcaSlicer service is already part of the Workpiece production-authority chain. Resin remains experimental. The resin stack therefore gets its own container, deployment domain, bearer token, resource limits and profile set. Failure or dependency upgrades in resin must not alter FDM behavior.

## Why PrusaSlicer + UVtools

PrusaSlicer has a mature SLA/MSLA slicing pipeline and a headless CLI with `--export-sla`. It also exposes explicit LCD geometry/mirroring configuration. However, third-party resin printers use multiple native file formats, and current PrusaSlicer does not natively support every modern Elegoo GOO target. UVtools is the explicit conversion/inspection layer and supports SL1 plus CTB and GOO among many formats.

## Orientation boundary

PrusaSlicer CLI can apply explicit X/Y/Z rotations, but Workpiece must not claim that stock CLI performs automatic resin orientation. The initial contract therefore accepts an explicit orientation proposal and records it in provenance. A future deterministic orientation search can be added server-side and validated independently.

## Validation gates

A production-ready machine profile requires at minimum:

1. exact build volume;
2. exact LCD physical dimensions;
3. exact pixel resolution;
4. display orientation and X/Y mirror behavior;
5. native target format/version;
6. confirmed lift/retract capabilities;
7. a known-good calibration/native file opened on the physical printer.

A production-ready resin profile requires exposure calibration on that exact printer/resin combination. Published/community values are starting points, not Workpiece authority.

## Acceptance plan

1. Build the pinned container and verify `/health` identifies both engines.
2. Add the exact workshop printer profile, initially `production_ready=false`.
3. Add one exact resin and draft/balanced/fine profiles, also non-production.
4. Slice a deterministic cube and calibration geometry.
5. Open the generated native file in UVtools and the printer/vendor viewer where available.
6. Compare layer count, dimensions, mirroring, exposure and lift/retract values.
7. Run a dry printer load check without exposure if supported.
8. Print small calibration pieces and measure XY/Z dimensions.
9. Calibrate exposure and supports.
10. Only then mark that exact compatibility tuple production-ready.

## Website integration later

The website should call this service server-to-server, never directly from anonymous browser JavaScript. Workpiece should store:

- immutable original STL;
- submitted/proposed orientation;
- printer/resin/quality profile IDs + hashes;
- PrusaSlicer and UVtools versions;
- intermediate SL1 SHA-256;
- native file SHA-256;
- UVtools issue summary;
- exact native production file.

A human workshop acknowledgment remains required before payment/production during controlled launch.
