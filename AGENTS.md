# Coding-agent repository rules

Read `README.md`, [the bounded execution protocol](docs/AGENT-EXECUTION-PROTOCOL.md), `docs/ARCHITECTURE.md` and `docs/UPSTREAM.md` before substantive changes.

- Follow approximately 25-minute bounded implementation sessions where practical, with real source/tests early and a precise checkpoint on completion or interruption.
- Verify relevant branch heads once at the start, preserve other contributors' branches and retained artifacts, prefer local focused tests over repeated CI, and stop retrying a persistent external dependency.
- Use genuinely independent development/verification lanes where available; never invent background workers or perform concurrent writes to the same file/branch.
- Keep the review 3MF and its effective config together, record SHA-256 of every delivered file, and respect upstream licensing requirements.
- Distinguish passing mocked tests from a real toolchain run, and both from a print that worked on the machine.
- Use focused PRs; do not merge failing changes or alter deployed services without the required explicit approval.
