"""Exercise collector discovery and transport independently of Juju or live LXD."""

import copy
import json
import socketserver
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

import pytest

from network_limits import (
    CollectionError,
    Collector,
    LocalAPI,
    TLSServer,
    collect,
    parse_bits,
    sample,
)


class API(LocalAPI):
    def __init__(self, instances, clustered=False):
        self.instances = instances
        self.clustered = clustered
        self.calls = []
        self.fail = False
        self.network = "bridge"

    def get(self, path):
        self.calls.append(path)
        if self.fail:
            raise CollectionError("unavailable")
        if path == "/1.0":
            return {"environment": {"server_name": "host-a", "server_clustered": self.clustered}}
        if path.startswith("/1.0/networks/"):
            return {"type": self.network}
        query = parse_qs(urlsplit(path).query)
        assert query == {
            "recursion": ["1"],
            "all-projects": ["true"],
            "instance-type": ["container"],
        }
        return self.instances


def instance(name="guest", project="default", **updates):
    data = {
        "name": name,
        "project": project,
        "location": "host-a",
        "type": "container",
        "status": "Running",
        "expanded_config": {},
        "devices": {"uplink": {"type": "nic", "limits.ingress": "1Mbit"}},
        "expanded_devices": {
            "uplink": {
                "type": "nic",
                "nictype": "bridged",
                "name": "eth0",
                "limits.ingress": "300Mbit",
                "limits.egress": "100Mbit",
            }
        },
    }
    return {**data, **updates}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0),
        ("123", 123),
        ("12bit", 12),
        ("10kbit", 10000),
        ("300Mbit", 300000000),
        ("2Gbit", 2000000000),
        ("1Tbit", 10**12),
        ("1Pbit", 10**15),
        ("1Ebit", 10**18),
        ("2Kibit", 2048),
        ("2Mibit", 2 * 1024**2),
        ("1Gibit", 1024**3),
        ("1Tibit", 1024**4),
        ("1Pibit", 1024**5),
        ("1Eibit", 1024**6),
    ],
)
def test_lxd_bandwidth_units(value, expected):
    assert parse_bits(value) == expected


@pytest.mark.parametrize("value", ["", "-1", "1.5Mbit", "1MB", "1mbit", "1Kbit", "10Ebit"])
def test_rejects_values_lxd_does_not_accept(value):
    with pytest.raises(CollectionError):
        parse_bits(value)


def test_reads_expanded_inheritance_and_asymmetric_caps():
    body = Collector(API([instance()])).render().decode()
    assert 'device="eth0",direction="inbound",name="guest",project="default"} 300000000' in body
    assert 'device="eth0",direction="outbound",name="guest",project="default"} 100000000' in body
    assert "1000000\n" not in body
    assert "lxd_network_limits_collection_success 1" in body


def test_limits_max_takes_precedence_over_both_directional_keys():
    guest = instance()
    guest["expanded_devices"]["uplink"]["limits.max"] = "50Mbit"
    lines = collect(API([guest]))
    assert sum(line.endswith(" 50000000\n") for line in lines) == 2
    assert not any("300000000" in line or "100000000" in line for line in lines)


def test_discovers_new_guests_and_removes_deleted_guests_on_next_scrape():
    api = API([instance()])
    collector = Collector(api)
    assert 'name="guest"' in collector.render().decode()
    api.instances = [instance("replacement")]
    body = collector.render().decode()
    assert 'name="guest"' not in body
    assert 'name="replacement"' in body


def test_cluster_projects_multiple_nics_and_kernel_assigned_interface_name():
    guest = instance()
    guest["expanded_devices"]["second"] = {"type": "nic", "nictype": "routed"}
    guest["expanded_config"]["volatile.second.name"] = "lan0"
    foreign = instance("foreign", location="host-b")
    stopped = instance("stopped", status="Stopped")
    vm = instance("vm", type="virtual-machine")
    body = "".join(collect(API([guest, instance(project="other"), foreign, stopped, vm], True)))
    assert 'project="other"' in body and 'device="lan0"' in body
    assert 'state="unlimited"' in body
    assert all(f'name="{name}"' not in body for name in ["foreign", "stopped", "vm"])


def test_managed_network_resolution_is_cached_and_ovn_is_unsupported():
    guest = instance()
    nic = guest["expanded_devices"]["uplink"]
    nic.pop("nictype")
    nic["network"] = "managed"
    api = API([guest, instance("two", expanded_devices=copy.deepcopy(guest["expanded_devices"]))])
    body = "".join(collect(api))
    assert "300000000" in body
    assert sum(path.startswith("/1.0/networks/") for path in api.calls) == 1
    api.network = "ovn"
    body = "".join(collect(api))
    assert 'state="unsupported"' in body
    assert "lxd_network_limit_bits_per_second" not in body


def test_missing_and_zero_limits_are_explicitly_unlimited():
    guest = instance()
    guest["expanded_devices"]["uplink"]["limits.ingress"] = ""
    guest["expanded_devices"]["uplink"]["limits.egress"] = "0"
    lines = collect(API([guest]))
    assert len(lines) == 2 and all('state="unlimited"' in line for line in lines)


def test_failure_clears_cap_samples_without_advancing_last_success(monkeypatch):
    monkeypatch.setattr("network_limits.time.time", lambda: 1234)
    api = API([instance()])
    collector = Collector(api)
    collector.render()
    api.fail = True
    body = collector.render().decode()
    assert "lxd_network_limits_collection_success 0\n" in body
    assert "lxd_network_limits_last_success_timestamp_seconds 1234\n" in body
    assert not any(
        line.startswith("lxd_network_limit_bits_per_second{") for line in body.splitlines()
    )
    assert "unavailable" not in body


@pytest.mark.parametrize(
    "malformation", ["duplicate", "missing_interface", "missing_expansion", "error"]
)
def test_incomplete_inventory_is_never_treated_as_unlimited(malformation):
    guest = instance()
    guests = [guest]
    if malformation == "duplicate":
        guests.append(copy.deepcopy(guest))
    elif malformation == "missing_interface":
        guest["expanded_devices"]["uplink"].pop("name")
    elif malformation == "missing_expansion":
        guest.pop("expanded_devices")
    else:
        guest["status"] = "Error"
    body = Collector(API(guests)).render().decode()
    assert "lxd_network_limits_collection_success 0\n" in body
    assert not any(
        line.startswith("lxd_network_limit_bits_per_second{") for line in body.splitlines()
    )


def test_prometheus_label_escaping():
    assert sample("example", {"name": 'a"b\\c\n'}, 1) == 'example{name="a\\"b\\\\c\\n"} 1\n'


@pytest.fixture()
def unix_api(tmp_path):
    """Serve the real LXD response envelope over a temporary Unix socket."""
    requested = []
    payloads = {
        "/1.0": {"environment": {"server_name": "host-a", "server_clustered": False}},
        "/1.0/instances": [instance()],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append((self.command, self.path))
            data = payloads.get(urlsplit(self.path).path)
            self.send_response(200 if data is not None else 403)
            self.end_headers()
            self.wfile.write(json.dumps({"type": "sync", "metadata": data}).encode())

        def log_message(self, format, *args):
            pass

    socket_path = str(tmp_path / "lxd.sock")
    server = socketserver.UnixStreamServer(socket_path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalAPI(socket_path), requested, payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_socket_discovery_and_current_profile_changes(unix_api):
    api, requested, payloads = unix_api
    collector = Collector(api)
    assert "300000000" in collector.render().decode()
    payloads["/1.0/instances"][0]["expanded_devices"]["uplink"]["limits.ingress"] = "600Mbit"
    assert "600000000" in collector.render().decode()
    assert len(requested) == 4 and all(method == "GET" for method, _ in requested)
    with pytest.raises(CollectionError, match="HTTP 403"):
        api.get("/denied")


def make_certificate(tmp_path, name):
    certificate, key = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name}",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def test_mtls_endpoint_requires_trusted_client_and_exposes_only_metrics(unix_api, tmp_path):
    api, _, _ = unix_api
    certificate, key = make_certificate(tmp_path, "server")
    client, client_key = make_certificate(tmp_path, "client")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    context.load_verify_locations(client)
    context.verify_mode = ssl.CERT_REQUIRED
    server = TLSServer(("127.0.0.1", 0), Collector(api), context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://127.0.0.1:{server.server_address[1]}"
    consumer = ssl.create_default_context()
    consumer.check_hostname = False
    consumer.verify_mode = ssl.CERT_NONE
    try:
        with pytest.raises((OSError, urllib.error.URLError)):
            urllib.request.urlopen(url + "/metrics", context=consumer, timeout=5)
        consumer.load_cert_chain(client, client_key)
        with urllib.request.urlopen(url + "/metrics", context=consumer, timeout=5) as response:
            assert "lxd_network_limits_collection_success 1" in response.read().decode()
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(url + "/1.0", context=consumer, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
