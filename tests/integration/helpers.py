"""Reusable integration helpers for manual-machine LXD host testing."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import jubilant

CONTROLLER = "localhost-localhost"
CLOUD = "localhost"
STACK_OWNER = "admin"
STACK_MODEL = "charmhub-stack-r2-20260317-193315"
LXD_SNAPSHOT = "reusable-20260324-170013"
LXD_CHANNEL = "5.21/stable"
SSH_PRIVATE_KEY = pathlib.Path("/home/erik/.ssh/id_ed25519")
SSH_PUBLIC_KEY = pathlib.Path("/home/erik/.ssh/id_ed25519.pub")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Node:
    """Describe one reusable manual machine used for integration coverage."""

    name: str
    address: str
    snapshot: str = LXD_SNAPSHOT


@dataclass(frozen=True)
class Testbed:
    """Carry the active Jubilant model plus the attached manual machines."""

    juju: jubilant.Juju
    machines: dict[str, str]


NODES = (
    Node(name="lxd-node1", address="10.232.126.154"),
    Node(name="lxd-node2", address="10.232.126.254"),
    Node(name="lxd-node3", address="10.232.126.43"),
)


def run_local(*args: str) -> str:
    """Run one local command and return stripped stdout, surfacing stderr on failure."""
    completed = subprocess.run(args, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def ssh(node: Node, command: str) -> str:
    """Run one remote shell command over SSH against the reusable Ubuntu node."""
    return run_local(
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        "-i",
        str(SSH_PRIVATE_KEY),
        f"ubuntu@{node.address}",
        command,
    )


def wait_for_ssh(node: Node, *, timeout: float = 180.0) -> None:
    """Poll SSH readiness until the restored node is reachable again."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ssh(node, "echo ready")
            return
        except subprocess.CalledProcessError:
            time.sleep(2.0)
    raise TimeoutError(f"SSH did not become ready for {node.name} ({node.address})")


def restore_node(node: Node) -> None:
    """Restore one reusable LXD VM back to the known clean snapshot."""
    run_local("lxc", "restore", node.name, node.snapshot)


def restore_nodes(nodes: tuple[Node, ...] = NODES) -> None:
    """Restore all reusable nodes to the clean snapshot and wait for SSH."""
    for node in nodes:
        restore_node(node)
    for node in nodes:
        wait_for_ssh(node)


def prepare_lxd(node: Node) -> None:
    """Install and minimally initialize LXD on one restored reusable node."""
    ssh(
        node,
        (
            "sudo snap list lxd >/dev/null 2>&1 || "
            f"sudo snap install lxd --channel={LXD_CHANNEL}; "
            "sudo usermod -a -G lxd ubuntu; "
            "sudo lxd waitready; "
            "sudo lxd init --minimal >/dev/null 2>&1 || true; "
            "sudo lxc query /1.0 >/dev/null"
        ),
    )


def prepare_nodes(nodes: tuple[Node, ...] = NODES) -> None:
    """Restore all nodes and install a temporary standalone LXD snap for testing."""
    restore_nodes(nodes)
    for node in nodes:
        prepare_lxd(node)


def require_offer(offer_name: str) -> None:
    """Fail fast when the shared monitoring offer is not visible on the controller."""
    output = run_local(
        "juju",
        "find-offers",
        f"{CONTROLLER}:{STACK_MODEL}",
    )
    offer_url = f"{STACK_OWNER}/{STACK_MODEL}.{offer_name}"
    if offer_url not in output:
        raise RuntimeError(f"required offer not found: {offer_url}")


def consume_offer(juju: jubilant.Juju, offer_name: str) -> str:
    """Consume one shared monitoring offer into the temporary integration model."""
    require_offer(offer_name)
    juju.consume(f"{STACK_OWNER}/{STACK_MODEL}.{offer_name}", controller=CONTROLLER)
    return offer_name


def status_json(juju: jubilant.Juju) -> dict[str, Any]:
    """Return `juju status` as parsed JSON for low-level assertions."""
    return json.loads(juju.cli("status", "--format", "json"))


def show_unit_json(juju: jubilant.Juju, unit_name: str) -> dict[str, Any]:
    """Return one unit record from `juju show-unit --format json`."""
    payload = json.loads(juju.cli("show-unit", unit_name, "--format", "json"))
    return payload[unit_name]


def add_manual_machine(juju: jubilant.Juju, node: Node) -> str:
    """Attach one restored node to the current model and return the machine id."""
    before = set(status_json(juju).get("machines", {}))
    output = juju.cli(
        "add-machine",
        f"ssh:ubuntu@{node.address}",
        "--private-key",
        str(SSH_PRIVATE_KEY),
        "--public-key",
        str(SSH_PUBLIC_KEY),
    )
    match = re.search(r"created machine (\S+)", output)
    if match:
        return match.group(1)
    after = set(status_json(juju).get("machines", {}))
    created = sorted(after - before)
    if len(created) == 1:
        return created[0]
    raise RuntimeError(f"unable to determine machine id for {node.name}: {output}")


def attach_manual_machines(juju: jubilant.Juju, nodes: tuple[Node, ...] = NODES) -> dict[str, str]:
    """Attach all reusable nodes to the current model and wait for them to start."""
    machines: dict[str, str] = {}
    for node in nodes:
        machine = add_manual_machine(juju, node)
        wait_for_machine_running(juju, machine)
        machines[node.name] = machine
    return machines


def deploy_lxd_host(testbed: Testbed, charm_path: pathlib.Path) -> None:
    """Deploy the local charm artifact to all three manual machines."""
    deploy_lxd_app(testbed, charm_path, app_name="lxd-host")


def deploy_lxd_app(testbed: Testbed, charm_path: pathlib.Path, *, app_name: str) -> None:
    """Deploy the local charm artifact to all three manual machines under one app name."""
    machine_ids = list(testbed.machines.values())
    testbed.juju.deploy(
        charm_path,
        app=app_name,
        base=charm_base(charm_path),
        to=machine_ids[0],
    )
    for machine in machine_ids[1:]:
        testbed.juju.add_unit(app_name, to=machine)


def wait_for_exact_app_status(
    juju: jubilant.Juju,
    app: str,
    *,
    status: str,
    message_substring: str | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Poll status until the target application reaches the requested status and message."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = status_json(juju)
        app_info = payload.get("applications", {}).get(app)
        if app_info is None:
            time.sleep(2.0)
            continue
        current = app_info.get("application-status", {}).get("current")
        message = app_info.get("application-status", {}).get("message", "")
        if current == status and (message_substring is None or message_substring in message):
            return payload
        time.sleep(2.0)
    raise TimeoutError(f"{app} did not reach status={status!r} message~={message_substring!r}")


def wait_for_units_active(
    juju: jubilant.Juju, app: str, *, expected_units: int, timeout: float = 900.0
) -> dict[str, Any]:
    """Poll status until all units of one application are active and idle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = status_json(juju)
        app_info = payload.get("applications", {}).get(app)
        if app_info is None:
            time.sleep(2.0)
            continue
        units = app_info.get("units", {})
        if len(units) != expected_units:
            time.sleep(2.0)
            continue
        if app_info.get("application-status", {}).get("current") != "active":
            time.sleep(2.0)
            continue
        if all(
            unit_info.get("workload-status", {}).get("current") == "active"
            and unit_info.get("juju-status", {}).get("current") == "idle"
            for unit_info in units.values()
        ):
            return payload
        time.sleep(2.0)
    raise TimeoutError(f"{app} did not converge to {expected_units} active idle units")


def unit_machine(payload: dict[str, Any], unit_name: str) -> str:
    """Look up the machine id for one unit from a cached status payload."""
    app_name = unit_name.split("/", 1)[0]
    return str(payload["applications"][app_name]["units"][unit_name]["machine"])


def wait_for_machine_running(
    juju: jubilant.Juju, machine: str, *, timeout: float = 600.0
) -> dict[str, Any]:
    """Poll status until one manual machine reports a running machine status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = status_json(juju)
        machine_info = payload.get("machines", {}).get(str(machine))
        if machine_info and machine_info.get("machine-status", {}).get("current") == "running":
            return payload
        time.sleep(2.0)
    raise TimeoutError(f"machine {machine} did not report machine-status=running")


def resolve_charm_path() -> pathlib.Path:
    """Resolve the built local charm artifact from `CHARM_PATH` or the repo root."""
    configured = os.environ.get("CHARM_PATH")
    if configured:
        path = pathlib.Path(configured)
        if not path.exists():
            raise FileNotFoundError(f"CHARM_PATH does not exist: {path}")
        return path

    built = sorted(REPO_ROOT.glob("lxd-host_*.charm"))
    if not built:
        raise FileNotFoundError(
            "no built charm artifact found; set CHARM_PATH or run `charmcraft pack`"
        )
    return built[-1]


def charm_base(charm_path: pathlib.Path) -> str:
    """Infer the charm base from the packed artifact name."""
    match = re.search(r"_(ubuntu@\d+\.\d+)-", charm_path.name)
    if match:
        return match.group(1)
    return "ubuntu@24.04"


def wait_for_machine_command_output(
    juju: jubilant.Juju,
    machine: str,
    command: str,
    *,
    timeout: float = 300.0,
) -> str:
    """Poll one machine command until it succeeds and returns non-empty stdout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            output = juju.ssh(machine, command).strip()
        except Exception:
            time.sleep(2.0)
            continue
        if output:
            return output
        time.sleep(2.0)
    raise TimeoutError(f"command did not produce output on machine {machine}: {command}")


def relation_info_for_endpoint(unit_payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """Return one relation-info entry for the requested endpoint."""
    for relation_info in unit_payload.get("relation-info", []):
        if relation_info.get("endpoint") == endpoint:
            return relation_info
    raise KeyError(f"relation endpoint not found: {endpoint}")


def http_json(url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch one JSON HTTP endpoint using stdlib only."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)
