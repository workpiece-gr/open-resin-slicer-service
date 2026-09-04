# Mars 2 resin orientation authority contract

Status: stacked CP2 checkpoint. This module ranks measured orientation candidates; it does not automatically approve an orientation, call PrusaSlicer, enable production, or replace human review.

## Why orientation is a separate resin problem

A generic planar-face or minimum-bounding-box orientation is not sufficient for MSLA production. Resin orientation must account for peel forces and layer cross-section, unsupported islands, suction/cupping, sealed cavities and resin traps, support burden and support scars, Z height, and print time.

Those signals do not all exist at raw-mesh time. Workpiece therefore separates candidate generation from evidence-backed evaluation instead of pretending a geometry-only heuristic is authoritative.

## Evidence stages

`geometry-only` is for cheap deterministic narrowing. It must retain the source STL hash and can never claim complete production evidence.

`sliced` is backed by the exact retained review 3MF and intermediate SL1 produced by the pinned PrusaSlicer path. It must retain source/3MF/SL1 hashes plus the PrusaSlicer commit.

`uvtools-inspected` additionally binds the exact printer-native CTB hash and UVtools version. Only this stage sets `production_evidence_complete=true`, and even then the evaluation authority remains `candidate-ranking-only` and `human_review_required=true`.

## Hard blockers before soft scoring

The evaluator applies hard blockers before ranking. Current fail-closed policy dimensions are:

- outside the validated manufacturing envelope;
- island count over the selected policy threshold;
- suction-cup count over the selected policy threshold;
- sealed-cavity count over the selected policy threshold;
- resin-trap count over the selected policy threshold.

A blocked candidate always ranks after every unblocked candidate, even if its soft score is numerically better.

## Soft score

The soft score retains a component breakdown for Z height, peak layer area, support volume, support-contact count, and estimated print time. Each component is normalized by explicit policy reference values and weights. Candidate ID is the final deterministic tie-breaker.

The defaults in `OrientationPolicy` are candidate-ranking scaffolding, not physically accepted Mars 2 grey-resin production tuning. Final thresholds, references, and weights must be versioned and tuned from real PrusaSlicer/UVtools output plus physical print acceptance.

## Candidate generation boundary

This checkpoint deliberately does not implement a dense arbitrary angle search or browser-style planar-face authority. A later deterministic generator can propose a bounded set from meaningful mesh directions (for example dominant face normals/principal axes plus resin-appropriate tilts), but every candidate that advances toward production still needs the evidence stages above.

## Integration boundary

No existing API is changed by this checkpoint. Once CP1 container acceptance is stable, the server can generate a bounded candidate set, create real Prusa supports/pads and slices for shortlisted candidates, inspect exact CTBs with UVtools, evaluate them with this contract, and present the surviving ranking and component evidence for human review.
