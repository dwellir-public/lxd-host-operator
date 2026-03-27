"""Inventory assembly for local LXD facts used by the charm."""

from __future__ import annotations

from dataclasses import dataclass

import lxd


@dataclass(frozen=True)
class LocalLXDInventory:
    """Represent the phase-1 local inventory needed for status and versioning."""

    snap_version: str
    snap_revision: str
    server_version: str
    server_name: str
    server_clustered: bool
    cluster_members: tuple[str, ...] = ()
    metrics_address: str = ""

    @property
    def role(self) -> str:
        """Return the local role label used in unit status."""
        if self.server_clustered:
            return f"cluster member {self.server_name}"
        return "standalone"


def collect_local_inventory() -> LocalLXDInventory:
    """Validate the host and return the current local LXD inventory."""
    snap_info = lxd.get_snap_info()
    lxd.ensure_daemon_active()
    server_info = lxd.get_server_info()
    cluster_members = lxd.list_cluster_member_names() if server_info.clustered else ()
    return LocalLXDInventory(
        snap_version=snap_info.version,
        snap_revision=snap_info.revision,
        server_version=server_info.version,
        server_name=server_info.server_name,
        server_clustered=server_info.clustered,
        cluster_members=cluster_members,
        metrics_address=lxd.get_config("core.metrics_address"),
    )
