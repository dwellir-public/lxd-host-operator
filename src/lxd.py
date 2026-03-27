"""Helpers for validating, inspecting, and configuring a local LXD installation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass


class LXDValidationError(RuntimeError):
    """Raised when the local host is not a usable pre-installed LXD node."""


@dataclass(frozen=True)
class SnapInfo:
    """Represent the locally installed LXD snap revision and version."""

    version: str
    revision: str


@dataclass(frozen=True)
class ServerInfo:
    """Represent the LXD server facts returned from the local socket API."""

    version: str
    server_name: str
    clustered: bool


def list_cluster_member_names() -> tuple[str, ...]:
    """Return cluster member names from the local LXD daemon."""
    output = run_command("lxc", "cluster", "list", "--format=json")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LXDValidationError("LXD cluster list returned invalid JSON") from exc

    members: list[str] = []
    for item in payload:
        name = str(item.get("server_name") or item.get("name") or "").strip()
        if name:
            members.append(name)
    if not members:
        raise LXDValidationError("LXD cluster list did not report any members")
    return tuple(sorted(set(members)))


@dataclass(frozen=True)
class TrustEntry:
    """Represent one trusted certificate entry known to the local LXD daemon."""

    name: str
    fingerprint: str
    certificate: str
    type: str


def run_command(*args: str) -> str:
    """Run a command and return trimmed stdout, surfacing stderr in failures."""
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise LXDValidationError(f"required command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise LXDValidationError(detail) from exc
    return completed.stdout.strip()


def get_snap_info() -> SnapInfo:
    """Return the installed LXD snap version and revision from `snap list`."""
    output = run_command("snap", "list", "lxd")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise LXDValidationError("LXD snap list output was incomplete")

    parts = lines[1].split()
    if len(parts) < 3 or parts[0] != "lxd":
        raise LXDValidationError("LXD snap is not installed")
    return SnapInfo(version=parts[1], revision=parts[2])


def ensure_daemon_active() -> None:
    """Verify the LXD daemon service is active in the snap service table."""
    output = run_command("snap", "services", "lxd")
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "lxd.daemon":
            if parts[2] != "active":
                raise LXDValidationError("LXD daemon is not active")
            return
    raise LXDValidationError("LXD daemon service was not listed by snap")


def get_server_info() -> ServerInfo:
    """Query the local LXD API and return the server version and role facts."""
    output = run_command("lxc", "query", "/1.0")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LXDValidationError("LXD API returned invalid JSON") from exc

    environment = payload.get("environment", {})
    version = str(environment.get("server_version", "")).strip()
    server_name = str(environment.get("server_name", "")).strip()
    clustered = bool(environment.get("server_clustered", False))

    if not version:
        raise LXDValidationError("LXD API did not report a server version")
    if not server_name:
        raise LXDValidationError("LXD API did not report a server name")

    return ServerInfo(version=version, server_name=server_name, clustered=clustered)


def get_config(key: str) -> str:
    """Return the current value of one LXD server config key."""
    return run_command("lxc", "config", "get", key)


def set_config(key: str, value: str) -> None:
    """Set one LXD server config key, retrying transient daemon-side validation failures."""
    retry_command("lxc", "config", "set", key, value)


def unset_config(key: str) -> None:
    """Unset one LXD server config key, retrying transient daemon-side validation failures."""
    retry_command("lxc", "config", "unset", key)


def get_snap_option(key: str) -> str:
    """Return one LXD snap option, treating absent keys as unset."""
    try:
        return run_command("snap", "get", "lxd", key)
    except LXDValidationError:
        return ""


def set_snap_option(key: str, value: str) -> None:
    """Set one LXD snap option, retrying short-lived snap hook races."""
    retry_command("snap", "set", "lxd", f"{key}={value}")


def unset_snap_option(key: str) -> None:
    """Unset one LXD snap option, retrying short-lived snap hook races."""
    retry_command("snap", "unset", "lxd", key)


def retry_command(*args: str, attempts: int = 10, delay_seconds: float = 2.0) -> str:
    """Run an LXD command repeatedly to smooth over short-lived daemon validation races."""
    last_error: LXDValidationError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return run_command(*args)
        except LXDValidationError as exc:
            last_error = exc
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)

    assert last_error is not None
    raise last_error


def list_trust_entries() -> list[TrustEntry]:
    """Return the trusted certificates currently known to local LXD."""
    output = run_command("lxc", "config", "trust", "list", "--format=json")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LXDValidationError("LXD trust list returned invalid JSON") from exc

    entries: list[TrustEntry] = []
    for item in payload:
        entries.append(
            TrustEntry(
                name=str(item.get("name", "")),
                fingerprint=str(item.get("fingerprint", "")),
                certificate=str(item.get("certificate", "")),
                type=str(item.get("type", "")),
            )
        )
    return entries


def ensure_metrics_certificate_trusted(*, trust_name: str, certificate_pem: str) -> None:
    """Ensure a metrics client certificate is present in the LXD trust store."""
    for entry in list_trust_entries():
        if entry.name != trust_name:
            continue
        if entry.type == "metrics" and entry.certificate == certificate_pem:
            return
        try:
            run_command("lxc", "config", "trust", "remove", entry.fingerprint)
        except LXDValidationError as exc:
            if "Certificate not found" not in str(exc):
                raise

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as cert_file:
        cert_file.write(certificate_pem)
        cert_path = cert_file.name
    try:
        run_command(
            "lxc",
            "config",
            "trust",
            "add",
            cert_path,
            "--name",
            trust_name,
            "--type=metrics",
        )
    finally:
        try:
            subprocess.run(("rm", "-f", cert_path), check=False)
        except Exception:
            pass


def remove_trusted_certificate_by_name(trust_name: str) -> None:
    """Remove a trusted certificate by its configured trust name."""
    for entry in list_trust_entries():
        if entry.name == trust_name:
            run_command("lxc", "config", "trust", "remove", entry.fingerprint)
