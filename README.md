# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing. It is intentionally isolated from the production FDM OrcaSlicer service.

## Artifact chain

`immutable STL -> PrusaSlicer review project (.3mf) -> PrusaSlicer SLA slice (.sl1) -> UVtools conversion/inspection -> printer-native .ctb/.goo`

The **review 3MF is created first** with the selected machine, resin, quality/support settings and orientation. The SLA slice is then generated from that exact retained 3MF without reloading profiles or reapplying transforms. The 3MF SHA-256 therefore sits directly in the provenance chain that produced the final printer file.

The API returns a review ZIP containing:

- immutable source STL;
- PrusaSlicer review `.3mf`;
- intermediate `.sl1`;
- exact printer-native `.ctb`/`.goo`;
- `manifest.json` with hashes, engines, profiles and orientation;
- raw UVtools issue report.

The intended workshop flow mirrors Workpiece FDM authority as closely as resin formats allow:

1. open the retained 3MF in PrusaSlicer and inspect plate placement, orientation, SLA settings and supports;
2. if the project is accepted **without editing it**, the bundled CTB is the exact printer file derived from that project;
3. if the 3MF is changed, the old CTB is invalid for that review state and the bundle must be regenerated;
4. only a physically accepted printer/resin/quality tuple may become production-authoritative.

PrusaSlicer project 3MF can persist SLA support-point metadata, but automatic support generation through the CLI may instead retain the support parameters and regenerate automatic supports on project load. CP1 explicitly records which behavior the pinned build produces; physical desktop inspection remains an acceptance gate.

The service never sends a job to a printer automatically.

## Current acceptance target

The first machine target is **ELEGOO Mars 2** with **ELEGOO Water Washable Grey Resin**.

Machine facts currently encoded in the acceptance profile:

- 1620 x 2560 mono LCD;
- 82.62 x 130.56 mm active display mapping used by the UVtools Mars 2 Pro profile;
- X mirroring enabled;
- CTB v4 conversion contract;
- 405 nm light source;
- Workpiece caps Z at **150 mm**, matching ELEGOO's current Mars 2 product specification rather than assuming the Mars 2 Pro's 160 mm Z height.

The Mars 2 profile is **candidate-ready, not production-ready**. Physical CTB acceptance on Chris's real printer is required before promotion.

The first concrete resin profile is `elegoo-water-washable-grey`. At 0.05 mm it starts at **2.75 s normal exposure / 30 s initial exposure**, the midpoint of ELEGOO's published Mars 2 / Mars 2 Pro Ceramic Grey range. Those values are calibration seeds only, not production authority.

## Candidate vs production endpoints

- `POST /v1/candidate` accepts only explicitly approved **candidate combinations**. It returns an acceptance bundle and may retain UVtools issues for controlled inspection. The response is marked `X-Workpiece-Authority: acceptance-candidate-only`.
- `POST /v1/project` accepts only separately approved **production combinations** and fails closed on configured critical UVtools issues. When eventually enabled, its response is marked `production-authoritative`.

This lets Workpiece test real hardware without pretending calibration is complete.

## Pinned engines

- PrusaSlicer `2.9.6`, source commit `b028299c770b8380ee81c921a2867d522f288123`
- UVtools `6.2.0`, Linux x64 ZIP SHA-256 `cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f`

## API

- `GET /health`
- `GET /source`
- `GET /v1/profiles`
- `POST /v1/candidate` — authenticated acceptance-only bundle
- `POST /v1/project` — authenticated production-authority bundle; unavailable until profiles are physically validated

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

Unit tests mock the external slicers. The PR CI also has a dedicated container-acceptance job that builds the pinned engines and requests a real Mars 2 grey bundle through the HTTP API.

## Planned Mars 2 acceptance

See `docs/MARS2_ACCEPTANCE.md`.

## License/source boundary

This service is intended to remain public under AGPL-3.0-or-later, like Workpiece's existing open FDM slicer service. The deployed `/source` endpoint must point to the exact public source used by the running service.
