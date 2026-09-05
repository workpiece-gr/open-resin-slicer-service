# Resin service architecture

## Isolation boundary

The FDM OrcaSlicer service is already part of the Workpiece production-authority chain. Resin remains under controlled development. The resin stack therefore has its own container, deployment domain, bearer token, resource limits and profile set. Failure or dependency upgrades in resin must not alter FDM behavior.

## Why PrusaSlicer + UVtools

PrusaSlicer provides the pinned SLA/MSLA slicing pipeline and retained 3MF/effective-config recipe. UVtools is the explicit native conversion, issue-inspection and final native-metrics layer for CTB/GOO outputs.

The authoritative downstream slice rule is: once a selected review 3MF and effective config exist, plate slicing uses that exact pair with `--dont-arrange`. It must not rebuild from STL, recenter, reapply orientation or resolve a different profile recipe.

## Orientation and selected-winner boundary

Workpiece does not claim that stock PrusaSlicer CLI performs automatic resin orientation.

The implemented server-side path instead separates orientation into independently testable stages:

1. deterministic geometry-proxy candidate generation/screening;
2. real pinned-engine slicing of only the proxy finalists;
3. exact CTB/native measurements and critical-issue evidence for each finalist;
4. deterministic ranking of sliced evidence;
5. one selected winner whose review 3MF, effective config, SL1, native file and native display envelope remain SHA-bound.

A proxy score never grants manufacturing authority. If all sliced finalists are blocked, the result requires manual review rather than silently choosing one. The production HTTP route accepts no manual X/Y/Z rotation override; it uses this exact sliced-evidence selection path.

## Physical plate/materialization boundary

Packing occurs in Workpiece manufacturing-envelope coordinates, not assumed slicer display coordinates.

A printer whose manufacturing mapping is `validated` must carry an explicit rigid `manufacturing_to_display_transform`. The transform is schema-checked, must preserve millimetre scale, may swap/reflect axes, and must map the complete manufacturing envelope inside the physical display. The exact transform used during planning is retained with the selected plan so later profile drift invalidates materialization.

The selected single-instance CTB bounding rectangle is retained in native display millimetres. With a validated transform it is mapped conservatively to manufacturing-axis bounds for packing. Planned manufacturing centers are then mapped back to display-space 3MF build-item placements.

The deterministic materializer clones the selected Prusa build item only. The selected 3MF object geometry, resolved metadata and SLA support-point metadata are preserved. Authority evidence later reconstructs the full materialized 3MF from the exact selected source project + exact source CTB envelope + retained placements and requires byte-for-byte equality before parsing the final build-item transforms back.

## Per-instance and final-native validation

Two independent physical-output proofs are required:

1. **Per-instance 3MF proof.** Every materialized build item is parsed back from the exact SHA-bound 3MF and must match the deterministic reconstruction. Per-instance supported/padded envelopes are derived from the exact selected single-instance CTB envelope through those verified placements, mapped back to manufacturing coordinates and checked against planned slots, margins and spacing. This is explicitly a transform proof, not falsely described as geometry re-extraction.
2. **Whole-plate native proof.** The exact materialized 3MF is sliced with the selected effective config, converted/inspected by pinned UVtools, and its final native whole-print bounding rectangle must match the expected union of materialized display envelopes within the bounded physical-pixel raster tolerance.

The final native issue receipt must contain the exact expected pinned UVtools categories, and production plate authority requires zero critical resin issues.

## Plate and order authority

`SelectedPlateAuthorityEvidence` joins the selected sliced-winner chain, verified per-instance materialized-3MF evidence, exact materialized-plate native execution, final whole-plate UVtools evidence and retained final file hashes. It also requires the printer profile itself already be explicitly production-ready.

A `production-authoritative` selected order requires one matching plate-authority object for every physical plate. The order binds its retained 3MF, SL1, native hashes and issue receipt back to those authority objects and additionally requires a carried immutable digest-pinned toolchain execution-environment record.

These evidence objects establish artifact authority; they do not change profile readiness, publish a toolchain image, deploy a service, or send a job to a printer.

## Production orchestration and bundle boundary

`app.production_orchestration.execute_selected_production_order()` composes the validated library stages into one bounded server-side production-evidence run. It requires the production profile tuple and a digest-pinned `WORKPIECE_RESIN_TOOLCHAIN_REF` before finalist slicing, blocks on manual-review orientation outcomes, executes physical plates sequentially, requires full plate authority for every plate, and emits the selected order v4 manifest with the validated execution-environment receipt.

`app.production_bundle.build_selected_production_bundle()` then verifies every retained source/selected/plate payload against those receipts and creates one deterministic multi-plate ZIP containing the source STL, proxy/sliced orientation evidence, exact selected winner recipe/artifacts, every materialized 3MF/SL1/native file, per-plate UVtools reports, and the authority manifest.

## HTTP boundary

The HTTP service exposes:

- `/v1/orientation/proxy` for geometry-only orientation screening;
- `/v1/candidate` for the non-authoritative direct acceptance bundle;
- `/v1/project` for the complete selected production-authority coordinator + deterministic multi-plate bundle.

`/v1/project` remains fail-closed at every prerequisite. It requires authentication, a digest-pinned immutable toolchain runtime receipt, an exact production-approved printer/resin/quality tuple, bounded quantity/finalist inputs, automatic sliced-finalist selection, validated manufacturing/display mapping, and complete plate authority for every generated physical plate. Any missing prerequisite or manual-review outcome prevents a production bundle.

Wiring the generic route does not approve the Mars 2. Its current profile remains candidate-only and its production compatibility list remains empty, so it cannot enter the production coordinator.

## Printer/resin validation gates

A production-ready machine profile requires at minimum:

1. exact build volume/manufacturing envelope;
2. exact LCD physical dimensions and pixel resolution;
3. physically validated manufacturing-to-display origin/axis/mirror behavior;
4. native target format/version;
5. confirmed lift/retract capabilities;
6. a known-good calibration/native file opened on the physical printer.

A production-ready resin profile requires exposure calibration on that exact printer/resin combination. Published/community values are starting points, not Workpiece authority.

## Mars 2 acceptance plan

1. Build the pinned container and verify `/health` identifies both engines.
2. Keep the Mars 2 profile candidate-only while its physical manufacturing/display mapping is unverified.
3. Slice deterministic calibration geometry through the candidate path.
4. Open generated native files in UVtools and the printer/vendor viewer where available.
5. Compare layer count, dimensions, mirroring, exposure and lift/retract values.
6. Establish the physical manufacturing-to-display transform from controlled test evidence; do not infer it from nominal display dimensions.
7. Run dry printer load checks where safe/supported.
8. Print calibration pieces and measure XY/Z dimensions.
9. Calibrate exposure and supports for the exact resin.
10. Only after those gates pass may the Mars 2 printer/resin/quality tuple be considered for production-ready profile promotion.

## Website integration

The website should call this service server-to-server, never directly from anonymous browser JavaScript. Workpiece should store immutable source and selected-order manifests plus the exact retained per-plate 3MF/SL1/native artifacts and their evidence. A human workshop acknowledgment can remain an additional operational gate during controlled launch.
