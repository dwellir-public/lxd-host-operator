"""Provision bounded TLS identities for subordinate inventory collectors."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from typing import Any
from urllib.parse import quote, urlsplit

PERMISSIONS = [
    {"entity_type": "server", "url": "/1.0", "entitlement": name}
    for name in ("can_view_events", "can_view_projects")
]


class AccessError(RuntimeError):
    """Expose setup failures without returning raw LXD responses or credentials."""


class Admin:
    """Use the principal's existing authority only to manage owned TLS identities."""

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Issue a bounded local request, returning no exception response bodies."""
        connection = http.client.HTTPConnection("localhost", timeout=15)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(15)
        try:
            connection.sock.connect("/var/snap/lxd/common/lxd/unix.socket")
            connection.request(
                method,
                path,
                json.dumps(body) if body is not None else None,
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status == 404 and method == "GET":
                return None
            if response.status >= 400:
                raise AccessError(f"LXD authorization {method}: HTTP {response.status}")
            return json.loads(response.read())["metadata"]
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            raise AccessError("LXD authorization API unavailable") from None
        finally:
            connection.close()


def owned_name(model_uuid: str, unit: str, relation: int, remote: str) -> tuple[str, str]:
    """Bind the managed namespace to the exact provider/consumer unit pair."""
    marker = f"netbox-inventory:{model_uuid}:{unit}:{relation}:{remote}"
    return "netbox-inventory-" + hashlib.sha256(marker.encode()).hexdigest()[:24], marker


def certificate_fingerprint(certificate: str) -> str:
    """Accept one public X.509 certificate and never private key material."""
    if len(certificate) > 16384 or not re.fullmatch(
        r"\s*-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*",
        certificate,
    ):
        raise AccessError("Invalid inventory client certificate")
    try:
        ssl.create_default_context(cadata=certificate)
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    except (ValueError, ssl.SSLError):
        raise AccessError("Invalid inventory client certificate") from None


def endpoint(metadata: dict) -> str:
    """Use this member's listener, never a shared cluster VIP selected by the client."""
    address = metadata.get("config", {}).get("core.https_address")
    if not address:
        addresses = metadata["environment"].get("addresses", [])
        address = addresses[0] if addresses else ""
    if not address:
        raise AccessError("Enable the LXD HTTPS API before using inventory v2")
    parsed = urlsplit("https://" + address)
    host = parsed.hostname or "127.0.0.1"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_unspecified:
            host = "::1" if ip.version == 6 else "127.0.0.1"
    except ValueError:
        pass
    authority = f"[{host}]" if ":" in host else host
    return f"https://{authority}:{parsed.port or 8443}"


def reconcile(api: Admin, name: str, marker: str, certificate: str) -> dict[str, str]:
    """Create/update only the exact owned group and fine-grained TLS identity."""
    fingerprint = certificate_fingerprint(certificate)
    metadata = api.request("GET", "/1.0")
    if "access_management_tls" not in metadata.get("api_extensions", []):
        raise AccessError("LXD HTTPS inventory requires access_management_tls (5.21.3+)")
    url = endpoint(metadata)
    group_path = "/1.0/auth/groups/" + quote(name, safe="")
    group = api.request("GET", group_path)
    desired = {"description": marker, "permissions": PERMISSIONS}
    if group is None:
        api.request("POST", "/1.0/auth/groups", {"name": name, **desired})
    elif group.get("description") != marker:
        raise AccessError("Inventory authorization group ownership conflict")
    elif sorted(group.get("permissions", []), key=lambda p: p["entitlement"]) != PERMISSIONS:
        api.request("PUT", group_path, desired)
    path = "/1.0/auth/identities/tls/" + quote(name, safe="")
    identity = api.request("GET", path)
    if identity is None:
        api.request(
            "POST",
            "/1.0/auth/identities/tls",
            {
                "name": name,
                "certificate": certificate,
                "groups": [name],
            },
        )
    elif identity.get("name") != name:
        raise AccessError("Inventory TLS identity ownership conflict")
    elif identity.get("tls_certificate", "").strip() != certificate.strip() or identity.get(
        "groups"
    ) != [name]:
        api.request("PUT", path, {"name": name, "tls_certificate": certificate, "groups": [name]})
    return {
        "https-url": url,
        "server-certificate": metadata["environment"]["certificate"],
        "client-fingerprint": fingerprint,
    }


def revoke(api: Admin, name: str, marker: str) -> None:
    """Revoke an owned identity; preserve groups that have other consumers."""
    group_path = "/1.0/auth/groups/" + quote(name, safe="")
    group = api.request("GET", group_path)
    if group is None or group.get("description") != marker:
        return
    path = "/1.0/auth/identities/tls/" + quote(name, safe="")
    identity = api.request("GET", path)
    if identity is not None and identity.get("name") == name:
        api.request("DELETE", path)
    remaining = api.request("GET", group_path)
    if remaining is not None and not any(remaining.get("identities", {}).values()):
        api.request("DELETE", group_path)
