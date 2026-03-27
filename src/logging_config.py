"""Relation-driven logging configuration for the LXD host charm."""

from __future__ import annotations

from dataclasses import dataclass

import ops

import lxd
import syslog_forwarder

LOGGING_RELATION_NAME = "logging"
SYSLOG_RELATION_NAME = "syslog"
LOKI_API_URL_KEY = "loki.api.url"
LOKI_AUTH_USERNAME_KEY = "loki.auth.username"
LOKI_AUTH_PASSWORD_KEY = "loki.auth.password"
LOKI_TYPES_KEY = "loki.types"
DAEMON_SYSLOG_KEY = "daemon.syslog"
DEFAULT_LOKI_TYPES = "logging,lifecycle"


class TransientLokiError(RuntimeError):
    """Raised when LXD rejects direct Loki config due to a transient upstream readiness issue."""


@dataclass(frozen=True)
class SyslogRelationTarget:
    """Represent one ready remote syslog receiver published by Alloy."""

    address: str
    port: str
    protocol: str


def reconcile(charm: ops.CharmBase, local_inventory) -> bool:
    """Reconcile native Loki first, then fall back to Alloy syslog forwarding."""
    if not (endpoint_url := active_loki_endpoint(charm)):
        if target := active_syslog_target(charm):
            clear_loki_configuration()
            enable_daemon_syslog()
            syslog_forwarder.ensure_forwarding(
                syslog_forwarder.SyslogTarget(
                    address=target.address,
                    port=target.port,
                    protocol=target.protocol,
                ),
                syslog_forwarder.SyslogTopology(
                    model=charm.model.name,
                    model_uuid=str(charm.model.uuid),
                    application=charm.app.name,
                    unit=charm.unit.name,
                    charm=charm.meta.name,
                    lxd_host=local_inventory.server_name,
                ),
            )
            return True

        clear_loki_configuration()
        disable_daemon_syslog()
        syslog_forwarder.disable_forwarding()
        return False

    try:
        set_config_if_needed(LOKI_API_URL_KEY, endpoint_url)
        set_config_if_needed(LOKI_TYPES_KEY, DEFAULT_LOKI_TYPES)
        unset_if_set(LOKI_AUTH_USERNAME_KEY)
        unset_if_set(LOKI_AUTH_PASSWORD_KEY)
        disable_daemon_syslog()
        syslog_forwarder.disable_forwarding()
    except lxd.LXDValidationError as exc:
        if _is_transient_loki_validation_error(exc):
            raise TransientLokiError(str(exc)) from exc
        raise
    return True


def active_loki_endpoint(charm: ops.CharmBase) -> str | None:
    """Return one deterministic Loki base endpoint from the consumer library."""
    endpoints = [
        normalise_loki_endpoint(endpoint.get("url", "").strip())
        for endpoint in charm._loki_consumer.loki_endpoints
        if isinstance(endpoint, dict)
    ]
    endpoints = sorted(endpoint for endpoint in endpoints if endpoint)
    if not endpoints:
        return None
    return endpoints[0]


def normalise_loki_endpoint(endpoint_url: str) -> str:
    """Convert a Loki push URL into the base URL expected by LXD."""
    return endpoint_url.removesuffix("/loki/api/v1/push")


def active_syslog_target(charm: ops.CharmBase) -> SyslogRelationTarget | None:
    """Return one ready remote syslog receiver from the `syslog` relation data."""
    candidates: list[SyslogRelationTarget] = []
    for relation in charm.model.relations.get(SYSLOG_RELATION_NAME, []):
        remote_app = relation.app
        if remote_app is None:
            continue
        relation_data = relation.data[remote_app]
        if relation_data.get("ready") != "true":
            continue
        address = relation_data.get("address", "").strip()
        port = relation_data.get("port", "").strip()
        protocol = preferred_protocol(
            relation_data.get("recommended-protocol", "").strip(),
            relation_data.get("protocols", "").strip(),
        )
        if not address or not port or not protocol:
            continue
        candidates.append(SyslogRelationTarget(address=address, port=port, protocol=protocol))

    if not candidates:
        return None
    return sorted(candidates, key=lambda candidate: (candidate.address, candidate.port))[0]


def preferred_protocol(recommended: str, supported: str) -> str:
    """Choose one usable protocol from the Alloy-published syslog receiver contract."""
    supported_protocols = [
        protocol.strip() for protocol in supported.split(",") if protocol.strip()
    ]
    if recommended and recommended in supported_protocols:
        return recommended
    if supported_protocols:
        return supported_protocols[0]
    return ""


def clear_loki_configuration() -> None:
    """Clear direct-Loki settings when no active logging endpoint exists."""
    unset_if_set(LOKI_API_URL_KEY)
    unset_if_set(LOKI_AUTH_USERNAME_KEY)
    unset_if_set(LOKI_AUTH_PASSWORD_KEY)
    unset_if_set(LOKI_TYPES_KEY)


def enable_daemon_syslog() -> None:
    """Enable the LXD snap syslog bridge only when it is not already active."""
    if lxd.get_snap_option(DAEMON_SYSLOG_KEY).lower() == "true":
        return
    lxd.set_snap_option(DAEMON_SYSLOG_KEY, "true")


def disable_daemon_syslog() -> None:
    """Disable the LXD snap syslog bridge when direct syslog forwarding is inactive."""
    current_value = lxd.get_snap_option(DAEMON_SYSLOG_KEY).lower()
    if current_value in {"", "false"}:
        return
    lxd.set_snap_option(DAEMON_SYSLOG_KEY, "false")


def set_config_if_needed(key: str, value: str) -> None:
    """Set an LXD config key only when the desired value differs."""
    if lxd.get_config(key) == value:
        return
    lxd.set_config(key, value)


def unset_if_set(key: str) -> None:
    """Unset an LXD config key when it currently has a value."""
    if not lxd.get_config(key):
        return
    lxd.unset_config(key)


def _is_transient_loki_validation_error(exc: lxd.LXDValidationError) -> bool:
    """Return True when LXD rejected Loki config due to transient endpoint readiness."""
    message = str(exc)
    return (
        "failed to connect to Loki" in message
        or "Loki is not ready" in message
        or "404 Not Found" in message
    )
