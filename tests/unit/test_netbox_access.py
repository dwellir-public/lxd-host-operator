"""Check bounded read-only grants, identity ownership and safe revocation."""

import copy
import subprocess
from typing import Any
from unittest.mock import Mock

import pytest

from netbox_access import PERMISSIONS, AccessError, Admin, owned_name, reconcile, revoke


@pytest.fixture
def certificates(tmp_path):
    result = []
    for index in range(2):
        cert, key = tmp_path / f"{index}.crt", tmp_path / f"{index}.key"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=client",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        result.append(cert.read_text())
    return result


class API(Admin):
    def __init__(self):
        self.metadata = {
            "api_extensions": ["access_management_tls"],
            "config": {"core.https_address": "0.0.0.0:8443"},
            "environment": {"certificate": "server certificate"},
        }
        self.records = {}
        self.writes = []

    def request(self, method: str, path: str, body: Any = None) -> Any:
        if method == "GET":
            if path == "/1.0":
                return copy.deepcopy(self.metadata)
            result = copy.deepcopy(self.records.get(path))
            if result and "/groups/" in path:
                result["identities"] = {
                    "tls": [
                        identity["name"]
                        for key, identity in self.records.items()
                        if "/identities/" in key and result["name"] in identity["groups"]
                    ]
                }
            return result
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == "POST":
            record = copy.deepcopy(body)
            if "certificate" in record:
                record["tls_certificate"] = record.pop("certificate")
            self.records[path + "/" + record["name"]] = record
        elif method == "PUT":
            self.records[path].update(copy.deepcopy(body))
        elif method == "DELETE":
            del self.records[path]
        else:
            raise AssertionError(method)


def test_registration_is_idempotent_and_rotation_changes_only_owned_identity(certificates):
    api = API()
    first = reconcile(api, "owned", "marker", certificates[0])
    assert first["https-url"] == "https://127.0.0.1:8443"
    assert len(api.writes) == 2
    assert api.records["/1.0/auth/groups/owned"]["permissions"] == PERMISSIONS
    assert reconcile(api, "owned", "marker", certificates[0]) == first
    assert len(api.writes) == 2
    rotated = reconcile(api, "owned", "marker", certificates[1])
    assert rotated["client-fingerprint"] != first["client-fingerprint"]
    assert api.writes[-1][0:2] == ("PUT", "/1.0/auth/identities/tls/owned")
    assert len(api.writes) == 3


def test_managed_group_cannot_keep_accidentally_added_admin_permissions(certificates):
    api = API()
    reconcile(api, "owned", "marker", certificates[0])
    api.records["/1.0/auth/groups/owned"]["permissions"].append(
        {
            "entity_type": "server",
            "url": "/1.0",
            "entitlement": "admin",
        }
    )
    reconcile(api, "owned", "marker", certificates[0])
    assert api.records["/1.0/auth/groups/owned"]["permissions"] == PERMISSIONS


def test_namespace_conflict_never_changes_an_unowned_group(certificates):
    api = API()
    api.records["/1.0/auth/groups/owned"] = {"description": "someone else"}
    with pytest.raises(AccessError, match="ownership conflict"):
        reconcile(api, "owned", "marker", certificates[0])
    assert not api.writes
    revoke(api, "owned", "marker")
    assert not api.writes


@pytest.mark.parametrize("missing", ["capability", "endpoint"])
def test_unsupported_hosts_fail_before_trust_changes(certificates, missing):
    api = API()
    if missing == "capability":
        api.metadata["api_extensions"] = []
    else:
        api.metadata["config"] = {}
    with pytest.raises(AccessError):
        reconcile(api, "owned", "marker", certificates[0])
    assert not api.writes


@pytest.mark.parametrize("certificate", ["", "secret", "-----BEGIN PRIVATE KEY-----abc"])
def test_invalid_client_material_never_reaches_api(certificate):
    api = Mock()
    with pytest.raises(AccessError, match="Invalid inventory client certificate"):
        reconcile(api, "owned", "marker", certificate)
    api.request.assert_not_called()


def test_revocation_preserves_other_group_members_and_is_idempotent(certificates):
    api = API()
    reconcile(api, "owned", "marker", certificates[0])
    api.records["/1.0/auth/identities/tls/other"] = {"name": "other", "groups": ["owned"]}
    revoke(api, "owned", "marker")
    assert "/1.0/auth/identities/tls/owned" not in api.records
    assert "/1.0/auth/groups/owned" in api.records
    assert "/1.0/auth/identities/tls/other" in api.records
    revoke(api, "owned", "marker")
    del api.records["/1.0/auth/identities/tls/other"]
    revoke(api, "owned", "marker")
    assert not api.records


def test_owned_namespace_is_specific_to_model_host_relation_and_consumer():
    original = owned_name("model", "lxd/0", 1, "scraper/0")
    assert original == owned_name("model", "lxd/0", 1, "scraper/0")
    assert original != owned_name("model", "lxd/1", 1, "scraper/0")
    assert original != owned_name("model", "lxd/0", 1, "scraper/1")
    assert original != owned_name("other", "lxd/0", 1, "scraper/0")
