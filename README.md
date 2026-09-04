# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing. It is intentionally isolated from the production FDM OrcaSlicer service.

## Engine chain

`immutable STL -> pinned PrusaSlicer SLA slice (.sl1) -> pinned UVtools conversion/inspection -> printer-native .ctb/.goo -> hashes/provenance -> human review`

The service never sends a job to a printer. Browser previews and orientation choices remain proposals until a retained native artifact has passed Workpiece review and physical acceptance.

## Current acceptance target

The first machine target is **ELEGOO Mars 2** with **ELEGOO Water Washable Resin**.

Machine facts currently encoded in the acceptance profile:

- 1620 x 2560 mono LCD;
- 82.62 x 130.56 mm active display mapping used by the UVtools Mars 2 Pro profile;
- X mirroring enabled;
- CTB v4 conversion contract;
- 405 nm light source;
- Workpiece caps Z at **150 mm**, matching ELEGOO's current Mars 2 product specification rather than assuming the Mars 2 Pro's 160 mm Z height.

The Mars 2 profile is **candidate-ready, not production-ready**. Physical CTB acceptance on Chris's real printer is required before promotion.

The resin family is recorded under `profiles/reference/elegoo-water-washable-v1.json`, but no concrete material profile is enabled yet because ELEGOO's recommended exposure depends on **resin color**. The actual bottle color must be selected before creating the first candidate material profile.

## Candidate vs production endpoints

- `POST /v1/candidate` accepts only explicitly approved **candidate combinations**. It may return a CTB even when UVtools reports issues so the file can be inspected during controlled acceptance. The response is marked `X-Workpiece-Authority: acceptance-candidate-only`.
- `POST /v1/project` accepts only separately approved **production combinations** and can fail closed on critical UVtools issues.

This lets Workpiece test real hardware without pretending calibration is complete.

## Pinned engines

- PrusaSlicer `2.9.6`, source commit `b028299c770b8380ee81c921a2867d522f288123`
- UVtools `6.2.0`, Linux x64 ZIP SHA-256 `cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f`

## API

- `GET /health`
- `GET /source`
- `GET /v1/profiles`
- `POST /v1/candidate` — authenticated acceptance-only slice
- `POST /v1/project` — authenticated production-authority slice; unavailable until profiles are physically validated

Both POST endpoints require `WORKPIECE_RESIN_PROJECT_API_TOKEN`.

## Profile gate

A candidate slice requires:

1. exact printer profile marked `candidate_ready`;
2. exact resin profile marked `candidate_ready`;
3. exact quality profile marked `candidate_ready`;
4. exact tuple in `candidate_combinations`;
5. every referenced PrusaSlicer config file present.

Production adds a second independent gate: all three profiles must be `production_ready` and the tuple must also be in `production_combinations`.

There is no generic Elegoo fallback.

## Local unit tests

```bash
python -m pytest -q
```

Unit tests mock the external slicers. A real container slice and physical-printer acceptance are separate checkpoints.

## Planned Mars 2 acceptance

See `docs/MARS2_ACCEPTANCE.md`.

## License/source boundary

This service is intended to remain public under AGPL-3.0-or-later, like Workpiece's existing open FDM slicer service. The deployed `/source` endpoint must point to the exact public source used by the running service.
