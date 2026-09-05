# Open Resin Slicer Service

Open-source HTTP service for Workpiece MSLA/resin slicing. It is intentionally isolated from the production FDM OrcaSlicer service.

## Artifact chain

`immutable STL -> oriented seed + merged effective SLA config -> centered review geometry (.3mf) -> PrusaSlicer SLA slice (.sl1) -> UVtools conversion/inspection -> printer-native .ctb/.goo`

The selected machine, resin and quality profiles are first merged by the pinned PrusaSlicer build and saved as the retained effective `.ini` while the requested orientation is applied to an intermediate geometry seed. Workpiece then reloads that already-oriented seed with the exact effective config, centers it explicitly on the active-display center declared by the candidate printer profile, and exports the final retained review `.3mf`. The final SLA slice reloads that exact review 3MF plus the exact retained effective config without reapplying orientation or placement transforms.

This distinction is intentional. In the pinned PrusaSlicer CLI path, `--export-3mf` exports model/review geometry without passing the print configuration to the 3MF writer, so Workpiece does **not** treat that file as a self-contained Prusa project recipe. Prusa also treats `.3mf` slicing input as `dont-arrange`, which is why Workpiece materializes the deterministic centering step before retaining the final review 3MF. The final 3MF SHA-256 and effective-config SHA-256 jointly bind the retained slicing recipe.

The API returns a review ZIP containing:

- immutable source STL;
- oriented and centered PrusaSlicer review `.3mf`;
- merged effective PrusaSlicer SLA `.ini`;
- intermediate `.sl1`;
- exact printer-native `.ctb`/`.goo`;
- `manifest.json` with hashes, engines, profile IDs, orientation, review-center placement and recipe roles;
- raw UVtools issue report.

The intended workshop flow mirrors Workpiece FDM authority as closely as resin formats allow:

1. inspect the retained 3MF for the exact oriented/centered geometry and inspect the retained effective `.ini` for the exact SLA settings used by the pinned engine;
2. inspect the generated SL1/CTB in UVtools for the materialized supports, layers and printer parameters;
3. if the retained 3MF and effective config are accepted **without editing either**, the bundled CTB is the exact stored printer file derived from that recipe;
4. if either recipe artifact changes, the old CTB is invalid for that review state and the bundle must be regenerated;
5. only a physically accepted printer/resin/quality tuple may become production-authoritative.

The CLI-exported review 3MF is not assumed to contain materialized automatic support points or SLA configuration. Automatic supports are generated during slicing from the retained geometry plus effective configuration. CP1 records the resulting artifacts for controlled inspection; physical desktop and printer acceptance remain mandatory gates.

The explicit CP1 review center uses the active-display dimensions in the candidate printer profile. That is a slicer-placement seed, **not** proof of the final printable manufacturing envelope or its physical mapping. The later plate-layout and hardware acceptance checkpoints still validate the conservative manufacturing envelope and printer mapping before production.

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
