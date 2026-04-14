"""Status rendering helpers for the LXD host charm."""

from __future__ import annotations

import ops

from inventory import LocalLXDInventory


def prefixed_message(inventory: LocalLXDInventory, message: str) -> str:
    """Prefix one unit status message with the local LXD hostname."""
    return f"{inventory.server_name}: {message}"


def render_unit_status(
    inventory: LocalLXDInventory, cluster_summary: str = ""
) -> ops.ActiveStatus:
    """Render the active status from local snap/role facts plus any cluster summary."""
    message = f"snap {inventory.snap_version} rev {inventory.snap_revision}, {inventory.role}"
    if cluster_summary:
        message = f"{message}; {cluster_summary}"
    return ops.ActiveStatus(prefixed_message(inventory, message))


def render_blocked_status(
    inventory: LocalLXDInventory, message: str
) -> ops.BlockedStatus:
    """Render a blocked status prefixed with the local LXD hostname."""
    return ops.BlockedStatus(prefixed_message(inventory, message))


def render_waiting_status(
    inventory: LocalLXDInventory, message: str
) -> ops.WaitingStatus:
    """Render a waiting status prefixed with the local LXD hostname."""
    return ops.WaitingStatus(prefixed_message(inventory, message))
