# Ubuntu 26.04 support

Scope: add Ubuntu 26.04 amd64 packaging and CI coverage. Evidence date:
2026-11-12. Initial source: `4f5fb4c541391ce6c07f0ebec240b44394dddf81`.
Charm-development skill reference revision:
`f0974a759100e8c1250d3d417ab0755873f0229d`.

The existing uv plugin and platform syntax satisfy the relevant
[Canonical migration requirements](https://canonical.com/juju/docs/charmcraft/4/howto/migrate-bases/change-to-ubuntu-2604/).
No charm runtime or dependency changes were needed.

| Requirement and source | Authority / applicability | Before | Disposition | Target | Actual result |
| --- | --- | --- | --- | --- | --- |
| Ubuntu 26.04, user request | task-required / packaging | GAP: absent from platforms | Add to [charmcraft.yaml](../charmcraft.yaml) | Build ubuntu@26.04:amd64 | PASS: Charmcraft 4.5.0 built the artifact; manifest verified |
| CI and release coverage, [release workflow](../.github/workflows/release.yml) | contract-required / repository | GAP: three bases, three revision outputs and release assets | Add 26.04 using LXD on a 24.04 runner; include its revision in release tags and notes | Four distinct artifacts and complete release metadata | PASS locally: actionlint and metadata checks; hosted execution and publication unverified |
| Python compatibility, user request | task-required / runtime validation | UNKNOWN on Python 3.14 | Add explicit 3.12/3.14 CI checks and run locally | Lint and 44 unit tests pass on both interpreters | PASS: tox on Python 3.12.15 and 3.14.10; locked unit dependencies also pass on 3.14 |
| Locked uv packaging, BUILD-001 | required-baseline / shared | PASS: uv plugin, tracked lock, consolidated definitions and explicit ops/testing dependencies | Preserve | Existing bases and dependency declarations retained | PASS: 20.04, 22.04 and 24.04 retained; pyproject.toml and uv.lock unchanged; older artifacts not rebuilt locally |
| Build instructions, generic documentation guidance | recommended-default / documentation | PARTIAL: base lists incomplete | Update [development](../DEVELOPING.md), [architecture](charm-architecture.md) and [NetBox integration](netbox-inventory.md) docs | Document four bases and CI strategy | PASS: docs updated; subordinate compatibility remains independently scoped |
| Live artifact integration, DEPLOY-001 | required-baseline / integration | UNKNOWN on 26.04 | Defer: no disposable 26.04 model selected; existing fixtures restore dedicated VMs | Remains unverified | Not run; no Juju models or workloads changed |

Other baseline areas were not assessed. No user decisions are needed for these
dispositions. Existing untracked investigation notes were preserved. Historical
release and implementation records were not edited.

Validation performed:

- `uv sync --locked --group dev --python 3.14`
- `PYTHONPATH=lib:src .venv/bin/python -m pytest tests/unit -q`: 44 passed.
- `uvx tox -e lint,unit`: lint passed and 44 tests passed on Python 3.12.15.
- `uvx --python 3.14 tox -e lint,unit`: lint passed and 44 tests passed on Python 3.14.10.
- `charmcraft pack --use-lxd --platform ubuntu@26.04:amd64`: passed.
- `actionlint .github/workflows/release.yml`: passed with actionlint 1.7.12.
- Local workflow checks confirmed that build bases match declared platforms,
  release metadata includes the 26.04 revision, and a missing revision fails.
- `git diff --check`: passed.

A preliminary direct Pyright run in the locked development environment reported
two existing attribute typing errors in observability helpers. The repository's
normal isolated tox lint environment passed on both Python versions. Runtime
source was not changed.

Artifact: `lxd-host_ubuntu@26.04-amd64.charm` (ignored by Git).
SHA256: `a5a8d0bf18378813a8215f0d4ba8a172cd4511d5b2265b4caa1987a337dc168c`.
Its manifest declares Ubuntu 26.04, amd64. Charmcraft stopped its managed build
container after packing. No Charmhub upload, GitHub release or deployment was run.

For manual validation, deploy this artifact with base `ubuntu@26.04` into an
explicitly selected disposable machine model with LXD already installed and
initialized. Confirm the reported LXD version and status, then exercise metrics
and logging relations with compatible consumers. The NetBox subordinate needs
its own Ubuntu 26.04 compatibility validation. The existing integration suite's
dedicated VM snapshots do not establish Ubuntu 26.04 deployment coverage.
