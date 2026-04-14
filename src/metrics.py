"""Metrics listener, trust, and scrape-publication helpers for LXD."""

from __future__ import annotations

import ipaddress
import json
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

import ops

import cluster_state
import lxd
from inventory import LocalLXDInventory

METRICS_RELATION_NAME = "metrics-endpoint"
METRICS_PORT = 8444
METRICS_PATH = "/1.0/metrics"
METRICS_SCRAPE_INTERVAL = "15s"
METRICS_CONFIG_KEY = "core.metrics_address"
CERTIFICATE_FIELD = "metrics_client_cert"
PRIVATE_KEY_FIELD = "metrics_client_key"
TRUST_NAME_FIELD = "metrics_trust_name"
METRICS_JOB_NAME_FIELD = "metrics_job_name"


@dataclass(frozen=True)
class MetricsCredentials:
    """Represent the client certificate material used to scrape LXD metrics."""

    certificate_pem: str
    private_key_pem: str
    trust_name: str


def has_metrics_relation(charm: ops.CharmBase) -> bool:
    """Return whether the charm currently has any metrics consumer relation."""
    return bool(charm.model.relations[METRICS_RELATION_NAME])


def reconcile(charm: ops.CharmBase, local_inventory: LocalLXDInventory) -> bool:
    """Reconcile the metrics listener, local trust, and scrape jobs for all relations."""
    charm._metrics_provider.update_scrape_job_spec(base_scrape_jobs())
    if not has_metrics_relation(charm):
        disable_metrics_listener()
        return False

    credentials_by_relation_id: dict[int, MetricsCredentials] = {}
    for relation in charm.model.relations[METRICS_RELATION_NAME]:
        credentials = relation_credentials(charm, relation)
        if credentials is None:
            continue
        credentials_by_relation_id[relation.id] = credentials
        if should_manage_trust_store(charm, local_inventory):
            lxd.ensure_metrics_certificate_trusted(
                trust_name=credentials.trust_name,
                certificate_pem=credentials.certificate_pem,
            )
        if charm.unit.is_leader():
            relation.data[charm.app]["scrape_jobs"] = json.dumps(scrape_jobs(credentials))

    enable_metrics_listener(desired_metrics_address(charm))
    return True


def publish_unit_metadata(
    charm: ops.CharmBase, relation: ops.Relation, local_inventory
) -> None:
    """Publish per-unit metadata on one metrics relation during a relation hook."""
    relation.data[charm.unit][METRICS_JOB_NAME_FIELD] = local_inventory.server_name


def cleanup_relation(
    charm: ops.CharmBase,
    relation: ops.Relation,
    local_inventory: LocalLXDInventory,
) -> None:
    """Remove the local trust entry that was created for one metrics relation."""
    if not should_manage_trust_store(charm, local_inventory):
        return
    trust_name = ""
    if charm.unit.is_leader():
        trust_name = relation.data[charm.app].get(TRUST_NAME_FIELD, "").strip()
    else:
        credentials = peer_relation_credentials(charm)
        if credentials is not None:
            trust_name = credentials.trust_name
    if trust_name:
        lxd.remove_trusted_certificate_by_name(trust_name)


def should_manage_trust_store(
    charm: ops.CharmBase,
    local_inventory: LocalLXDInventory,
) -> bool:
    """Return whether this unit should mutate the local or clustered trust store."""
    return not local_inventory.server_clustered or charm.unit.is_leader()


def base_scrape_jobs() -> list[dict]:
    """Return the provider job spec before relation-specific TLS injection."""
    return [
        {
            "job_name": "lxd",
            "metrics_path": METRICS_PATH,
            "scheme": "https",
            "scrape_interval": METRICS_SCRAPE_INTERVAL,
            "static_configs": [{"targets": [f"*:{METRICS_PORT}"]}],
        }
    ]


def scrape_jobs(credentials: MetricsCredentials) -> list[dict]:
    """Return the full scrape job including relation-specific client TLS material."""
    jobs = base_scrape_jobs()
    jobs[0]["tls_config"] = {
        "insecure_skip_verify": True,
        "cert_file": credentials.certificate_pem,
        "key_file": credentials.private_key_pem,
    }
    return jobs


def desired_metrics_address(charm: ops.CharmBase) -> str:
    """Return the unit-local LXD metrics bind address derived from Juju bindings."""
    bind_address = str(charm.model.get_binding(METRICS_RELATION_NAME).network.bind_address)
    return format_host_port(bind_address, METRICS_PORT)


def enable_metrics_listener(address: str) -> None:
    """Enable LXD metrics on the desired address if it is not already set."""
    if lxd.get_config(METRICS_CONFIG_KEY) == address:
        return
    lxd.set_config(METRICS_CONFIG_KEY, address)


def disable_metrics_listener() -> None:
    """Disable LXD metrics when no metrics relation exists."""
    if not lxd.get_config(METRICS_CONFIG_KEY):
        return
    lxd.unset_config(METRICS_CONFIG_KEY)


def relation_credentials(
    charm: ops.CharmBase,
    relation: ops.Relation,
) -> Optional[MetricsCredentials]:
    """Return or create the metrics credentials published on one relation."""
    if not charm.unit.is_leader():
        return peer_relation_credentials(charm)

    app_data = relation.data[charm.app]
    existing = credentials_from_mapping(app_data)
    if existing is not None:
        mirror_peer_credentials(charm, existing)
        return existing

    trust_name = f"juju-{charm.app.name}-metrics-{relation.id}"
    created = generate_client_certificate(f"{charm.app.name}-metrics-{relation.id}")
    app_data[CERTIFICATE_FIELD] = created.certificate_pem
    app_data[PRIVATE_KEY_FIELD] = created.private_key_pem
    app_data[TRUST_NAME_FIELD] = trust_name
    credentials = MetricsCredentials(created.certificate_pem, created.private_key_pem, trust_name)
    mirror_peer_credentials(charm, credentials)
    return credentials


def peer_relation_credentials(charm: ops.CharmBase) -> Optional[MetricsCredentials]:
    """Read the leader-published metrics credentials from peer app data for followers."""
    relation = charm.model.get_relation(cluster_state.PEER_RELATION_NAME)
    if relation is None:
        return None
    return credentials_from_mapping(relation.data[charm.app])


def mirror_peer_credentials(charm: ops.CharmBase, credentials: MetricsCredentials) -> None:
    """Mirror the metrics credentials into peer app data so follower units can trust them."""
    relation = charm.model.get_relation(cluster_state.PEER_RELATION_NAME)
    if relation is None:
        return
    app_data = relation.data[charm.app]
    app_data[CERTIFICATE_FIELD] = credentials.certificate_pem
    app_data[PRIVATE_KEY_FIELD] = credentials.private_key_pem
    app_data[TRUST_NAME_FIELD] = credentials.trust_name


def credentials_from_mapping(
    mapping: ops.RelationDataContent,
) -> Optional[MetricsCredentials]:
    """Parse metrics credentials from one relation databag-like mapping."""
    certificate_pem = mapping.get(CERTIFICATE_FIELD, "").strip()
    private_key_pem = mapping.get(PRIVATE_KEY_FIELD, "").strip()
    trust_name = mapping.get(TRUST_NAME_FIELD, "").strip()
    if not (certificate_pem and private_key_pem and trust_name):
        return None
    return MetricsCredentials(certificate_pem, private_key_pem, trust_name)


def generate_client_certificate(common_name: str) -> MetricsCredentials:
    """Generate a self-signed client certificate for LXD metrics authentication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = f"{tmpdir}/client.crt"
        key_path = f"{tmpdir}/client.key"
        try:
            subprocess.run(
                (
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    key_path,
                    "-out",
                    cert_path,
                    "-subj",
                    f"/CN={common_name}",
                    "-days",
                    "3650",
                ),
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise lxd.LXDValidationError("required command not found: openssl") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise lxd.LXDValidationError(
                f"failed to generate metrics certificate: {detail}"
            ) from exc

        with open(cert_path, encoding="utf-8") as cert_file:
            certificate_pem = cert_file.read()
        with open(key_path, encoding="utf-8") as key_file:
            private_key_pem = key_file.read()
    return MetricsCredentials(
        certificate_pem=certificate_pem,
        private_key_pem=private_key_pem,
        trust_name="",
    )


def format_host_port(host: str, port: int) -> str:
    """Render a host:port pair, adding IPv6 brackets when needed."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{port}"
    if address.version == 6:
        return f"[{host}]:{port}"
    return f"{host}:{port}"
