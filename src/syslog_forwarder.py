"""Host syslog forwarding helpers for Alloy remote-syslog ingestion."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

RSYSLOG_CONFIG_PATH = Path("/etc/rsyslog.d/90-lxd-host-forward.conf")


@dataclass(frozen=True)
class SyslogTarget:
    """Describe the remote Alloy receiver that should receive forwarded syslog."""

    address: str
    port: str
    protocol: str


@dataclass(frozen=True)
class SyslogTopology:
    """Describe source identity that should survive Alloy syslog ingestion as labels."""

    model: str
    model_uuid: str
    application: str
    unit: str
    charm: str
    lxd_host: str


def ensure_forwarding(target: SyslogTarget, topology: SyslogTopology) -> None:
    """Install or update a narrow rsyslog forwarder for LXD-tagged messages."""
    ensure_rsyslog_installed()
    content = render_config(target, topology)
    current = (
        RSYSLOG_CONFIG_PATH.read_text(encoding="utf-8") if RSYSLOG_CONFIG_PATH.exists() else ""
    )
    if current == content:
        ensure_rsyslog_active()
        return
    RSYSLOG_CONFIG_PATH.write_text(content, encoding="utf-8")
    validate_rsyslog_config()
    ensure_rsyslog_active()
    restart_rsyslog()


def disable_forwarding() -> None:
    """Remove the managed rsyslog forwarding file when syslog mode is inactive."""
    if not RSYSLOG_CONFIG_PATH.exists():
        return
    RSYSLOG_CONFIG_PATH.unlink()
    validate_rsyslog_config()
    restart_rsyslog()


def render_config(target: SyslogTarget, topology: SyslogTopology) -> str:
    """Render a managed rsyslog config that forwards only LXD-tagged lines."""
    action_lines = [
        "  action(",
        '    type="omfwd"',
        f'    target="{target.address}"',
        f'    port="{target.port}"',
        f'    protocol="{target.protocol}"',
        '    template="LxdHostForwardFormat"',
        '    action.resumeRetryCount="-1"',
        '    queue.type="linkedList"',
        '    queue.filename="lxd_host_remote_syslog"',
        '    queue.saveOnShutdown="on"',
    ]
    if target.protocol == "tcp":
        action_lines.append('    TCP_Framing="octet-counted"')
    action_lines.extend(["  )", "  stop", "}"])
    action_block = "\n".join(action_lines)

    return "\n".join(
        [
            "# Managed by the lxd-host charm.",
            "template(",
            '  name="LxdHostForwardFormat"',
            '  type="string"',
            '  string="<%pri%>1 %timestamp:::date-rfc3339% '
            f"{topology.lxd_host} {topology.application} {topology.unit} - "
            f'[juju@47450 model=\\"{topology.model}\\" model_uuid=\\"{topology.model_uuid}\\" '
            f'application=\\"{topology.application}\\" unit=\\"{topology.unit}\\" '
            f'charm=\\"{topology.charm}\\" lxd_host=\\"{topology.lxd_host}\\"] '
            '%msg:::drop-last-lf%\\n"',
            ")",
            "",
            'if (re_match($programname, ".*lxd.*") or re_match($syslogtag, ".*lxd.*")) then {',
            action_block,
            "",
        ]
    )


def ensure_rsyslog_installed() -> None:
    """Install rsyslog on the host when the forwarding helper needs it."""
    if subprocess.run(("dpkg", "-s", "rsyslog"), capture_output=True, check=False).returncode == 0:
        return
    subprocess.run(("apt-get", "update"), check=True)
    subprocess.run(
        ("apt-get", "install", "-y", "rsyslog"),
        check=True,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )


def validate_rsyslog_config() -> None:
    """Validate the live rsyslog configuration before restarting the service."""
    subprocess.run(("rsyslogd", "-N1"), check=True, capture_output=True, text=True)


def ensure_rsyslog_active() -> None:
    """Enable and start rsyslog so the forwarding rule can take effect."""
    subprocess.run(("systemctl", "enable", "--now", "rsyslog"), check=True)


def restart_rsyslog() -> None:
    """Restart rsyslog after a managed configuration change."""
    subprocess.run(("systemctl", "restart", "rsyslog"), check=True)
