# Sliced finalist execution contract

The real resin orientation stage is deliberately split into two responsibilities:

1. `slice_native()` creates the exact retained 3MF -> SL1 -> printer-native artifact chain for one proxy finalist.
2. A pinned native-artifact measurement adapter extracts four reproducible soft-ranking metrics from the exact printer-native file: maximum illuminated layer area, total cured material volume, whole-print XY footprint area, and print Z height.

`app.orientation_execute.execute_sliced_finalists()` binds those responsibilities together. It rejects source, orientation, profile, or artifact-hash mismatches before native metrics can enter orientation ranking.

The v2 metric contract deliberately avoids support-volume and support-contact-area values that exist only in transient PrusaSlicer state or are not cleanly preserved in the retained chain. Pinned UVtools 6.2.0 exposes layer `Area`, whole-file `MaterialMilliliters`, `BoundingRectangleMillimeters`, and `PrintHeight`, allowing the v2 measurements to be reproduced from the exact retained CTB. Material milliliters are converted to cubic millimetres by multiplying by 1000; footprint area is bounding-rectangle width times height.

Critical UVtools findings such as unresolved islands, suction cups, and resin traps remain hard blockers rather than soft score terms.

This contract intentionally does not infer missing measurements from proxy geometry or unrelated metadata. Until the pinned UVtools adapter is implemented and exercised against a real retained CTB, real sliced orientation execution remains acceptance-only and has no production authority.
