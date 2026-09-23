# Coding-agent repository rules

Read `README.md`, [the bounded execution protocol](docs/AGENT-EXECUTION-PROTOCOL.md), and the relevant `docs/ARCHITECTURE.md`, `docs/UPSTREAM.md` and acceptance/qualification evidence before substantive changes.

- Follow approximately 25-minute bounded implementation sessions where practical, with real source/tests early and a precise checkpoint on completion or interruption.
- Verify relevant branch heads once at the start, preserve other contributors' branches and retained artifacts, prefer local focused tests over repeated CI, and stop retrying a persistent external dependency.
- Use genuinely independent development/verification lanes where available; never invent background workers or perform concurrent writes to the same file/branch.
- Preserve input-to-native-output evidence identity, exact slicing configuration, deterministic multi-plate provenance and upstream licensing requirements.
- Distinguish successful mock/export/archive checks from native slicer validation, manufacturing qualification and physical-machine safety.
- Use focused PRs; do not merge failing changes or alter deployed services without the required explicit approval.
