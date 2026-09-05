# Sliced finalist execution contract

The real resin orientation stage is deliberately split into two responsibilities:

1. `slice_native()` creates the exact retained 3MF -> SL1 -> printer-native artifact chain for one proxy finalist.
2. A measurement adapter extracts max layer area, support volume, support contact area, and Z height from those exact sliced artifacts.

`app.orientation_execute.execute_sliced_finalists()` binds those responsibilities together. It rejects source, orientation, profile, or artifact-hash mismatches before sliced metrics can enter orientation ranking.

This contract intentionally does not infer missing measurements from proxy geometry or unrelated metadata. Until a pinned Prusa/UVtools adapter extracts the measurements from the retained artifact chain, real sliced orientation execution is incomplete and has no production authority.
