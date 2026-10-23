# Developing `lxd-host`

## Prerequisites

- `uv`
- `tox`
- `charmcraft`
- Python 3.8 or newer

## Local setup

```bash
uv sync --group dev
```

## Validation

```bash
tox -e format
tox -e lint
tox -e unit
```

## Build

```bash
charmcraft pack
```

To build a specific base artifact explicitly:

```bash
charmcraft pack --platform ubuntu@22.04:amd64
charmcraft pack --platform ubuntu@24.04:amd64
charmcraft pack --platform ubuntu@20.04:amd64
```

## Integration tests

Build the charm first, then run the Jubilant scenarios:

```bash
charmcraft pack
tox -e integration
```

If the built charm is not in the repo root, point the tests at it explicitly:

```bash
CHARM_PATH=/path/to/lxd-host.charm tox -e integration
```

When `CHARM_PATH` points at a specific packed artifact such as
`lxd-host_ubuntu@22.04-amd64.charm`, the integration helpers deploy the charm
using the matching Juju base automatically.

Notes:

- the integration suite restores `lxd-node1`, `lxd-node2`, and `lxd-node3`
  from their reusable snapshots before and after each scenario
- the tests temporarily install `lxd` on those VMs because the reusable
  snapshots intentionally do not include the snap
- the Alloy scenario deploys `alloy-vm` with `enable-syslogreceivers=true`
  because the `syslog-receiver` relation is otherwise advertised as disabled

## NetBox inventory HTTPS integration

The `netbox-inventory` integration preserves version 1 fields for existing
consumers and adds HTTPS access for consumers publishing `versions=2` and a
public `client-certificate`. The server needs `access_management_tls` (5.21.3+)
and an existing HTTPS listener. The charm does not expose or rebind the listener.

Each provider/consumer unit pair gets an owned fine-grained TLS identity and
group with exactly `can_view_projects` and `can_view_events` on the server. There
are no management, execution or guest-file permissions. Private keys remain on
the scraper unit. Unit data adds `https-version`, `https-ready`, `https-url`,
`server-certificate`, `client-fingerprint` and, when needed, a safe `https-error`.
Version 1 consumers ignore these additional fields, including after rollback.

Registration is idempotent and certificate changes update the owned identity.
Groups whose ownership marker differs are never changed. Departures revoke only
owned identities; groups used by another identity are preserved. Pending cleanup
is stored and retried after transient API failures on later hooks. These handlers
do not reconcile observability services or restart LXD.

Validate with the scraper's HTTPS inventory tests and both matching local
artifacts. In the authorized `server1` test VM (5.21.8), registration, repeated
reconciliation, rotation and revocation passed. Read-only HTTPS/WSS worked; seven
write/exec/file/admin requests were denied with HTTP 403. Probe identities, group,
project and stopped instance were removed and the temporary loopback listener
restored. Clustered enrollment and full Juju artifact refresh/rollback remain
unverified. Repeat permission checks with disposable fixtures, never live guests.
