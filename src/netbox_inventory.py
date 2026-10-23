"""Publish local identity and provision read-only HTTPS access for subordinates."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import ops

import netbox_access
from inventory import LocalLXDInventory

if TYPE_CHECKING:
    from charm import LxdHostCharm

ENDPOINT = "netbox-inventory"
LOGGER = logging.getLogger(__name__)


def prune(charm: LxdHostCharm, relation_id: int | None = None, unit: str | None = None) -> None:
    """Retry exact owned revocations after departure, including temporary API failure."""
    saved = {key: dict(value) for key, value in charm._netbox_state.registrations.items()}
    live = {
        (str(relation.id), remote.name)
        for relation in charm.model.relations.get(ENDPOINT, [])
        for remote in relation.units
    }
    for key, entry in list(saved.items()):
        forced = str(relation_id) == entry["relation"] and (unit is None or unit == entry["unit"])
        if not forced and (entry["relation"], entry["unit"]) in live:
            continue
        try:
            netbox_access.revoke(netbox_access.Admin(), entry["name"], entry["marker"])
        except netbox_access.AccessError:
            LOGGER.warning("Inventory TLS revocation pending; will retry on the next hook")
            continue
        del saved[key]
    charm._netbox_state.registrations = saved


def https_payload(charm: LxdHostCharm, relation: ops.Relation) -> dict[str, str]:
    """Use additive v2 fields so old consumers can still be refreshed or rolled back."""
    requested = [
        remote
        for remote in relation.units
        if "2" in relation.data[remote].get("versions", "").split(",")
    ]
    if not requested:
        return {}
    payload = {"https-version": "2", "https-ready": "false"}
    if len(requested) != 1:
        return {**payload, "https-error": "Expected one co-located inventory consumer"}
    remote = requested[0]
    certificate = relation.data[remote].get("client-certificate", "")
    name, marker = netbox_access.owned_name(
        charm.model.uuid, charm.unit.name, relation.id, remote.name
    )
    try:
        netbox_access.certificate_fingerprint(certificate)
        saved = {key: dict(value) for key, value in charm._netbox_state.registrations.items()}
        saved[f"{relation.id}:{remote.name}"] = {
            "name": name,
            "marker": marker,
            "relation": str(relation.id),
            "unit": remote.name,
        }
        charm._netbox_state.registrations = saved
        registered = netbox_access.reconcile(netbox_access.Admin(), name, marker, certificate)
    except netbox_access.AccessError as error:
        return {**payload, "https-error": str(error)}
    return {**payload, **registered, "https-ready": "true"}


def publish(charm: LxdHostCharm, inventory: LocalLXDInventory | None) -> None:
    """Keep the v1 contract unchanged; append HTTPS access for requesting units."""
    prune(charm)
    for relation in charm.model.relations.get(ENDPOINT, []):
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
            payload.update(https_payload(charm, relation))
        data = relation.data[charm.unit]
        for key in set(data) - set(payload):
            del data[key]
        for key, value in payload.items():
            if data.get(key) != value:
                data[key] = value
