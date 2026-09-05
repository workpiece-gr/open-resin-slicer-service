# Sliced finalist execution contract

The real resin orientation stage is deliberately split into three responsibilities:

1. `slice_native()` creates the exact retained 3MF + effective-config -> SL1 -> printer-native artifact chain for one proxy finalist.
2. `app.uvtools_metrics` parses four reproducible soft-ranking metrics from the exact printer-native file: maximum illuminated layer area, total cured material volume, whole-print XY footprint area, and print Z height.
3. `app.orientation_adapter.execute_real_sliced_finalists()` executes exactly the proxy finalists through the pinned engines, extracts those native metrics, and feeds hash-bound evidence into `app.orientation_execute.execute_sliced_finalists()`.

`app.orientation_execute.execute_sliced_finalists()` rejects source, orientation, profile, or artifact-hash mismatches before native metrics can enter orientation ranking. The real adapter runs finalists sequentially rather than in parallel so a bounded proxy finalist set cannot multiply PrusaSlicer/UVtools CPU and memory pressure.

The real adapter also avoids retaining every finalist's complete 3MF, effective config, SL1, and CTB in RAM. Each exact artifact chain is hash-verified and spooled to temporary disk immediately after slicing. Only a lightweight hash/issue/metric receipt stays in memory while the remaining finalists execute. After sliced validation selects a winner, the adapter reloads and re-verifies only that exact retained artifact chain. If all finalists are hard-blocked, no heavyweight finalist artifact is returned automatically; the evidence instead requires manual review.

The v2 metric contract deliberately avoids support-volume and support-contact-area values that exist only in transient PrusaSlicer state or are not cleanly preserved in the retained chain. Pinned UVtools 6.2.0 exposes layer `Area` and `Volume`, plus whole-file `BoundingRectangleMillimeters` and `PrintHeight`. Material volume is derived by summing every decoded layer's `Volume`, while footprint area is the whole-print bounding-rectangle width times height. Layer coverage must be exact and complete or validation fails closed.

Critical UVtools findings are deliberately not rejected inside an individual finalist's `slice_native()` call. They are preserved in the artifact receipt and become hard blockers during sliced-finalist validation, allowing one bad orientation to be rejected without aborting evaluation of other proxy finalists. The hard blockers are unresolved islands, suction cups, resin traps, touching bounds, and empty layers. Soft metrics rank only finalists that survive those blockers.

Geometry proxies remain screening-only. They are bounded by a deterministic triangle/layer work budget, and their sampled metrics never replace native-artifact validation. Real sliced orientation execution remains acceptance-only and has no production authority until exact supported/padded-envelope extraction, physical plate materialization, printer coordinate mapping, calibrated resin tuple, and physical print acceptance are complete.
