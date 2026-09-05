# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing. It is intentionally isolated from the production FDM OrcaSlicer service.

## Artifact chain

The candidate direct-slice path retains:

`immutable STL -> PrusaSlicer review project (.3mf) + exact effective config -> PrusaSlicer SLA slice (.sl1) -> UVtools conversion/inspection -> printer-native .ctb/.goo`

The production-authority path is stricter:

`immutable STL -> deterministic orientation screening -> exact sliced-finalist validation -> selected CTB-bound envelope -> deterministic physical plate plan -> exact per-plate 3MF build-item materialization -> verified per-instance 3MF evidence -> exact retained-config per-plate SL1/native slice -> UVtools whole-plate native envelope validation -> plate authority evidence -> selected order manifest -> deterministic multi-plate production ZIP`

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
- `POST /v1/project` is the authenticated selected production-authority path. It only succeeds for an exact printer/resin/quality tuple already approved for production, an immutable digest-pinned runtime toolchain, an automatically selected sliced-finalist orientation, and a complete authority proof for every physical plate.

The production endpoint rejects manual `rotate_x`, `rotate_y`, and `rotate_z` overrides. It accepts `requested_quantity` (bounded by `MAX_PRODUCTION_QUANTITY`, default 100) and `finalist_limit` (bounded by the proxy safety cap). If sliced finalist selection requires manual review, a profile/tuple is not production-ready, the toolchain receipt is missing/mutable, or any physical plate fails authority validation, the request fails closed.

**The Mars 2 target is still candidate-only.** Its profile remains `production_ready=false`, its manufacturing/display mapping remains unverified, and `production_combinations` remains empty. Wiring `/v1/project` therefore does not authorize production on the Mars 2.

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
10. a selected order manifest containing complete authority evidence for every physical plate plus an immutable digest-pinned toolchain execution-environment receipt.

## Production bundle

`app.production_orchestration.execute_selected_production_order()` executes the complete source-bound production-evidence chain sequentially. `app.production_bundle.build_selected_production_bundle()` then hash-verifies and retains the exact production artifacts in one deterministic ZIP, including:

- source STL;
- proxy and sliced orientation evidence;
- selected winner 3MF/effective config/SL1/native artifact;
- every materialized physical-plate 3MF;
- every per-plate SL1 and printer-native file;
- per-plate UVtools issue reports;
- selected order v4 manifest containing plate-authority evidence and immutable toolchain provenance.

The runtime HTTP route can create this evidence-backed bundle, but no code in this repository automatically sends it to a printer or performs a deployment.

## Pinned engines

- PrusaSlicer `2.9.6`, source commit `b028299c770b8380ee81c921a2867d522f288123`
- UVtools `6.2.0`, Linux x64 ZIP SHA-256 `cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f`

The production path additionally requires `WORKPIECE_RESIN_TOOLCHAIN_REF` to be an immutable GHCR image reference pinned by `sha256` digest. A mutable tag is rejected.

## API

- `GET /health`
- `GET /source`
- `GET /v1/profiles`
- `POST /v1/orientation/proxy` — authenticated geometry-only orientation screening; never manufacturing authority
- `POST /v1/candidate` — authenticated acceptance-only direct-slice bundle
- `POST /v1/project` — authenticated evidence-backed selected production bundle

POST endpoints require `WORKPIECE_RESIN_PROJECT_API_TOKEN`.

`GET /health` exposes `production_http_endpoint_ready: true` because the generic authority pipeline is wired. This flag does **not** mean a particular printer or resin tuple has been approved for production; profile/compatibility gates remain independent.

## Profile gate

A candidate slice requires:

1. exact printer profile marked `candidate_ready`;
2. exact resin profile marked `candidate_ready`;
3. exact quality profile marked `candidate_ready`;
4. exact tuple in `candidate_combinations`;
5. every referenced PrusaSlicer config file present.

A production request additionally requires the corresponding profiles to be `production_ready`, the exact tuple to exist in `production_combinations`, the printer's manufacturing/display mapping to be physically validated, and every selected plate to pass the complete authority chain above.

There is no generic Elegoo fallback.

## Local unit tests

```bash
python -m pytest -q
```

Unit tests mock the external slicers where appropriate. CI also contains pinned-toolchain/container acceptance coverage for the candidate service path; production authority is additionally covered at the coordinator, bundle and HTTP contract layers without promoting the Mars 2 tuple.

## Planned Mars 2 acceptance

See `docs/MARS2_ACCEPTANCE.md`.

## License/source boundary

This service is intended to remain public under AGPL-3.0-or-later, like Workpiece's existing open FDM slicer service. The deployed `/source` endpoint must point to the exact public source used by the running service.
