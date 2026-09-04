# Resin orientation geometry-proxy contract

Status: CP3 supporting checkpoint. This is cheap candidate-screening geometry only. It is not support generation, sliced validation, production authorization, or an API change.

## Purpose

Running the full pinned PrusaSlicer support/pad/slice chain for every orientation proposal would be expensive. `app/orientation_proxy.py` therefore extracts bounded geometric signals that can later be used to prune the deterministic proposal set before a small number of finalists receive real Prusa/UVtools validation.

## STL boundary

The proxy parser accepts the same structurally valid binary and ASCII STL forms as the service validator, but it deliberately caps automatic proxy analysis at 100,000 triangles. Larger meshes fail closed to sliced/manual orientation rather than silently sampling triangles and breaking topology.

## Transform order

Proxy rotations mirror the pinned PrusaSlicer 2.9.6 CLI implementation at commit `b028299c770b8380ee81c921a2867d522f288123`: transforms are applied in Z, then X, then Y order. This is the actual order in `src/CLI/ProcessTransform.cpp`, irrespective of CLI flag ordering.

The default candidate generator currently emits `z_deg = 0`, so Z spin remains a later plate-packing choice.

## Proxy metrics

For each orientation the module records:

- final XY envelope width/depth;
- final Z height;
- full 0.05 mm-equivalent layer count;
- how many layers were actually sampled, capped at 128 by default;
- maximum sampled gross contour area;
- downward projected triangle area;
- a downward support-moment signal (projected downward area multiplied by height above the oriented minimum Z);
- number of sampled layers whose intersection segments could not be stitched into closed contours.

`reliable_for_auto_screening` is false whenever sampled contours remain open.

## Conservative area rule

The proxy intentionally sums absolute areas of every closed contour. Nested hole contours are therefore conservatively counted as filled instead of being subtracted using uncertain proxy topology. This can overestimate peel-area burden, but it avoids a more dangerous false-low estimate. The real sliced mask remains authoritative.

## Support boundary

`downward_projected_area_mm2` and `downward_support_moment_mm3` are geometry signals only. They depend on STL triangle winding and do not claim to equal Prusa support contact area or support volume. They must not populate authoritative support-volume fields.

The existing browser resin estimate uses a generic downward-face ratio and support fraction; that is useful for customer preview but is intentionally not copied into server manufacturing authority.

## Slice boundary

This module does not detect authoritative islands, suction cups, resin traps, supports, pad geometry, or UVtools issues. Those remain hard-blocking sliced-validation evidence after finalists are materialized through the real pinned toolchain.

## Next step

Add a deterministic proxy-screening/ranking layer that uses these signals only to choose a small finalist set. The final orientation decision must still run with `require_sliced_validation=True` before it can feed the deterministic physical-plate planner.
