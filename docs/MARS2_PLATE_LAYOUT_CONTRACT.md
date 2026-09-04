# Mars 2 deterministic plate-layout contract

Status: CP2 implementation checkpoint. This is not production authorization and does not change the candidate/production profile gates.

## Purpose

Quantity must map to explicit physical plates before the resin slicer can create authoritative review projects and printer-native files. The service therefore treats physical plate layout as a deterministic manufacturing input rather than a UI-only convenience.

## Contract

`app/plate.py` accepts the supported-part XY envelope, requested quantity, physical plate envelope, edge margin, and inter-part spacing. It returns an immutable plan with:

- stable global instance numbers;
- explicit XY centre coordinates for every instance;
- a single deterministic Z rotation (0 or 90 degrees);
- deterministic row-major ordering;
- explicit plate numbers and per-plate instance lists;
- capacity and plate-count metadata.

A 90 degree Z rotation is selected only when it strictly increases per-plate capacity. Capacity ties retain 0 degrees so repeated requests do not change layout due to arbitrary orientation choices.

## Authority rule

One physical plate must ultimately produce exactly one retained review project and one printer-native file. A multi-plate order is therefore represented as multiple auditable physical-plate artifacts under one order-level manifest, not as one ambiguous machine file.

The CP2 manifest schema is `workpiece-resin-plate-plan-v1` and records the complete deterministic placement plan before slicer integration.

## Coordinate system

Coordinates are millimetres from the lower-left physical plate origin. Each placement stores the instance centre. Edge margin and inter-part spacing are explicit inputs and therefore reproducible.

## Safety boundaries

This checkpoint intentionally does not guess support geometry or infer its envelope from the raw STL. The planner must receive the final supported-part footprint once support generation is available in the authoritative path. It also does not promote any Mars 2 profile to production-ready status.

## Next integration step

Wire the validated Mars 2 physical plate dimensions and the supported-part XY envelope into the planner, then generate one review 3MF / intermediate SLA / native CTB chain per returned physical plate. Each artifact manifest must include the CP2 plate index and placement transforms.
