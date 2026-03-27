# LXD Host Charm Architecture

## Overview

`lxd-host` is a machine charm that manages observability-only behavior for an
already installed LXD snap. It does not install LXD, bootstrap a daemon, or
change cluster topology.

The charm is packaged for both Ubuntu 22.04 and Ubuntu 24.04 on amd64.

## Current shape

Phase 1 through 5 keep the charm deliberately small:

- `src/charm.py` owns Juju event wiring and status publication
- `src/lxd.py` wraps readonly shell calls to `snap` and `lxc`
- `src/inventory.py` assembles local LXD facts into a typed data object
- `src/status.py` renders human-facing unit status
- `src/cluster_state.py` publishes per-unit peer facts and computes the
  leader-side cluster assessment
- `src/metrics.py` manages `core.metrics_address`, LXD metrics trust, and
  `prometheus_scrape` relation payloads
- `src/logging_config.py` manages native `loki.*` settings from
  `loki_push_api` relations
- `src/syslog_forwarder.py` manages the host-side rsyslog bridge used for Alloy
  remote-syslog ingestion
- `lib/charms/prometheus_k8s/v0/prometheus_scrape.py` provides the standard
  metrics relation library
- `lib/charms/loki_k8s/v1/loki_push_api.py` provides the standard Loki consumer
  relation library

## Current runtime flow

On install, start, update-status, and leader-elected:

1. validate that the `lxd` snap is installed
2. validate that `lxd.daemon` is active
3. query the local LXD API over the socket with `lxc query /1.0`
4. collect local cluster-member names when the local daemon is clustered
5. publish per-unit LXD facts to the peer relation
6. have the leader assess whether all deployed units agree on versions and
   member sets; this centralizes the cluster verdict in peer application data so
   every unit renders status from the same view instead of each follower trying
   to infer cluster health from whatever peer data it can currently see. The
   assessment compares each unit's published clustered/standalone mode, LXD
   server version, and sorted member list, then blocks on mixed standalone and
   clustered state, divergent LXD versions, mismatched member sets, or a Juju
   unit count that does not match the LXD-reported member count
7. render unit status from the snap revision, local role, and cluster summary
8. when leader, set workload version from the local LXD server version

On `metrics-endpoint` relation events:

1. enable or disable `core.metrics_address` based on relation presence
2. bind metrics to the unit's Juju bind address on port `8444`
3. generate a client certificate for the relation on the leader
4. trust that client certificate locally in LXD as a metrics certificate
5. publish `prometheus_scrape` jobs for `/1.0/metrics` over HTTPS with inline
   client TLS material

The published scrape job also carries an explicit `scrape_interval` of `15s`.
This is part of the provider-side relation contract, not just a local
implementation detail. Consumers such as `alloy-vm` should honor that interval
when rendering their own scrape configuration so the upstream metrics cadence
matches what the LXD charm expects for dashboards and rate-based queries.

On `logging` relation events:

1. read available Loki push endpoints from the consumer relation library
2. pick one deterministic endpoint URL when multiple units are related
3. set `loki.api.url` and `loki.types=logging,lifecycle`
4. clear `loki.auth.*` and `daemon.syslog` so direct Loki takes precedence;
   direct Loki mode writes LXD's native `loki.*` server config and sends
   `logging` plus `lifecycle` events straight from LXD to the related Loki
   endpoint, without going through host syslog or rsyslog
5. clear native Loki config entirely when no direct Loki relation remains

LXD can reject `loki.*` updates transiently during endpoint changes, so the
charm retries those writes before failing the hook.

On `syslog` relation events:

1. read one ready Alloy receiver from relation data; the charm only treats a
   `syslog` relation as usable when the remote app marks it `ready=true` and
   provides an `address`, `port`, and supported `protocols`. It then chooses
   one receiver deterministically, preferring Alloy's `recommended-protocol`
   when that protocol is also listed as supported. Unlike direct Loki mode,
   this path does not use LXD's native `loki.*` settings: Alloy receives RFC5424
   syslog from rsyslog, relabels the stamped Juju and host identity fields into
   Loki labels, and forwards the logs on to Loki itself
2. prefer direct Loki when both logging modes are related
3. enable the LXD snap `daemon.syslog` option when Alloy syslog is selected
4. write one managed rsyslog forwarding file that only forwards LXD-tagged
   messages; the filter matches syslog records whose `$programname` or
   `$syslogtag` contains `lxd`, which is how the charm narrows forwarding to
   messages emitted by the LXD daemon instead of forwarding unrelated host logs
5. stamp `lxd_host`, application, and unit identity into RFC5424 fields Alloy
   already relabels into Loki labels
6. remove the managed rsyslog file and disable `daemon.syslog` when syslog mode
   is no longer active; this happens whenever there is no ready Alloy syslog
   target or when a direct `logging` relation is active and therefore takes
   precedence. Disabling is done by setting the LXD snap option
   `daemon.syslog=false`, and the charm only performs that write when the option
   is currently set to some other value

On `cluster` peer relation events:

1. write local snap, server, and observability facts into unit peer data
2. have the leader aggregate all visible peer-unit reports plus its own local
   inventory
3. block on mixed standalone and clustered state
4. block on divergent LXD server versions
5. block when the deployed unit count does not match the LXD cluster member set
6. publish the leader assessment into peer application data for follower units
7. append the healthy cluster summary to every unit status when the cluster view
   is consistent

## Planned growth

Later phases will add:

- operator runbooks for common local-host validation failures

## Integration harness

Phase 6 adds a Jubilant-backed integration suite under `tests/integration/`.

The test harness:

- restores the reusable `lxd-node1`, `lxd-node2`, and `lxd-node3` VMs before
  and after each scenario
- temporarily installs and minimally initializes LXD on those VMs so the charm
  can continue treating LXD as pre-existing workload state
- attaches the restored VMs as Juju manual machines in a temporary model
- consumes the shared `mimir-vm` and `loki-loadbalancer-vm` offers from the
  monitoring stack model
- verifies one direct-Loki scenario and one Alloy metrics-plus-syslog scenario

## Dashboard asset

The repository also keeps a patched copy of Grafana dashboard `19131` at
`dashboards/19131-grafana-dashboard.json`.

That asset exists to validate the live LXD metrics and Loki label contract
against the dashboard shape users actually import. The local copy carries the
Juju-job compatibility fix and a small set of query corrections for multi-value
instance selection and rootfs filesystem matching.
