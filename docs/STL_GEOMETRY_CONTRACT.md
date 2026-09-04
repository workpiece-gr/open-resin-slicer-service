# STL source-space geometry contract

Status: stacked CP2 checkpoint. This adds bounded source-space STL measurement only; it does not change slicer transforms, plate placement, API behavior, or production authority.

## Purpose

The existing STL gate validates structure before invoking external slicers, but deterministic resin orientation and physical plate placement also need measured source-space geometry. In particular, a customer STL may have an arbitrary non-zero origin. A plate planner target center therefore cannot be used directly as a raw STL translation.

`app/mesh.py` provides a streaming triangle iterator and a geometry summary containing source-space min/max bounds, XYZ extents, bounds center, triangle count, and surface area.

## Bounded parsing

The existing `validate_stl_bytes` triangle limit remains authoritative. Binary STL triangles are streamed directly from the validated payload. ASCII vertex lines are parsed as finite floats after structural validation, including protection against values such as `1e999` that overflow to infinity only when converted.

The extractor does not retain the full mesh in memory and rechecks that the number of measured triangles matches the validated count.

## Units

STL itself has no unit metadata. This service treats STL coordinates as millimetres, matching the manufacturing/slicer contract. The manifest therefore records `units_assumed=mm`; this is an explicit assumption, not information recovered from the STL file.

## Authority boundary

The geometry manifest authority is `source-space-measurement-only`. These measurements describe the submitted STL before PrusaSlicer orientation, support generation, pad generation, or placement.

This checkpoint deliberately does not guess PrusaSlicer's rotation pivot or transform order. Integration must derive and verify the final oriented/supported/padded envelope in the actual retained 3MF coordinate frame before converting deterministic plate target centers into object transforms.

## Next use

A deterministic orientation candidate generator can iterate these triangles to derive meaningful bounded candidate directions. Later, after real PrusaSlicer supports/pads exist, the final supported envelope must be remeasured/revalidated before plate acceptance. The exact retained review 3MF remains the human-review authority.
