# Resin orientation geometry-proxy contract

Status: CP3 supporting checkpoint. This is cheap candidate-screening geometry only. It is not support generation, sliced validation, production authorization, or an API change.

## Purpose

Running the full pinned PrusaSlicer support/pad/slice chain for every orientation proposal would be expensive. `app/orientation_proxy.py` therefore extracts bounded geometric signals that can be used to prune the deterministic proposal set before a small number of finalists receive real Prusa/UVtools validation.

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

## Deterministic proxy screening

`app/orientation_screen.py` reduces the bounded proposal set to at most five finalists by default, with a hard cap of eight.

It first blocks proxy candidates whose sampled intersections contain open contours. Among the remaining candidates it performs non-dominated/Pareto ranking while minimizing three proxy objectives:

- maximum sampled gross layer area;
- downward support-moment signal;
- Z height.

This stage deliberately has **no calibrated weights**. Within the same Pareto rank, candidates are ordered by their lowest normalized maximum regret across the three objectives, then normalized total burden, least total rotation, and canonical XYZ angles. This creates a deterministic balanced tie-break without converting proxy signals into fake production metrics.

The proxy-screen manifest is `workpiece-resin-orientation-proxy-screen-v1` and hard-codes `automatic_production_authority` to false.

## Slice boundary

This module does not detect authoritative islands, suction cups, resin traps, supports, pad geometry, or UVtools issues. Those remain hard-blocking sliced-validation evidence after finalists are materialized through the real pinned toolchain.

Proxy finalists are **only** candidates for expensive validation. They may not enter deterministic physical-plate planning as the selected production orientation until actual sliced-validation metrics have been generated and the final orientation decision passes with `require_sliced_validation=True`.

## Next step

Once CP1 container acceptance is stable, add a finalist-validation path that materializes the small proxy-selected set with the real Prusa support/pad configuration, retains each review 3MF, and derives sliced/UVtools evidence before final orientation selection.
