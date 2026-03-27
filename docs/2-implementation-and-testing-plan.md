# LXD Host Operator Implementation And Testing Plan

## Goal

Build a new machine charm named `lxd-host` in the `lxd-host-operator`
repository.

The charm is intentionally minimal:

- it never installs LXD
- it only manages already installed LXD
- it only configures observability-related settings
- it is cluster-aware for status, metrics, and log-routing behavior
- subordinate exporter charms should publish their own metrics over `metrics-endpoint` rather than being proxied through `lxd-host`

The initial supported scope is:

- metrics exposure for LXD
- metrics fan-in from related subordinate exporters through one central Alloy
- log forwarding configuration for LXD
- relation-driven integration with:
  - `loki-vm`
  - `loki-loadbalancer-vm`
  - `alloy-vm`
- Juju topology on metrics and logs where the downstream stack supports it
- per-unit snap version in `ActiveStatus`
- leader-derived LXD version as workload version

## Key decisions

### 1. No install or bootstrap path

Unlike `charm-lxd`, this charm does not install or initialize LXD.

Install hook behavior should be limited to:

- verifying the `lxd` snap is present
- verifying the daemon is reachable
- inventorying local LXD facts
- blocking clearly if LXD is missing or unusable

There is no fallback path that installs LXD or runs `lxd init`.

### 2. Observability-only responsibility

The charm manages only these workload concerns:

- `core.metrics_address`
- `loki.*` keys for native LXD Loki output
- `daemon.syslog` snap option when using Alloy syslog ingestion
- LXD trust entries or TLS material needed for metrics scraping

It does not manage:

- storage pools
- networks
- clustering bootstrap or join
- image handling
- instance lifecycle
- kernel tuning

### 3. Cluster awareness without cluster ownership

Cluster-aware here means:

- each unit detects whether its local LXD is clustered
- each unit reports local member facts into peer data
- the leader validates whether all units belong to the same cluster
- metrics are exported per member so the cluster is observable as a set
- leader status can summarize cluster health and membership drift

The charm does not form or modify the cluster.

## Proposed repository shape

Follow the charm development standard:

- `src/charm.py`
- `src/lxd.py`
- `src/inventory.py`
- `src/metrics.py`
- `src/logging_config.py`
- `src/cluster_state.py`
- `src/status.py`
- `tests/unit/`
- `tests/integration/`
- `docs/charm-architecture.md`
- `docs/2-implementation-and-testing-plan.md`
- `DEVELOPING.md`
- `pyproject.toml`
- `uv.lock`
- `tox.ini`
- `charmcraft.yaml`

Module intent:

- `src/charm.py`: Juju wiring, status orchestration, relation event handling
- `src/lxd.py`: subprocess wrappers and readonly LXD helpers
- `src/inventory.py`: local LXD facts, snap facts, cluster facts
- `src/metrics.py`: metrics listener and `prometheus_scrape` provider logic
- `src/logging_config.py`: direct Loki and Alloy syslog integration logic
- `src/cluster_state.py`: leader-side cluster aggregation from peer data
- `src/status.py`: status message composition and workload version policy

## Proposed relation surface

### Metrics

Provide:

- `metrics-endpoint`
  - interface: `prometheus_scrape`

Reason:

- this matches the modern metrics style already used by `alloy-vm`
- the provider-side library injects Juju topology into scrape metadata
- the provider publishes its intended scrape interval to consumers, currently
  `15s`, so downstream scrapers can preserve the expected metrics cadence
- one LXD unit can expose one scrape target cleanly
- subordinate exporters attached to the same machine should expose their own `metrics-endpoint` relations using the same interface so Alloy can scrape each workload directly while preserving source topology

### Direct Loki logging

Require:

- `logging`
  - interface: `loki_push_api`

Reason:

- this is the right fit for `loki-vm`
- it also fits `loki-loadbalancer-vm`
- LXD natively supports direct Loki output via `loki.api.url`

### Alloy log ingestion

Require:

- `syslog`
  - interface: `syslog`

Reason:

- local `alloy-vm` provides `syslog-receiver`
- Alloy does not provide `loki_push_api` to workloads
- for Alloy log ingestion, the charm should configure LXD snap syslog output and
  host syslog forwarding to the related Alloy receiver

This means the log integrations are:

- `loki-vm` or `loki-loadbalancer-vm`: direct LXD to Loki
- `alloy-vm`: LXD to local syslog, host syslog forwarder to Alloy

## Runtime behavior

### Install and start behavior

On install and start:

1. verify `snap list lxd` succeeds
2. verify `snap.lxd.daemon` is active
3. verify the local API is reachable over the local socket
4. collect:
   - snap version and revision
   - LXD server version
   - `server_clustered`
   - local server name
   - current `core.metrics_address`
   - current `loki.*` settings
   - current `daemon.syslog` snap setting
5. set blocked status if the host is not a valid existing LXD installation

### Status behavior

Per unit `ActiveStatus` should include the current snap version and local role.

Examples:

- `active: snap 5.21.3 rev 33110, standalone`
- `active: snap 5.21.3 rev 33110, cluster member lxd-node2`

Leader workload version:

- leader should set workload version from the local LXD server version
- follower units may also set their unit workload version, but the plan should
  treat the leader's LXD version as the canonical model view

### Cluster awareness behavior

Each unit should publish into peer data:

- snap version
- snap revision
- LXD server version
- server name
- `server_clustered`
- cluster member name
- cluster address facts needed for diagnostics
- whether metrics are enabled locally
- whether a log sink is configured locally

Leader aggregation should detect:

- all units standalone
- all units in the same cluster
- mixed standalone and clustered state
- cluster UUID mismatch
- member-count mismatch
- version mismatch

The initial charm should block on inconsistent cluster state rather than trying
to reconcile it.

## Metrics plan

### Listener policy

The charm should manage only `core.metrics_address`.

Default behavior:

- if a `metrics-endpoint` relation exists, enable `core.metrics_address`
- if no metrics relation exists, disable `core.metrics_address`

Address policy:

- prefer the unit ingress address from Juju binding
- bind on the standard LXD metrics port
- keep writes local to the unit

### Scrape publication

Use `MetricsEndpointProvider` from
`charms.prometheus_k8s.v0.prometheus_scrape`.

Provider payload should include:

- path: `/1.0/metrics`
- unit address
- unit name
- scrape metadata with Juju topology

Related subordinate exporter policy:

- `prometheus-node-exporter`
- `prometheus-zfs-exporter`
- `prometheus-ipmi-exporter`

should all prefer `metrics-endpoint` with interface `prometheus_scrape` and may
keep older Prometheus-specific relations only for compatibility.

That gives one consistent machine-observability path:

```text
subordinate exporter -> alloy-vm:metrics-endpoint -> alloy-vm:send-remote-write -> mimir-vm
```

### TLS and trust

LXD metrics commonly need trusted client access.

Plan:

- issue relation-scoped client credentials for metrics consumers
- add the client certificate to the LXD trust store with metrics-only access
- publish `tls_config` through the `prometheus_scrape` relation

This should reuse the proven pattern from `charm-lxd`, but moved into a
smaller, observability-only implementation.

## Logging plan

### Direct Loki path

When related to `loki-vm` or `loki-loadbalancer-vm`:

- read the endpoint from `loki_push_api`
- configure:
  - `loki.api.url`
  - `loki.auth.username` if ever provided
  - `loki.auth.password` if ever provided
  - `loki.types` with sensible defaults for daemon and lifecycle events
- clear `daemon.syslog` if native Loki output is chosen

### Alloy syslog path

When related to `alloy-vm` over `syslog`:

- enable `daemon.syslog`
- configure host syslog forwarding to the receiver endpoint
- preserve enough labels or message structure to identify:
  - model
  - application
  - unit
  - LXD member

### Precedence and conflict policy

If both direct Loki and Alloy syslog relations are present:

- prefer direct Loki output
- report a clear status or log warning that Alloy syslog is not active

This keeps behavior deterministic.

## Status and version plan

Per-unit status should be composed from:

- local LXD validity
- local observability configuration state
- peer-derived cluster consistency state

Priority order:

1. blocked: invalid or missing local LXD
2. blocked: inconsistent cluster
3. waiting/maintenance: relation or credential transition in progress
4. active: local observability configured correctly

Workload version:

- on the leader, set workload version to local LXD server version
- if cluster versions diverge in a future phase, show that in status while still
  using the leader's local version as the model workload version

## Testing environment plan

Use three existing Ubuntu hosts with preinstalled LXD and passwordless sudo.

Development topology:

- `lxd-node1`
- `lxd-node2`
- `lxd-node3`

Use them as Juju manual machines.

Expected test modes:

1. standalone node with valid LXD
2. three-unit non-clustered deployment
3. three-unit pre-clustered LXD deployment

Reset strategy:

- rely on machine snapshots between runs
- keep charm behavior idempotent so repeated deploy/remove cycles are safe

## Integration scenarios

Phase-complete integration scenarios should cover:

1. direct Loki path
   - `lxd-host` on 3 manual machines
   - related to `loki-vm` or consumed `loki-loadbalancer-vm`

2. Alloy path
   - `lxd-host` on 3 manual machines
   - `alloy-vm` in the same model
   - `lxd-host:metrics-endpoint` related to `alloy-vm:metrics-endpoint`
   - `lxd-host:syslog` related to `alloy-vm:syslog-receiver`
   - `alloy-vm:send-remote-write` related to consumed `mimir-vm`
   - `alloy-vm:send-loki-logs` related to consumed `loki-loadbalancer-vm`

## Delivery phases

### Phase 0: Scaffold

- [x] Create the repository skeleton.
- [x] Add `charmcraft.yaml`.
- [x] Add `pyproject.toml`.
- [x] Add `tox.ini`.
- [x] Generate and commit `uv.lock`.
- [x] Add the initial docs skeleton.

Exit:

- [x] `tox -e lint`
- [x] `tox -e unit`
- [x] `charmcraft pack`

### Phase 1: Existing-LXD validation and inventory

- [x] Add local validation helpers.
- [x] Add snap and LXD inventory collection.
- [x] Add unit status rendering.
- [x] Add leader workload version handling.

Exit:

- [x] Block when LXD is missing.
- [x] Report active when LXD is present.
- [x] Include the snap version in status.

### Phase 2: Metrics

- [x] Add the `prometheus_scrape` provider.
- [x] Manage the metrics listener.
- [x] Add metrics trust and TLS config.

Exit:

- [x] Alloy can scrape one unit.
- [x] Juju topology is present in scrape metadata.

### Phase 3: Direct Loki

- [x] Add the `loki_push_api` consumer.
- [x] Add relation-driven `loki.*` reconciliation.
- [x] Add sink precedence logic.

Exit:

- [x] A `loki-vm` relation configures LXD logging.
- [x] A `loki-loadbalancer-vm` relation also works.

### Phase 4: Alloy syslog path

- [x] Add `syslog` consumer logic.
- [x] Add the host syslog forwarding helper.
- [x] Add `daemon.syslog` management.

Exit:

- [x] A related Alloy instance receives remote syslog from LXD hosts.
- [x] Forwarding includes useful topology labels.

### Phase 5: Cluster awareness

- [x] Add peer aggregation.
- [x] Add cluster consistency checks.
- [x] Add a cluster-aware status summary.

Exit:

- [x] A 3-node cluster reports healthy aggregate state.
- [x] Mismatches block clearly.

### Phase 6: Integration hardening

- [x] Add the Jubilant test suite.
- [x] Add reusable test helpers for node reset and machine attach.
- [x] Update documentation.

Exit:

- [x] The direct Loki scenario passes.
- [x] The Alloy scenario passes.

## Unit test plan

- validate missing LXD snap handling
- validate inactive daemon handling
- validate API query failure handling
- validate inventory parsing for standalone and clustered nodes
- validate active status rendering
- validate leader-only workload version behavior
- validate peer-derived cluster mismatch handling
- validate healthy cluster summary rendering

## Integration test plan

- deploy to a manual machine with a preinstalled LXD snap
- confirm blocked status on a host with LXD removed or daemon stopped
- confirm active status includes snap version on a healthy host
- later phases:
  - confirm metrics relation toggles `core.metrics_address`
  - confirm direct Loki relation configures `loki.api.url`
  - confirm Alloy relation enables syslog forwarding
  - confirm two-of-three clustered deployments block with a member-count mismatch
  - confirm three-node deployments summarize cluster consistency

## Documentation backlog after this plan

- add operator runbooks for missing snap, stopped daemon, and broken local API
- document the exact LXD keys managed by each observability relation
