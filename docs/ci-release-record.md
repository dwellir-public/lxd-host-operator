# CI release working record

Scope: add automatic edge publication and promote the first successful main
build to stable, as authorized by the user. Evidence date: 2026-10-24.

Initial source: `bce1620f8b9bfe9d8d0544abfad6eab8336caa6b`.
Reference: [Alloy release workflow](https://github.com/dwellir-public/alloy-vm-operator/blob/237aab5e99f6b92f8505c0647c9213424105a330/.github/workflows/release.yml).

| Requirement and source | Authority / applicability | Before | Disposition | Target | Actual evidence |
| --- | --- | --- | --- | --- | --- |
| Automatic edge release, user request | task-required / release | GAP: no workflows | Implement release workflow | Publish main after checks and all builds | Pending first CI run |
| Three declared bases, charmcraft.yaml | contract-required / packaging | Bases declared, no CI evidence | Preserve all bases | Built amd64 artifacts for 20.04, 22.04, 24.04 | Pending first CI run |
| Scoped credential, user request | task-required / release | GAP: no repository secrets | Create and install CHARMHUB_TOKEN | Scoped edge upload/release token | GitHub secret installed 2026-10-24, TTL 31536000 seconds |
| Stable promotion, user request | task-required / initial release | UNKNOWN: existing channel map | Promote verified main revisions | Stable matches successful edge build | Pending first CI run |
| Locked uv packaging, BUILD-001 | required-baseline / shared | PASS: uv plugin, tracked uv.lock, consolidated definitions, ops/testing dependencies | Preserve | Packaging unchanged | No dependency changes |
| Lint and unit checks, generic testing guidance | recommended-default / shared | GAP: Ruff complexity, line length and formatting errors | Resolve lint blockers without changing behavior | Lint and unit pass | Local lint/type checks and 44 unit tests pass; actionlint and git diff --check pass |
| Live artifact integration, DEPLOY-001 | required-baseline / integration | UNKNOWN in this task | Defer live fixture execution, dedicated snapshots required | Remains unverified | No live models accessed |

Changes are limited to the workflow, developer/release documentation, and
existing lint blockers. Other charm behavior and full compliance are unassessed.
No user decisions are needed for these dispositions. No credentials are retained
in this record. Final release evidence will record commit, base, revision and
artifact checksum after CI completes.
