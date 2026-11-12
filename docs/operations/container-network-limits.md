# Container network limits and generic alerting

This feature is prepared for review. It has not been deployed, enabled, or used
to replace Grafana rules. The existing Polygon rules remain unchanged.

## Collection and identity

`network_limits.py` makes GET requests to the local LXD Unix socket. Each scrape
reads expanded instance configuration across all projects, selects this cluster
member's running/frozen containers, and discovers their NICs. It does not change
profiles, guests, traffic control, the LXD snap, or bandwidth limits. VMs and
physical hosts are outside this collector's scope.

Expanded devices already reflect profile inheritance and instance overrides.
For supported bridged, p2p and routed NICs, a nonempty `limits.max` replaces both
`limits.ingress` and `limits.egress`, matching LXD's enforcement code. Otherwise
the directional values are independent. Accepted integer SI and IEC units follow
LXD's bit-rate parser. Zero/absent values have no finite denominator. Managed
network types are resolved rather than treating an OVN NIC as a bridged NIC.

The `device` label is the interface name inside the guest, not the LXD device
configuration key or the host veth name. Explicit names and LXD's volatile name
are supported. An unresolved name, malformed limit, duplicate identity, or
incomplete local inventory marks the scrape unsuccessful rather than guessing.

| Metric | Meaning |
| --- | --- |
| `lxd_network_limit_bits_per_second` | Positive effective configured cap; labels `project`, `name`, `device`, `direction` |
| `lxd_network_limit_state` | Value 1 with `state=limited`, `unlimited`, or `unsupported` and the same identity |
| `lxd_network_limits_collection_success` | 1 for a complete current collection, otherwise 0 |
| `lxd_network_limits_last_success_timestamp_seconds` | Unix time of the last complete collection; 0 before first success |
| `lxd_network_limits_collection_duration_seconds` | Current collection duration |

Directions are `inbound` (container receive) and `outbound` (container transmit).
No cap samples survive a failed collection. The last-success timestamp remains
for diagnosing freshness. New, deleted, moved and reconfigured containers are
reflected on the next successful scrape; there is no name allowlist or cap cache.

The two scrape jobs derive **the same `lxd_host` label from the target address
without its port**. Join on `(lxd_host, project, name, device, direction)` within
a regional datasource. Do not join only on container names, `job`, or port-bearing
`instance` values. Keep existing Juju labels, job names and edge-site metadata.
Use the same per-member address spelling for both targets (including IPv6
brackets); do not pair a cluster VIP/DNS alias with a member IP.

## Charm-managed hosts

The option `network-limits-enabled` defaults to false. A charm refresh alone does
not install/start the companion, change scrape jobs, or change LXD limits.

When enabled with a ready `metrics-endpoint` integration, the charm:

- starts `lxd-host-network-limits.service` bound to that integration's address on
  port 9445;
- retains native LXD metrics on HTTPS port 8444 and adds a separate HTTPS job;
- uses the existing integration client certificate for authentication to both
  jobs, with a separate generated companion server certificate;
- adds `lxd_host` to both jobs and `lxd_collector=network-limits` to the companion;
- refreshes discovery every 30-second scrape, with a 20-second scrape deadline.

The server requires client certificates. It exposes only `/metrics`, not an LXD
API proxy, and exports no config bodies or credentials. The service runs as root
to read the local LXD socket, with a read-only system filesystem, private temporary
directory and no new privileges. Files and the separate server key reside under
`/var/lib/lxd-host/network-limits/` with restricted permissions. Like the existing
native LXD job, the companion job skips server-name verification and authenticates
with its client certificate; management-network access restrictions still apply.

Changing the bind address, certificate set or collector code restarts only the
companion. It never restarts LXD or a guest. Turning the option off or removing the
last metrics integration removes the companion unit; charm removal also deletes
its owned files. Config reconciliation does not rewrite logging configuration.
Existing native listener/trust behavior is preserved.

## Standalone edge hosts

The companion is also a standard-library program, independent of Juju. For edge
hosts that currently use an independent LXD exporter on port 9092, prepare a
separate installation of `src/network_limits.py` plus a systemd service running:

```text
/usr/bin/python3 /var/lib/lxd-host/network-limits/collector.py \
  --listen HOST_MANAGEMENT_IP --port 9445 \
  --cert /var/lib/lxd-host/network-limits/server.crt \
  --key /var/lib/lxd-host/network-limits/server.key \
  --client-ca /var/lib/lxd-host/network-limits/clients.pem
```

`HOST_MANAGEMENT_IP` is a deployment-time value. Provision server credentials and
an approved metrics client certificate before enabling the service. Distribute
private client keys only through the existing secret-management process; do not
store them in Git. Keep the server key restricted to root. The charm's
[systemd renderer](../../src/network_limits_service.py) is the reference for
service hardening. Do not use that renderer's development Python path for a
standalone installation.

Augment the **existing** native LXD scrape job with the following relabel rule,
without changing its targets, HTTP/TLS settings, credentials or other labels:

```yaml
relabel_configs:
  - source_labels: [__address__]
    regex: '(.+):[0-9]+'
    target_label: lxd_host
    replacement: '$1'
```

Add one companion job per host/member using that exact same management address,
HTTPS port 9445, `/metrics`, a 30-second interval, 20-second timeout, the same host
relabel rule, and static label `lxd_collector: network-limits`. Configure its
`tls_config` with the newly provisioned client certificate/key. Use the proper
server CA and server name where available, or explicitly approve the same
server-verification policy used by the existing native charm job. Do not alter
the existing edge native scrape authentication to accommodate the companion.

For diagnostics in an authorized environment, a one-shot read does not bind a
port or change state:

```bash
sudo python3 src/network_limits.py --once
```

An unsuccessful one-shot still outputs health gauges; inspect
`lxd_network_limits_collection_success`, not just the process exit code.

## Proposed alert rollout

[Draft rules](../../examples/container-network.rules.yaml) deliberately live
outside automatically forwarded rule directories. They are **not** installed by
this charm. The draft warning is >80% continuously for five minutes, using the
current per-direction cap. It has no Polygon or other chain-specific selector.

The draft also monitors collector failure/staleness, missing host coverage or
`lxd_host` relabeling, and finite-cap NICs without matching traffic samples. Zero
and absent limits do not produce division by zero or false finite-cap utilization.
Deduplicate traffic with `max` and caps conservatively with `min`, retaining the
full identity. A collector success gauge of 0 or last-success age of 90 seconds
excludes its cap samples from utilization; health alerts remain responsible for
that lost coverage.

Before enabling these alerts, register/publish an approved runbook and configure
per-region Grafana-managed equivalents or ruler recording rules. Preserve the
existing `Slack-monitoring` destination (`#monitoring-bot`) unless changed during
review. Do not forward the drafts or retire existing rules before that approval.

The read-only discovery on 2026-11-12 found these candidate sources:

| Region | Datasource UID | Targets / containers with eth0 metrics |
| --- | --- | --- |
| Stockholm-1 | `cfju42w986ltsc` | 21 / 422 |
| Stockholm-2 | `ffju3c3tm0k5cf` | 21 / 477 |
| Singapore | `cfjtyzm1tuwaod` | 6 / 87 |
| Tokyo | `df62nrh7sd1c0b` | 1 / 12 |
| OVH EU West | `ef62npiahnh8gc` | 1 / 10 |
| OVH US East | `fentt7xzol1q8c` | 1 / 11 |
| OVH US West | `dfh9vjr60bw8wc` | 1 / 12 |

These are telemetry counts, not proof of complete deployment coverage. Reconcile
all deployed hosts and NICs, including those with names other than eth0. The US
West datasource `eeredj82w3gu8f` points to the same Prometheus; use only one copy.
The shared edge Mimir did not contain these LXD series. Lagos/Tunis coverage was
not present in the inspected Grafana catalog and requires separate reconciliation.

Suggested rollout, requiring separate authorization:

1. Build and deploy the reviewed artifact on an explicitly selected disposable
   nonproduction LXD host. Do not use the historical VM restore fixtures blindly.
2. Validate profile inheritance, an instance override, asymmetric directions,
   `limits.max`, unlimited NICs, multiple projects/NICs, and cluster movement.
   Compare caps with expanded LXD config and live `tc` enforcement in the lab.
3. Verify native/companion targets and host labels in the selected collector and
   regional backend. Check certificate rotation, authentication rejection,
   failures, stale samples and missing telemetry, then controlled alert firing and
   clearance with approved lab fixtures.
4. Canary one host in each of the seven regions before enabling the remaining
   reviewed targets. Track unsupported NICs, missing hosts and regional gaps.
5. After confirming generic alert health and full intended coverage, retire the
   two Polygon-only rules to avoid duplicate notifications.

Rollback disables the option and restores the previous scrape configuration.
For standalone installs, remove only the companion job/service/files and the
added join label. Do not change NIC caps or remove native LXD monitoring. Keep the
Polygon rules until replacements are verified.

## Triage when a warning fires

Owner/escalation: infrastructure on-call. Source of truth: local expanded LXD
configuration, native LXD byte counters, and Linux traffic-control counters.

Find the container's actual host and project from its labels. Inspect its expanded
NIC settings. Use its state API `network.<guest-device>.host_name` to map the guest
NIC to the current host veth. On that host:

- `tc -s filter show dev HOST_VETH ingress` reports container **outbound** policing;
- `tc -s class show dev HOST_VETH` and `tc -s qdisc show dev HOST_VETH` show
  container **inbound** shaping and queueing.

Read two samples at least 30 seconds apart. Check the host/uplink, peer growth,
sync lag and RPC latency before proposing capacity or limit changes. Escalate
persistent impact to infrastructure on-call. This resource alert alone does not
justify a node restart or snapshot. Consider the issue stable when utilization
stays below 80% for 15 minutes and drops/latency/lag have stabilized.

This collector exports **configured caps**, not kernel policer drops or proof
that traffic-control rules match configuration. Short bursts may still be dropped
below the averaged threshold. Exact policer-drop export remains a separate
extension; native interface drop counters do not substitute for it.

## Implementation references and current limits

Semantics were checked against Canonical LXD stable-5.21 at
`d38de57843bf06d764a7b3926036dc8779655003`:
[limit precedence and enforcement](https://github.com/canonical/lxd/blob/d38de57843bf06d764a7b3926036dc8779655003/lxd/device/device_utils_network.go#L451)
and [bit-rate units](https://github.com/canonical/lxd/blob/d38de57843bf06d764a7b3926036dc8779655003/shared/units/units.go#L84).

The all-project recursive list may involve other cluster members. If LXD cannot
complete that request within the local read deadline, collection becomes
unhealthy and omits caps; it does not reuse old values. Validate that failure
mode and collection duration in a cluster canary. Very large inventory responses
are bounded at 16 MiB and similarly fail closed. This first implementation does
not claim kernel-enforcement validation or production delivery evidence.
