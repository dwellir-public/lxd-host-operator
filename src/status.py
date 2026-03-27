"""Status rendering helpers for the LXD host charm."""

from __future__ import annotations

import ops

from inventory import LocalLXDInventory


def render_unit_status(
    inventory: LocalLXDInventory, cluster_summary: str = ""
) -> ops.ActiveStatus:
    """Render the active status from local snap/role facts plus any cluster summary."""
    message = f"snap {inventory.snap_version} rev {inventory.snap_revision}, {inventory.role}"
    if cluster_summary:
        message = f"{message}; {cluster_summary}"
    return ops.ActiveStatus(message)
