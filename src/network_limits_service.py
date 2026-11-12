"""Manage only the optional network-limit collector, never LXD or guest services."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import network_limits

SERVICE = "lxd-host-network-limits.service"
DIRECTORY = Path("/var/lib/lxd-host/network-limits")
UNIT_PATH = Path("/etc/systemd/system") / SERVICE


class ServiceError(RuntimeError):
    """Report collector lifecycle failures without command output or private keys."""


def run(*command: str) -> None:
    """Execute a bounded service command without exposing its output."""
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceError("Network-limit collector service reconciliation failed") from exc


def write_changed(path: Path, content: str, mode: int = 0o600) -> bool:
    """Write an owned file only when its content changes and enforce its mode."""
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    if changed:
        path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return changed


def render_unit(address: str) -> str:
    """Run the standard-library collector with the charm's installed Python."""
    arguments = [
        sys.executable,
        str(DIRECTORY / "collector.py"),
        "--listen",
        address,
        "--cert",
        str(DIRECTORY / "server.crt"),
        "--key",
        str(DIRECTORY / "server.key"),
        "--client-ca",
        str(DIRECTORY / "clients.pem"),
    ]
    command = " ".join(json.dumps(arg.replace("%", "%%")) for arg in arguments)
    return "\n".join(
        [
            "[Unit]",
            "Description=LXD container network limit metrics",
            "After=snap.lxd.daemon.service",
            "",
            "[Service]",
            f"ExecStart={command}",
            "Restart=on-failure",
            "RestartSec=5",
            "User=root",
            "UMask=0077",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "PrivateTmp=true",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def ensure(address: str, certificates: list[str]) -> None:
    """Report local filesystem failures as collector status instead of failing a hook."""
    try:
        _install(address, certificates)
    except OSError as exc:
        raise ServiceError("Cannot write network-limit collector configuration") from exc


def _install(address: str, certificates: list[str]) -> None:
    """Install the opt-in mTLS service, reusing only public relation client certificates."""
    if not certificates:
        raise ServiceError("Network-limit collector has no metrics client certificates")
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    DIRECTORY.chmod(0o700)
    certificate, key = DIRECTORY / "server.crt", DIRECTORY / "server.key"
    if not (certificate.exists() and key.exists()):
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "3650",
            "-subj",
            "/CN=lxd-host-network-limits",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        )
    key.chmod(0o600)
    changed = write_changed(
        DIRECTORY / "collector.py", Path(network_limits.__file__).read_text(encoding="utf-8")
    )
    changed |= write_changed(DIRECTORY / "clients.pem", "\n".join(sorted(set(certificates))))
    unit_changed = write_changed(UNIT_PATH, render_unit(address), 0o644)
    if unit_changed:
        run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", SERVICE)
    if changed or unit_changed:
        run("systemctl", "restart", SERVICE)
    run("systemctl", "is-active", "--quiet", SERVICE)


def disable() -> None:
    """Remove the owned service unit when the option or final metrics relation disappears."""
    if not UNIT_PATH.exists():
        return
    run("systemctl", "disable", "--now", SERVICE)
    UNIT_PATH.unlink()
    run("systemctl", "daemon-reload")


def remove() -> None:
    """Stop the companion and remove only the files this charm owns."""
    disable()
    for name in ("collector.py", "server.crt", "server.key", "clients.pem"):
        (DIRECTORY / name).unlink(missing_ok=True)
