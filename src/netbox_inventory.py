"""Publish versioned, unit-scoped local identity to the NetBox subordinate."""

from __future__ import annotations

import json

import ops

from inventory import LocalLXDInventory

ENDPOINT = "netbox-inventory"


def publish(charm: ops.CharmBase, inventory: LocalLXDInventory | None) -> None:
    """Publish readiness on every validation, including unavailable LXD states."""
    payload = {"version": "1", "ready": "false"}
    if inventory is not None:
        payload.update(
            {
                "ready": "true",
                "hostname": inventory.server_name,
                "clustered": json.dumps(inventory.server_clustered),
                "members": json.dumps(list(inventory.cluster_members)),
                "socket": "/var/snap/lxd/common/lxd/unix.socket",
            }
        )
    for relation in charm.model.relations.get(ENDPOINT, []):
        data = relation.data[charm.unit]
        for key in set(data) - set(payload):
            del data[key]
        for key, value in payload.items():
            if data.get(key) != value:
                data[key] = value
