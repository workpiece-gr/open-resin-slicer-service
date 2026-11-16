# Coding-agent repository rules

How we work (reviews, CI, merging, publishing, evaluations, wording) is written once, in `docs/SYSTEM_MAP.md` section 0 of the private `workpiece-gr/workpiece-web` repository: read it first. This repository is public: never put host details, credentials, addresses or customer data here.

Before substantive changes read `README.md`, `docs/ARCHITECTURE.md` and `docs/UPSTREAM.md`.

This repository's own rules:

- Keep the review 3MF and its effective config together, record the SHA-256 of every delivered file, and respect upstream licensing requirements.
- Distinguish a passing mocked test from a real toolchain run, and both from a print that worked on the machine.
- Use focused PRs; do not merge failing changes and do not alter deployed services without the owner's explicit approval.
