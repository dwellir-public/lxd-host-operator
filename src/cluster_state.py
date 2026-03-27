"""Peer relation exchange and leader-side cluster consistency checks."""

from __future__ import annotations

import json
from dataclasses import dataclass

import ops

from inventory import LocalLXDInventory

PEER_RELATION_NAME = "cluster"
APP_HEALTH_KEY = "cluster-health"
APP_MESSAGE_KEY = "cluster-message"
APP_SUMMARY_KEY = "cluster-summary"


@dataclass(frozen=True)
class PeerUnitState:
    """Represent one unit's local LXD identity as published into peer data."""

    snap_version: str
    snap_revision: str
    server_version: str
    server_name: str
    server_clustered: bool
    cluster_members: tuple[str, ...]
    metrics_enabled: bool
    log_sink: str


@dataclass(frozen=True)
class ClusterAssessment:
    """Represent the cluster-wide health result derived from peer relation data."""

    healthy: bool
    message: str = ""
    summary: str = ""


def publish_local_state(
    charm: ops.CharmBase,
    local_inventory: LocalLXDInventory,
    *,
    metrics_enabled: bool,
    log_sink: str,
) -> None:
    """Write this unit's local LXD identity into the peer relation unit databag."""
    relation = peer_relation(charm)
    if relation is None:
        return

    payload = {
        "snap_version": local_inventory.snap_version,
        "snap_revision": local_inventory.snap_revision,
        "server_version": local_inventory.server_version,
        "server_name": local_inventory.server_name,
        "server_clustered": json.dumps(local_inventory.server_clustered),
        "cluster_members": json.dumps(list(local_inventory.cluster_members)),
        "metrics_enabled": json.dumps(metrics_enabled),
        "log_sink": log_sink,
    }
    databag = relation.data[charm.unit]
    for key, value in payload.items():
        if databag.get(key) != value:
            databag[key] = value


def reconcile(charm: ops.CharmBase, local_inventory: LocalLXDInventory) -> ClusterAssessment:
    """Assess peer consistency and publish the leader's result into peer app data."""
    relation = peer_relation(charm)
    if relation is None:
        return ClusterAssessment(healthy=True)

    if charm.unit.is_leader():
        assessment = assess_cluster(collect_peer_states(charm, local_inventory))
        app_databag = relation.data[charm.app]
        app_databag[APP_HEALTH_KEY] = "healthy" if assessment.healthy else "blocked"
        app_databag[APP_MESSAGE_KEY] = assessment.message
        app_databag[APP_SUMMARY_KEY] = assessment.summary
        return assessment

    app_databag = relation.data[charm.app]
    if app_databag.get(APP_HEALTH_KEY) == "blocked":
        return ClusterAssessment(healthy=False, message=app_databag.get(APP_MESSAGE_KEY, ""))
    return ClusterAssessment(healthy=True, summary=app_databag.get(APP_SUMMARY_KEY, ""))


def assess_cluster(states: list[PeerUnitState]) -> ClusterAssessment:
    """Check whether peer-published unit identities describe one coherent cluster."""
    if len(states) <= 1:
        return ClusterAssessment(healthy=True)

    clustered_flags = {state.server_clustered for state in states}
    if len(clustered_flags) > 1:
        return ClusterAssessment(
            healthy=False,
            message="Cluster mismatch: mixed standalone and clustered units",
        )

    if clustered_flags == {False}:
        return ClusterAssessment(healthy=True, summary=f"standalone set {len(states)} units")

    versions = {state.server_version for state in states}
    if len(versions) > 1:
        detail = ", ".join(sorted(versions))
        return ClusterAssessment(
            healthy=False,
            message=f"Cluster mismatch: LXD versions differ ({detail})",
        )

    member_sets = {state.cluster_members for state in states}
    if len(member_sets) > 1:
        return ClusterAssessment(
            healthy=False,
            message="Cluster mismatch: member sets differ across units",
        )

    member_count = len(states[0].cluster_members)
    if member_count != len(states):
        return ClusterAssessment(
            healthy=False,
            message=(
                f"Cluster mismatch: observed {len(states)} units but "
                f"LXD reports {member_count} members"
            ),
        )

    return ClusterAssessment(
        healthy=True,
        summary=f"cluster healthy {len(states)} units/{member_count} members",
    )


def collect_peer_states(
    charm: ops.CharmBase, local_inventory: LocalLXDInventory
) -> list[PeerUnitState]:
    """Collect all parseable peer unit states, including the current local unit."""
    relation = peer_relation(charm)
    if relation is None:
        return []

    states: list[PeerUnitState] = []
    local_state = parse_peer_unit_state(relation.data[charm.unit])
    if local_state is None:
        local_state = PeerUnitState(
            snap_version=local_inventory.snap_version,
            snap_revision=local_inventory.snap_revision,
            server_version=local_inventory.server_version,
            server_name=local_inventory.server_name,
            server_clustered=local_inventory.server_clustered,
            cluster_members=local_inventory.cluster_members,
            metrics_enabled=bool(local_inventory.metrics_address),
            log_sink="unknown",
        )
    states.append(local_state)

    for unit in relation.units:
        parsed = parse_peer_unit_state(relation.data[unit])
        if parsed is not None:
            states.append(parsed)
    return states


def parse_peer_unit_state(databag: ops.RelationDataContent) -> PeerUnitState | None:
    """Parse one peer unit databag into a structured unit state."""
    try:
        snap_version = databag["snap_version"]
        snap_revision = databag["snap_revision"]
        server_version = databag["server_version"]
        server_name = databag["server_name"]
        server_clustered = json.loads(databag["server_clustered"])
        cluster_members = tuple(json.loads(databag.get("cluster_members", "[]")))
        metrics_enabled = bool(json.loads(databag.get("metrics_enabled", "false")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    return PeerUnitState(
        snap_version=snap_version,
        snap_revision=snap_revision,
        server_version=server_version,
        server_name=server_name,
        server_clustered=server_clustered,
        cluster_members=tuple(sorted(cluster_members)),
        metrics_enabled=metrics_enabled,
        log_sink=databag.get("log_sink", ""),
    )


def peer_relation(charm: ops.CharmBase) -> ops.Relation | None:
    """Return the peer relation object when it exists."""
    return charm.model.get_relation(PEER_RELATION_NAME)
