# Mars 2 multi-plate order manifest contract

Status: stacked CP2 checkpoint. This is a pure authority/provenance layer and does not enable production or call PrusaSlicer.

## Purpose

A quantity request can span multiple physical Mars plates. Workpiece therefore needs one umbrella manifest that binds the immutable source request and deterministic plate plan to every retained per-plate manufacturing artifact.

The schema is `workpiece-resin-order-manifest-v1`.

## Required authority chain

The order manifest records:

- original retained STL filename and SHA-256;
- requested quantity;
- final common orientation;
- exact printer, resin, and quality profile IDs;
- pinned PrusaSlicer and UVtools versions;
- the complete deterministic physical plate plan;
- one artifact record for every physical plate, in plate-index order;
- exact review 3MF, intermediate SL1, and printer-native CTB hashes for each plate;
- UVtools issue counts for each plate.

The builder fails closed if requested quantity differs from the plate plan, if a physical plate is missing or duplicated, or if retained artifact names/hashes are malformed.

## Review rule

Each printer-native plate file is valid only for the exact retained review 3MF hash recorded for that physical plate. Editing any retained review 3MF invalidates that plate's downstream SL1/CTB and requires the umbrella order manifest to be regenerated.

This preserves the existing CP1 rule that the retained 3MF is the human-review authority and the native CTB is downstream of that exact project.

## Manufacturing-envelope boundary

Plate packing remains based on a conservative validated manufacturing envelope, not automatically on raw LCD dimensions. ELEGOO advertises the Mars 2 build volume as 129 x 80 x 150 mm, while the ELEGOO/ChiTuBox and UVtools slicer profiles expose the full 82.62 x 130.56 mm LCD area. Workpiece must keep those concepts separate.

The exact mapping/offset between the conservative manufacturing envelope and slicer/display coordinates remains a physical-acceptance item. This checkpoint deliberately does not invent that offset.

## Integration boundary

This module does not yet change `/v1/candidate` or `/v1/project`. Once CP1 acceptance is stable and explicit per-plate 3MF generation exists, the slicer integration should create one `PlateArtifactRecord` per physical plate and only emit an order bundle after the manifest builder verifies complete plate coverage.
