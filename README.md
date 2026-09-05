# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing. It is intentionally isolated from the production FDM OrcaSlicer service.

## Artifact chain

The candidate direct-slice path retains:

`immutable STL -> PrusaSlicer review project (.3mf) + exact effective config -> PrusaSlicer SLA slice (.sl1) -> UVtools conversion/inspection -> printer-native .ctb/.goo`

The production-authority library path is stricter:

`immutable STL -> deterministic orientation screening -> exact sliced-finalist validation -> selected CTB-bound envelope -> deterministic physical plate plan -> exact per-plate 3MF build-item materialization -> verified per-instance 3MF evidence -> exact retained-config per-plate SL1/native slice -> UVtools whole-plate native envelope validation -> plate authority evidence -> selected order manifest`

The review 3MF and exact effective config are retained together. Downstream SLA slicing uses that exact pair with `--dont-arrange`; it does not rebuild from STL, recenter the model, reapply orientation, or re-resolve profiles.

The deterministic materializer changes only the selected Prusa 3MF build items. The final project must be byte-for-byte reproducible from the exact selected review 3MF, exact CTB-bound source envelope and retained placements before per-instance materialization evidence is accepted. The final native file is separately measured again with pinned UVtools.

The service never sends a job to a printer automatically.

## Current acceptance target

The first machine target is **ELEGOO Mars 2** with **ELEGOO Water Washable Grey Resin**.

Machine facts currently encoded in the acceptance profile include:

- 1620 x 2560 mono LCD;
- 82.62 x 130.56 mm active display used by the UVtools Mars 2 Pro reference profile;
- X mirroring enabled in the current native conversion reference;
- CTB v4 conversion contract;
- 405 nm light source;
- Workpiece caps Z at **150 mm**, matching ELEGOO's Mars 2 specification rather than assuming the Mars 2 Pro's 160 mm Z height.

The conservative Workpiece manufacturing envelope is 80 x 129 mm, but its physical manufacturing-to-display coordinate transform is deliberately still **unverified**. The Mars 2 profile is therefore **candidate-ready, not production-ready**. No offset, axis direction or mirror transform is inferred from nominal dimensions alone.

The first concrete resin profile is `elegoo-water-washable-grey`. At 0.05 mm it starts at **2.75 s normal exposure / 30 s initial exposure**. Those values are calibration seeds only, not production authority.

## Candidate vs production endpoints

- `POST /v1/candidate` is the authenticated acceptance path. It accepts only explicitly approved candidate combinations and returns `X-Workpiece-Authority: acceptance-candidate-only`.
- `POST /v1/project` is **reserved and fail-closed**. It currently returns HTTP 503 after authentication and does not slice the uploaded STL. The endpoint will remain closed until the selected-orientation/plate-authority pipeline is wired end-to-end through the HTTP service.

Adding a `production_ready` profile or a `production_combinations` entry is therefore not enough to expose a production HTTP path. The runtime must also be explicitly changed to execute and bind the complete plate-authority chain.

## Production plate authority contract

A production-authoritative physical plate requires all of the following before it can appear in a production selected-order manifest:

1. an explicitly production-ready printer with a validated rigid manufacturing-to-display transform;
2. exact source/sliced-winner provenance, including review 3MF, effective config, winner SL1 and winner native hashes;
3. deterministic per-plate 3MF materialization from the exact selected review project;
4. byte-for-byte deterministic reconstruction of that materialized 3MF and parsed build-item transforms;
5. per-instance supported/padded envelopes derived from the exact selected CTB envelope through those verified transforms and checked against planned slots, margins and spacing;
6. exact per-plate Prusa slicing from the materialized 3MF + retained effective config with `--dont-arrange`;
7. pinned UVtools conversion, issue inspection and native metrics with zero critical resin issues;
8. final whole-plate native bounds matching the expected materialized display envelope within the bounded raster tolerance;
9. one complete `SelectedPlateAuthorityEvidence` object bound to the retained plate 3MF/SL1/native hashes and issue receipt;
10. a selected order manifest containing complete authority evidence for every physical plate.

Creating those evidence objects does not enable a printer, deployment or production route by itself.

## Pinned engines

- PrusaSlicer `2.9.6`, source commit `b028299c770b8380ee81c921a2867d522f288123`
- UVtools `6.2.0`, Linux x64 ZIP SHA-256 `cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f`

## API

- `GET /health`
- `GET /source`
- `GET /v1/profiles`
- `POST /v1/orientation/proxy` — authenticated geometry-only orientation screening; never manufacturing authority
- `POST /v1/candidate` — authenticated acceptance-only direct-slice bundle
- `POST /v1/project` — authenticated reserved production endpoint; currently HTTP 503/fail-closed

POST endpoints require `WORKPIECE_RESIN_PROJECT_API_TOKEN`.

`GET /health` exposes `production_http_endpoint_ready: false` while the production HTTP orchestration remains unwired.

## Profile gate

A candidate slice requires:

1. exact printer profile marked `candidate_ready`;
2. exact resin profile marked `candidate_ready`;
3. exact quality profile marked `candidate_ready`;
4. exact tuple in `candidate_combinations`;
5. every referenced PrusaSlicer config file present.

Profile metadata also supports an independent production-ready state, but profile readiness is only one prerequisite of the plate-authority contract above. It cannot open `/v1/project` on its own.

There is no generic Elegoo fallback.

## Local unit tests

```bash
python -m pytest -q
```

Unit tests mock the external slicers where appropriate. CI also contains pinned-toolchain/container acceptance coverage for the candidate service path.

## Planned Mars 2 acceptance

See `docs/MARS2_ACCEPTANCE.md`.

## License/source boundary

This service is intended to remain public under AGPL-3.0-or-later, like Workpiece's existing open FDM slicer service. The deployed `/source` endpoint must point to the exact public source used by the running service.
