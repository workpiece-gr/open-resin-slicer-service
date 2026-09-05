# Resin toolchain release boundary

Workpiece resin service development must not compile PrusaSlicer during normal CI.

## Why this exists

The resin manufacturing engine is substantially more expensive to build than the Workpiece Python service. Treating the engine build as part of every service change made CI slow and made application development dependent on repeated C++ dependency builds.

The FDM service already uses the correct separation: the slicer engine is an external pinned runtime and CI focuses on Workpiece behavior. Resin now follows the same operational model while retaining source-derived authority for PrusaSlicer.

## Two images

### Toolchain image

`Dockerfile.toolchain` is the rare, source-derived engine build. It contains:

- PrusaSlicer 2.9.6
- exact PrusaSlicer source commit `b028299c770b8380ee81c921a2867d522f288123`
- PrusaSlicer's pinned dependency bundle
- UVtools 6.2.0
- pinned UVtools archive SHA-256
- `/opt/workpiece-toolchain/manifest.json`

Changing the Workpiece API, profiles, planning logic, orientation logic, or tests must not require rebuilding this image.

### Service image

`Dockerfile` starts from a toolchain image and adds only the Workpiece Python environment, application code, profiles, and project metadata.

Normal CI must use an immutable registry reference of the form:

`ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:<digest>`

A mutable tag is never manufacturing authority.

## Lock contract

`toolchain.lock.json` is the source-controlled boundary between the two images.

While the first toolchain image has not been explicitly approved and published, it remains:

- `status: unpublished`
- `digest: null`

In that state, normal unit CI runs but real container acceptance is intentionally blocked. CI must not silently rebuild PrusaSlicer or fall back to a mutable tag.

After an explicitly approved publication, update the lock in a reviewed commit to:

- `status: published`
- the immutable GHCR digest returned by the publish job

Normal acceptance then pulls exactly that digest, builds the small service layer, verifies the embedded toolchain manifest against the lock, and runs the Mars 2 candidate/determinism checks.

## Toolchain workflow

`.github/workflows/toolchain.yml` is manual-only.

A normal manual run builds and verifies the toolchain candidate but does not publish it.

Publishing is a separate gated action. It requires both:

1. `publish=true`
2. the exact acknowledgement `PUBLISH_PINNED_TOOLCHAIN`

Publishing a toolchain does not update `toolchain.lock.json` automatically. The returned digest must be reviewed and pinned in a separate commit. This prevents a workflow run from silently changing manufacturing authority.

## Release sequence

1. Change `Dockerfile.toolchain` only when the engine/runtime itself must change.
2. Run the manual toolchain build without publication.
3. Verify PrusaSlicer version, embedded manifest, UVtools pin, and any engine acceptance evidence.
4. Obtain explicit approval before publication.
5. Publish the toolchain and record the immutable digest.
6. Update `toolchain.lock.json` in a reviewed commit.
7. Run normal resin container acceptance against that digest.
8. Keep printer/resin/quality production readiness blocked until physical Mars 2 acceptance is complete.

## Reproducibility rule

The manufacturing provenance chain should ultimately record both the logical engine pins and the executable environment:

- PrusaSlicer version and source commit
- UVtools version and archive SHA-256
- immutable toolchain image digest
- service/application revision
- printer, resin, and quality profile IDs
- retained 3MF/SL1/CTB artifact identities and semantic regeneration evidence

The toolchain digest complements source provenance; it does not replace it.
