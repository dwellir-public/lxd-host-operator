"""Read effective LXD container NIC limits without changing daemon or guest state."""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import re
import socket
import ssl
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import quote, urlencode

SOCKET_PATH = "/var/snap/lxd/common/lxd/unix.socket"
PORT = 9445
LIMIT_METRIC = "lxd_network_limit_bits_per_second"
STATE_METRIC = "lxd_network_limit_state"
SUCCESS_METRIC = "lxd_network_limits_collection_success"
TIMESTAMP_METRIC = "lxd_network_limits_last_success_timestamp_seconds"
SUPPORTED_NICS = {"bridged", "p2p", "routed"}
logger = logging.getLogger(__name__)


class CollectionError(RuntimeError):
    """Indicate incomplete inventory without exposing daemon responses or secrets."""


class LocalAPI:
    """Issue only bounded GET requests through the local LXD Unix socket."""

    def __init__(self, path: str = SOCKET_PATH):
        self.path = path

    def get(self, path: str) -> Any:
        """Read one API response, closing the socket on every outcome."""
        connection = http.client.HTTPConnection("localhost", timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(5)
        try:
            connection.sock.connect(self.path)
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                raise CollectionError(f"LXD inventory HTTP {response.status}")
            result = json.loads(response.read(16 * 1024 * 1024 + 1))
            if result.get("type") != "sync":
                raise CollectionError("LXD inventory response was not synchronous")
            return result["metadata"]
        except (OSError, ValueError, KeyError, http.client.HTTPException) as exc:
            raise CollectionError("LXD inventory unavailable or invalid") from exc
        finally:
            connection.close()


def parse_bits(value: str) -> int:
    """Match LXD's integer SI/IEC bit-rate units, including the int64 bound."""
    match = re.fullmatch(r"([0-9]+)(bit|[kMGTPE]bit|[KMGTPE]ibit)?", value)
    if not match:
        raise CollectionError("Unsupported LXD bandwidth value")
    suffix = match[2] or "bit"
    power = "kMGTPE".find(suffix[0]) + 1 if suffix not in {"bit"} else 0
    if "ibit" in suffix:
        power = "KMGTPE".index(suffix[0]) + 1
    result = int(match[1]) * (1024 if "ibit" in suffix else 1000) ** power
    if result > 2**63 - 1:
        raise CollectionError("LXD bandwidth exceeds int64")
    return result


def sample(metric: str, labels: dict[str, str], value: int | float) -> str:
    """Render a deterministic Prometheus sample with escaped label values."""

    def escape(val: str) -> str:
        return val.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    fields = ",".join(f'{key}="{escape(val)}"' for key, val in sorted(labels.items()))
    return f"{metric}{{{fields}}} {value}\n"


def nic_samples(instance: dict, key: str, nic: dict, nictype: str) -> list[str]:
    """Resolve expanded configuration using the same limits.max precedence as LXD."""
    device = nic.get("name") or instance["expanded_config"].get(f"volatile.{key}.name")
    if not device:
        raise CollectionError("Running NIC has no resolved guest interface name")
    labels = {"name": instance["name"], "project": instance["project"], "device": device}
    lines = []
    for direction, option in (("inbound", "limits.ingress"), ("outbound", "limits.egress")):
        identity = {**labels, "direction": direction}
        state = "unsupported"
        if nictype in SUPPORTED_NICS:
            # LXD overwrites BOTH directional settings whenever limits.max is nonempty.
            value = nic.get("limits.max") or nic.get(option, "")
            bits = parse_bits(value) if value else 0
            state = "limited" if bits else "unlimited"
            if bits:
                lines.append(sample(LIMIT_METRIC, identity, bits))
        lines.append(sample(STATE_METRIC, {**identity, "state": state}, 1))
    return lines


def network_type(api: LocalAPI, instance: dict, nic: dict, networks: dict) -> str:
    """Resolve managed bridge devices without assuming all managed networks are bridges."""
    if nic.get("nictype") or not nic.get("network"):
        return nic.get("nictype", "")
    identity = (instance["project"], nic["network"])
    if identity not in networks:
        network = api.get(
            "/1.0/networks/"
            + quote(nic["network"], safe="")
            + "?"
            + urlencode({"project": instance["project"]})
        )
        networks[identity] = "bridged" if network["type"] == "bridge" else ""
    return networks[identity]


def collect(api: LocalAPI) -> list[str]:
    """Discover local running container NICs across projects; ignore other members/VMs."""
    environment = api.get("/1.0")["environment"]
    server_name = environment["server_name"]
    clustered = environment["server_clustered"]
    query = urlencode({"recursion": 1, "all-projects": "true", "instance-type": "container"})
    instances = api.get("/1.0/instances?" + query)
    if not isinstance(instances, list):
        raise CollectionError("LXD inventory did not return an instance list")
    lines: list[str] = []
    networks: dict[tuple[str, str], str] = {}
    identities: set[tuple[str, str, str]] = set()
    for instance in instances:
        if clustered and instance["location"] != server_name:
            continue
        if instance.get("status") == "Error":
            raise CollectionError("Local instance inventory is incomplete")
        if instance["type"] != "container" or instance["status"] not in {"Running", "Frozen"}:
            continue
        for key, nic in sorted(instance["expanded_devices"].items()):
            if nic["type"] != "nic":
                continue
            nictype = network_type(api, instance, nic, networks)
            rendered = nic_samples(instance, key, nic, nictype)
            device = nic.get("name") or instance["expanded_config"].get(f"volatile.{key}.name")
            identity = (instance["project"], instance["name"], device)
            if identity in identities:
                raise CollectionError("Duplicate container NIC identity")
            identities.add(identity)
            lines.extend(rendered)
    return lines


class Collector:
    """Produce fresh metrics per scrape and omit all caps on collection failure."""

    def __init__(self, api: LocalAPI):
        self.api = api
        self.last_success = 0.0

    def render(self) -> bytes:
        """Return gauges and collection health without caching stale limit values."""
        started = time.monotonic()
        try:
            lines = collect(self.api)
        except (CollectionError, KeyError, TypeError, AttributeError):
            logger.warning("LXD network limit collection failed; cap samples omitted")
            lines = []
            success = 0
        else:
            success = 1
            self.last_success = time.time()
        headers = [
            f"# HELP {LIMIT_METRIC} Effective configured per-direction NIC cap in bits/s.\n",
            f"# TYPE {LIMIT_METRIC} gauge\n",
            f"# HELP {STATE_METRIC} Direction state: limited, unlimited or unsupported.\n",
            f"# TYPE {STATE_METRIC} gauge\n",
            f"# HELP {SUCCESS_METRIC} One if the current collection is complete.\n",
            f"# TYPE {SUCCESS_METRIC} gauge\n",
            f"# HELP {TIMESTAMP_METRIC} Last complete collection in Unix seconds.\n",
            f"# TYPE {TIMESTAMP_METRIC} gauge\n",
            "# TYPE lxd_network_limits_collection_duration_seconds gauge\n",
        ]
        lines += [
            f"{SUCCESS_METRIC} {success}\n",
            f"{TIMESTAMP_METRIC} {self.last_success}\n",
            f"lxd_network_limits_collection_duration_seconds {time.monotonic() - started}\n",
        ]
        return "".join(headers + lines).encode()


def handler_for(collector: Collector) -> type[BaseHTTPRequestHandler]:
    """Create a GET-only endpoint exposing no daemon API or configuration payloads."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            """Serve fresh metric samples at the single supported path."""
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = collector.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Avoid logging client data or generating per-scrape journal traffic."""

    return Handler


class TLSServer(HTTPServer):
    """Bound TLS handshakes as well as HTTP reads, without unbounded worker threads."""

    def __init__(self, address: tuple, collector: Collector, context: ssl.SSLContext):
        self.context = context
        super().__init__(address, handler_for(collector))

    def get_request(self) -> tuple:
        """Set the accepted socket deadline before attempting authentication."""
        connection, address = super().get_request()
        connection.settimeout(5)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except (OSError, ssl.SSLError):
            connection.close()
            raise


def main() -> None:
    """Run once to stdout or serve a mutually authenticated TLS metrics endpoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=SOCKET_PATH)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--client-ca")
    args = parser.parse_args()
    collector = Collector(LocalAPI(args.socket))
    if args.once:
        print(collector.render().decode(), end="")
        return
    if not (args.cert and args.key and args.client_ca):
        parser.error("serving requires --cert, --key and --client-ca (mutual TLS)")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    context.load_verify_locations(args.client_ca)
    context.verify_mode = ssl.CERT_REQUIRED
    server_type = type("IPv6Server", (TLSServer,), {"address_family": socket.AF_INET6})
    server_class = server_type if ":" in args.listen else TLSServer
    with server_class((args.listen, args.port), collector, context) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
