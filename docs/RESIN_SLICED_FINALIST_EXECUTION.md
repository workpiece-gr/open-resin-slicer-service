# Sliced finalist execution contract

The real resin orientation stage is deliberately split into two responsibilities:

1. `slice_native()` creates the exact retained 3MF -> SL1 -> printer-native artifact chain for one proxy finalist.
2. The pinned native-artifact measurement adapter extracts four reproducible soft-ranking metrics from the exact printer-native file: maximum illuminated layer area, total cured material volume, whole-print XY footprint area, and print Z height.

`app.orientation_execute.execute_sliced_finalists()` binds those responsibilities together. It rejects source, orientation, profile, or artifact-hash mismatches before native metrics can enter orientation ranking.

The v2 metric contract deliberately avoids support-volume and support-contact-area values that exist only in transient PrusaSlicer state or are not cleanly preserved in the retained chain. Pinned UVtools 6.2.0 exposes layer `Area` and `Volume`, plus whole-file `BoundingRectangleMillimeters` and `PrintHeight`. `app.uvtools_metrics` therefore derives material volume by summing every decoded layer's `Volume`, while footprint area is the whole-print bounding-rectangle width times height. Layer coverage must be exact and complete or validation fails closed.

All engine-critical native findings are hard blockers rather than soft score terms: unresolved islands, suction cups, resin traps, touching bounds, and empty layers. The soft metrics only rank finalists that survive those blockers.

Geometry proxies remain screening-only. They are bounded by a deterministic triangle/layer work budget, and their sampled metrics never replace native-artifact validation. Real sliced orientation execution remains acceptance-only and has no production authority until the printer mapping, resin tuple, materialized plate, and physical print are accepted.
