# Ubuntu 20.04 retirement

Scope: remove Ubuntu 20.04 from supported packaging and future releases, as
requested by the user. Initial revision: `50d9380`. Evidence date: 2026-11-12.

| Requirement and source | Authority / applicability | Before | Disposition | Target | Actual result |
| --- | --- | --- | --- | --- | --- |
| Remove 20.04, user request | task-required / packaging | GAP: 20.04 declared | Remove from [charmcraft.yaml](../charmcraft.yaml) | Only 22.04, 24.04 and 26.04 amd64 | PASS: YAML and matrix consistency checks |
| Release consistency, [workflow](../.github/workflows/release.yml) | contract-required / repository | GAP: four build targets, uploads and revisions | Remove 20.04 throughout CI; use three-revision release tags | Three distinct artifacts, matching uploads and metadata | PASS: actionlint, matching outputs/assets and local release script checks |
| Locked packaging, BUILD-001 | required-baseline / shared | PASS: uv plugin, tracked lock, explicit ops/testing dependencies | Preserve dependencies and runtime source | Existing packaging structure retained | PASS: dependency files, lock and runtime source unchanged |
| Current documentation, generic documentation guidance | recommended-default / documentation | GAP: current docs list 20.04 | Update development, architecture and NetBox docs | Current docs reflect remaining bases | PASS: source search and codespell |
| Live artifact behavior, DEPLOY-001 | required-baseline / integration | UNKNOWN for this change | Defer deployment: only packaging and CI scope changed | Remains unverified | No live deployment planned |

No runtime changes, dependency updates, existing Charmhub channel changes or
live deployments are included. Historical release and validation records retain
their original base lists. No user decisions are needed for these dispositions;
other baseline areas are unassessed.

Validation:

- `actionlint .github/workflows/release.yml`: passed with actionlint 1.7.12.
- Local YAML checks confirmed matching packaging platforms, CI build jobs,
  upload steps, revision outputs and release assets for all three remaining bases.
- Local release script checks confirmed three-revision metadata, rejection of
  missing revisions and a tag guard matching the new format. No remote release
  commands were executed.
- `PYTHONPATH=lib:src .venv/bin/python -m pytest tests/unit -q`: 44 passed on
  Python 3.14.10.
- `codespell` on changed files and `git diff --check`: passed.

No artifacts were rebuilt for this removal. The prior Ubuntu 26.04 build evidence
is in the [support record](ubuntu-26.04-support.md). For a future manual check,
inspect CI artifacts and release notes for exactly 22.04, 24.04 and 26.04.
Hosted CI execution and live deployments remain unverified for this change.
