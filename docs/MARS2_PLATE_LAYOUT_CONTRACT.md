# Mars 2 deterministic plate-layout contract

Status: CP2 implementation checkpoint. This is not production authorization and does not change the candidate/production profile gates.

## Purpose

Quantity must map to explicit physical plates before the resin slicer can create authoritative review projects and printer-native files. The service therefore treats physical plate layout as a deterministic manufacturing input rather than a UI-only convenience.

## Contract

`app/plate.py` accepts the final supported-part XY envelope, requested quantity, validated printable plate envelope, edge margin, and inter-part spacing. It returns an immutable plan with:

- stable global instance numbers;
- explicit target XY centre coordinates for every supported-part envelope;
- a single deterministic Z rotation (0 or 90 degrees);
- deterministic row-major ordering;
- explicit plate numbers and per-plate instance lists;
- capacity and plate-count metadata.

A 90 degree Z rotation is selected only when it strictly increases per-plate capacity. Capacity ties retain 0 degrees so repeated requests do not change layout due to arbitrary orientation choices.

The current algorithm is deliberately conservative: it packs rectangular supported-part envelopes and uses one common 0/90-degree orientation. It does not attempt mixed-orientation or interlocking/nesting optimization.

## Authority rule

One physical plate must ultimately produce exactly one retained review project and one printer-native file. A multi-plate order is therefore represented as multiple auditable physical-plate artifacts under one order-level manifest, not as one ambiguous machine file.

The CP2 manifest schema is `workpiece-resin-plate-plan-v1` and records the complete deterministic placement plan before slicer integration.

## Coordinate and transform semantics

Coordinates are millimetres from the lower-left physical plate origin. Each placement stores the target centre of the final supported-part XY envelope.

The stored `x_mm` / `y_mm` values are **not** raw STL- or mesh-origin translations. STL origins can be arbitrary. The slicer integration must determine the actual oriented/supported envelope in the project coordinate frame, derive the object transform required to move that envelope centre to the planned target centre, and then verify the post-transform bounds before exporting the authoritative 3MF.

This distinction is required so an off-centre source origin cannot silently shift an otherwise valid plan outside the intended grid or manufacturing envelope.

## Manufacturing envelope rule

The planner's `plate_width_mm` and `plate_depth_mm` inputs must represent a validated printable/manufacturing envelope. Raw LCD/display dimensions must not be substituted automatically.

The current CP1 Mars 2 candidate profile records both 82.62 × 130.56 mm display dimensions and ELEGOO's stated 80 × 129 × 150 mm build volume. That discrepancy must be resolved conservatively before plate-layout integration becomes production authority; CP2 must not assume the larger display dimensions are printable merely because they are present in the slicer profile.

## Safety boundaries

This checkpoint intentionally does not guess support geometry or infer its production envelope from the naked STL. The planner must receive the final supported-part footprint once support generation is available in the authoritative path. It also does not promote any Mars 2 profile to production-ready status.

The planner also does not prove that support/pad generation is position-independent. During integration, the exact per-plate 3MF must be revalidated after supports/pads and transforms are materialized; if the resulting envelope differs from the planning envelope, the plate must fail closed and be replanned.

## Next integration step

After CP1 acceptance is stable:

1. resolve the validated Mars 2 printable XY envelope;
2. obtain the oriented, supported-part envelope and its centre in project coordinates;
3. run the deterministic planner;
4. derive explicit object transforms from the planned target centres rather than treating them as raw translations;
5. create one review 3MF per physical plate;
6. verify final supported/padded bounds and collisions in each generated project;
7. slice that exact retained 3MF to SL1, convert to CTB, and retain all per-plate hashes under an order-level manifest.

Each artifact manifest must include the CP2 plate index, target placement data, actual applied transforms, and resulting 3MF / CTB hashes.
