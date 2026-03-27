# Developing `lxd-host`

## Prerequisites

- `uv`
- `tox`
- `charmcraft`
- Python 3.10 or newer

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
