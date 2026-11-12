# Per-container network limit collection

Scope: prepare source changes for the approved actual-cap alerting approach.
No production refresh, host service installation, or Grafana rule mutation is
included. Baseline: `4f5fb4c541391ce6c07f0ebec240b44394dddf81`, 2026-11-12.

## Change matrix

| Requirement / authority | Before | Disposition | Target | Actual / evidence |
| --- | --- | --- | --- | --- |
| Actual caps, inherited profiles and overrides / task-required | Missing from native LXD metrics | Add read-only collector of expanded devices | Dynamic per-NIC, per-direction caps | PASS: semantic/unit and real Unix-socket fixture checks |
| Distinct host/project/container/NIC identities / task-required | Native host identity differs by collector/region | Add common `lxd_host` target-address relabel to both jobs | Collision-safe joins, no name allowlist | PASS: IPv4/IPv6/DNS relabel tests and cross-host/project PromQL cases |
| Discovery, unlimited and unsupported devices / task-required | Static Polygon selectors and 300Mbit assumption | Discover running local containers in all projects; distinguish states | New containers appear automatically | PASS: discovery/removal, managed networks, multi-NIC and unsupported-state tests |
| Freshness and collector failure / agent-proposed | No cap collector | Export success and timestamp; never retain old caps on failure | Failures observable; gaps have draft alerts | PASS: stale/failed cap suppression and alert tests |
| Existing endpoints, TLS and logging / recommended-default: skill generic standards | Native HTTPS LXD metrics and direct scrape relations | Preserve; companion is opt-in | Refresh alone does not enable new collector | PASS: disabled default, opt-in, removal and mTLS integration tests |
| BUILD-001 / required-baseline: charm-development skill | uv plugin, tracked lock, explicit ops/testing; split tox dependency definitions remain | Preserve dependencies; defer unrelated packaging consolidation | Baseline retained, existing duplication remains | PASS: tox lint/unit; no dependency changes. Full baseline remediation deferred |
| DEPLOY-001 / required-baseline: charm-development skill | Jubilant fixtures hardcode another operator's reusable VMs and restore them | Do not execute destructive historical fixtures; add isolated collector acceptance checks | Source validation and local HTTP/socket integration; Juju delivery remains unverified | PARTIAL: local transport checks pass; built-artifact/Juju delivery not executed |
| OBS-001 / required-baseline: charm-development skill | Existing prometheus_scrape topology maintained | Preserve Juju labels; add non-topology join label only | Source identity not replaced | Backend delivery unverified until rollout |

This bounded change does not migrate machine-observability schemas, change LXD
configuration or traffic limits, or claim full charm compliance. Existing live
Polygon alerts remain until the generic replacement has verified coverage.

## Implementation

- Add a standalone standard-library collector and opt-in systemd lifecycle helper.
- Extend `metrics-endpoint` with a companion scrape job and stable host relabeling.
- Keep native listener, TLS certificate handling and logging contracts unchanged.
- Add semantic, lifecycle, HTTP/Unix-socket and alert-expression tests.
- Document metric semantics, edge-site standalone installation, canary rollout,
  rollback and draft generic alert rules; keep rules out of live delivery.

No further design approval is needed for these source changes. Deployment and
live rule replacement are separate work.


## Validation and handoff

- Baseline: 44 unit tests passed before implementation.
- Final local `uvx tox -e lint,unit`: 92 tests passed; lint, formatting,
  codespell and static typing passed.
- Official `promtool` 3.16.0: syntax check passed for 7 draft rules; rule tests
  passed for five scenarios covering identity/deduplication, absent/zero caps,
  collection failure, stale data, and cap-driven firing/clearance.
- New modules parse with Python 3.8 syntax. This is not all-base execution evidence.
- Touched documentation links resolve locally; `git diff --check` passes.
- No production changes or live notifications were made during this preparation.
- Three-base charm builds are left to the existing PR CI workflow. No Juju
  deployment/integration or systemd installation was run. The existing historical
  integration fixtures restore another operator's VMs and were not used.
- Kernel tc policer-drop export is explicitly deferred; this change exports
  configured caps only. Regional rollout, gap reconciliation, published runbooks,
  Grafana-managed conversion and retirement of Polygon alerts remain separate.
- Two existing `CharmBase` dynamic-attribute accesses prevented a clean static
  check. They now use `getattr` in the touched metrics helper and the equivalent
  logging helper; this is a typing-only compatibility fix, not a behavior change.

The change workflow and skill were read locally with SHA256
`c18f9ceda1d914683ca8e3b03414c936d7bc54d4fc65d637ed4cf431fa9993f5`
and `5ceebd0d92cd93102b813afb9aa149a6cd2963b79280a37449af09d924739b6d`
respectively. No schema migration or whole-charm compliance claim is made.
