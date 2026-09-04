# Resin-aware orientation contract

Status: CP3 implementation checkpoint. This is candidate-selection infrastructure only. It does not change `/v1/candidate`, `/v1/project`, profile readiness, or production authorization.

## Why the browser heuristic is not enough

The existing Workpiece browser workflow uses a generic largest-suitable-planar-face heuristic. That remains useful for preview and FDM-style manufacturability estimates, but it is not a sufficient MSLA production rule.

Resin orientation changes peel/release forces, unsupported minima/islands, suction/cupping risk, support burden and support scars, and total Z height. A large flat face parallel to the vat can be a particularly poor resin orientation even when it would be a natural FDM bed face.

## CP3 decision model

`app/orientation.py` ranks explicit orientation candidates. Every candidate carries:

- XYZ rotation;
- maximum layer cross-section area;
- estimated/observed support volume;
- estimated/observed model/support contact area;
- resulting Z height;
- unresolved island count;
- unresolved suction-cup count;
- unresolved resin-trap count;
- metric source provenance.

### Hard blockers

Candidates are excluded from automatic selection before soft scoring when they contain any unresolved islands, suction cups, or resin traps.

When `require_sliced_validation=True`, geometry-proxy candidates are also excluded. This gives the later authoritative path an explicit way to require measurements derived from an actual supported/sliced candidate rather than browser or raw-mesh estimates.

If no candidate remains, the decision fails closed to `manual-review-required`.

### Soft ranking

The initial candidate heuristic uses min-max normalization across the unblocked candidate set and these weights:

- maximum layer area: 0.40;
- support volume: 0.25;
- support contact area: 0.20;
- Z height: 0.15.

These weights are deterministic seeds, not Mars 2 calibration results. They must be validated against real supported slices and physical prints before any automatic production use.

Ties resolve deterministically by lowest score, then least total rotation, then canonical XYZ angles.

## Deterministic candidate generation

`app/orientation_candidates.py` now supplies a bounded proposal generator. The default set contains 30 unique rotations:

- the source orientation;
- positive/negative X and Y tilts at 15, 30, and 45 degrees;
- the four X/Y diagonal combinations at each tilt;
- the five remaining principal build directions (the source orientation already represents +Z).

The generator is deliberately a proposal layer, not geometry authority. It sorts configured tilt values, de-duplicates canonical rotations, and refuses to generate more than 64 candidates.

Z spin is fixed at zero in generated proposals. Rotating only around Z does not change the resin build direction or peel/support relationship; the later physical-plate planner may choose a Z rotation for capacity after the resin orientation is selected.

This initial bounded lattice does not claim to discover every optimal orientation or preserve every functional face. A later geometry-aware extension may add face-normal or user-protected-surface proposals without changing the scoring/authority schema.

## Metric authority

Two metric sources are explicit:

- `geometry-proxy`: cheap geometric/layer simulation suitable for screening proposals;
- `sliced-validation`: metrics derived after the candidate has real support/pad and slice evidence.

The decision manifest always records which source produced each candidate's metrics. `automatic_production_authority` is hard-coded false in this checkpoint.

The intended long-term flow is:

`source STL -> deterministic orientation candidates -> proxy screening -> real Prusa support/pad for finalists -> sliced/UVtools validation -> orientation decision -> deterministic plate packing -> exact retained 3MF/SL1/CTB chain -> human/physical acceptance`

## Surface-finish boundary

Global support contact area is included as a support-scar proxy. Workpiece does not yet know which user surfaces are cosmetic, mating, sealing, threaded, optical, or otherwise protected. Until that semantic surface data exists, the orientation decision must remain reviewable by a human and must not claim to optimize functional/cosmetic scar placement automatically.

## Design basis

The contract reflects established SLA/MSLA guidance rather than the existing FDM orientation rule:

- Formlabs model-orientation guidance: tilting large flat surfaces reduces per-layer area and peel force; orientation should avoid unsupported minima and suction cups: https://formlabs.com/support/Model-Orientation/
- Formlabs SLA orientation guidance: concave/cupped geometry and drainage materially affect suction risk: https://formlabs.com/blog/how-to-orient-sla-parts/
- PrusaSlicer SLA orientation guidance: large flat horizontal sections require high separation force, while angled placement distributes supports and affects print time: https://help.prusa3d.com/article/object-orientation_1658

These sources are design guidance, not validation of the Workpiece weights or the Mars 2 calibration tuple.

## Next integration step

Add a bounded metric-extraction layer for geometry-proxy screening. Finalists should then be materialized with the real Prusa support/pad configuration and inspected from actual slice evidence before the selected orientation is allowed into the plate planner.
