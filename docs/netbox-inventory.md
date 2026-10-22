# NetBox inventory integration

The `netbox-inventory` endpoint provides interface `lxd_inventory`, version 1,
with container scope. It co-locates the NetBox LXD Scraper subordinate on each
principal's machine. LXD must already be installed and initialized; this interface
does not install or bootstrap it.

Deploy both local charm artifacts in the same machine model, configure NetBox
URL/token/source on the subordinate, then integrate:

```bash
juju integrate lxd-host:netbox-inventory netbox-lxd-scraper:lxd-host
```

Each principal unit publishes these string values in its own unit databag,
including nonleaders. No application data or credentials are required.

| Key | Format | Meaning |
| --- | --- | --- |
| `version` | `"1"` | Interface version |
| `ready` | `"true"` or `"false"` | Whether local LXD validation succeeded |
| `hostname` | Plain string, e.g. `host-a` | LXD server name, used to match the NetBox device |
| `clustered` | `"true"` or `"false"` | Local LXD clustering mode |
| `members` | JSON array string, e.g. `["host-a", "host-b"]` | Cluster member names; `[]` for standalone |
| `socket` | `/var/snap/lxd/common/lxd/unix.socket` | Supported local snap socket path |

When validation fails, only `version=1` and `ready=false` remain. The subordinate
must stop consuming the old identity. Regular lifecycle/status reconciliation and
NetBox integration events refresh these facts. Validation failure through other
integration handlers also revokes readiness.

The relation communicates identity and readiness; it grants no socket permissions
and changes no LXD settings. The subordinate manages its own reader/writer service
accounts and NetBox credentials. Dedicated NetBox integration events do not
reconcile metrics, logging or service configuration. Existing observability
behavior and supported bases remain unchanged.

## Validation

`tests/unit/test_netbox_inventory.py` covers leader/nonleader publication,
standalone and clustered facts, stale-data removal, regular updates and early
validation failures in metrics handlers. It asserts that NetBox relation events
do not invoke observability reconciliation. The existing charm unit suite covers
the surrounding lifecycle and observability behavior.

```bash
uv run --group unit pytest tests/unit
charmcraft pack --platform ubuntu@22.04:amd64
charmcraft pack --platform ubuntu@24.04:amd64
```

The scraper subordinate supports Ubuntu 22.04 and 24.04. The principal's existing
Ubuntu 20.04 declaration is preserved; this integration does not extend the
subordinate to that base. A live test requires an explicitly selected disposable
machine model, existing LXD and the subordinate's NetBox fixtures. Unit tests and
builds alone do not verify deployed co-location or event delivery.
