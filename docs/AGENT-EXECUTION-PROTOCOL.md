# Bounded agent execution protocol

**Audience:** future chats, Codex, coding agents and human developers. Read this after `AGENTS.md` and before substantial work. This is an execution protocol, not a replacement for project-specific engineering, safety, release or licensing requirements.

## 1. Start from a deliverable, not another investigation

- Read the repository rules, the relevant authoritative work order/roadmap and the **latest session checkpoint**. Carry forward completed investigations and known defects; do not restart the same discovery merely because a new chat has begun.
- Identify one concrete, reviewable outcome for this session: changed source and regression tests, a reproducible repair, verified documentation, or a bounded negative test. Define what can be finished even if optional inputs are missing.
- Verify the relevant `main` and active task-branch heads **once at session start** and record the exact starting commit. Inspect any local uncommitted work before touching it. Recheck only when integration or an observed concurrent change makes it necessary.
- Use approximately **25-minute bounded sessions where practical**. This is a work budget, not permission to promise background execution. Make a real source/test artifact early. If interrupted, hand over the actual partial result instead of recapping plans.

## 2. Efficient calls, real implementation and stop rules

- Read only the code, tests, references and release/engineering constraints needed for the current change. Batch independent reads. Do not repeatedly fetch unchanged files, relist branches, restart historical investigations, or poll a slow check in a tight loop.
- Separate discovery from implementation: after the minimum necessary verification, change the smallest relevant source and add a regression test for the observed failure. A complete plan without a changed artifact does not finish an implementation task.
- After a failed remote/API/supplier/CAD operation, make **at most one reasonable retry or different approach**. Record the exact blocker, then continue an independent source, test, calculation or documentation lane. A missing drawing must not block unrelated software.
- Prefer a local working tree, focused tests and targeted diff review as the inner loop. Do not repeatedly trigger CI or expensive full builds. Run relevant GitHub Actions once the candidate PR is reviewable, or when an affected change genuinely needs another run. Inspect a failing job's logs; fix the cause instead of repolling it.
- Do not promise that a chat is coding or monitoring after its response ends. Name the remote branch, local patch/ZIP, open PR or real scheduled process only if it actually exists.

## 3. Independent parallel lanes, not imaginary workers

- Split work only along real ownership boundaries: distinct files, tests versus implementation, backend versus frontend, different repositories, or independent engineering calculations. Give each lane explicit inputs, deliverable and integration criteria.
- Use actual independent workers when available. Otherwise batch read-only calls and maintain separately scoped task branches or sequential implementation checkpoints. **Separate branches are not background workers.** Never claim parallel execution if work was only planned.
- Never perform simultaneous writes to the same file or branch. Coordinate each lane's commits, synchronize with current `main` before integration, check for other contributors' changes and reconcile conflicts deliberately.
- Keep risky or high-coupling operations sequential: final merges, releases, production settings, payment/data migrations, destructive operations and physical-machine acceptance.

## 4. Verification and integration

- Run the smallest meaningful regression tests first, then any required repository-specific checks. Record exact commands and pass/fail/skip outcomes. A green mock, imported STEP, valid ZIP or successful build proves only the checks it actually exercised.
- Keep source changes in one focused task branch per coherent deliverable. Preserve pre-existing branches, PRs, baselines, generated/reference assets and uncommitted work. No force push or undocumented bulk cleanup.
- If remote access works, push/commit the deliverable and open a reviewable PR. Recheck `main` immediately before final validation/merge; rerun affected checks after any synchronization. Follow the repository's actual authorization and CI rules. Never silently merge, publish or deploy merely because source validation passed.
- If GitHub fails, deliver complete local source changes, tests, a diff/ZIP where possible, and a checkpoint stating exactly what could not be pushed or verified. Do not lose a working result to a repeated API retry.

## 5. Communicate durable progress and leave a usable checkpoint

Give a concise update when there is a real discovery, artifact or blocker—not a stream of generic “still working” messages. Deliver verified findings as soon as they are stable. Do not hide a failure behind an optimistic summary.

Every session ends with the following **session checkpoint**, including incomplete sessions:

| Field | Required content |
| --- | --- |
| Starting state | Source repository; base branch and exact starting commit; active task branch |
| Deliverables | Actual changed files, new tests, generated artifacts or local patch/ZIP |
| Verification | Exact local test commands and results; CI run/PR status only if actually checked |
| Remaining issues | Known failing tests, unverified assumptions, unavailable dependencies/supplier inputs |
| Integration status | Distinguish uncommitted, local commit, pushed branch, open PR, merged and deployed |
| Next task | One concrete next implementation or verification action; do not restart finished analysis |

For the next chat: read `AGENTS.md`, this file and the newest checkpoint; verify the necessary heads once; implement the first unfinished task. Current repository/CI/provider state overrides a stale handoff, but a stale handoff is not a reason to repeat the entire previous investigation.

## Repository-specific guardrails

This is the public resin slicing service. Preserve exact input/project/config/native-output provenance, deterministic per-plate evidence, license obligations and qualification gates. A valid archive or a passing mock must not be described as a real printer or production qualification.
