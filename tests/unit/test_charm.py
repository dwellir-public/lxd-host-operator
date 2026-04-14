import json

from ops import testing
from ops.testing import PeerRelation, Relation

from charm import LxdHostCharm
from inventory import LocalLXDInventory
from lxd import LXDValidationError

META = {
    "name": "lxd-host",
    "provides": {"metrics-endpoint": {"interface": "prometheus_scrape"}},
    "requires": {
        "logging": {"interface": "loki_push_api"},
        "syslog": {"interface": "syslog"},
    },
    "peers": {"cluster": {"interface": "lxd_host_peers"}},
}


def _context() -> testing.Context:
    return testing.Context(LxdHostCharm, meta=META)


def test_install_blocks_when_lxd_is_missing(monkeypatch):
    """Install should block clearly when the LXD snap or API is unavailable."""
    ctx = _context()

    def _raise():
        raise LXDValidationError("should not be used")

    monkeypatch.setattr("charm.inventory.collect_local_inventory", _raise)

    state_out = ctx.run(ctx.on.install(), testing.State())

    assert state_out.unit_status == testing.BlockedStatus("LXD unavailable: should not be used")


def test_start_reports_active_status_with_snap_version(monkeypatch):
    """Start should render the local snap version and standalone role."""
    ctx = _context()

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="5.21.3",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(ctx.on.start(), testing.State())

    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_leader_elected_sets_workload_version(monkeypatch):
    """Leader should set workload version from the local LXD server version."""
    ctx = _context()
    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=True,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(ctx.on.leader_elected(), testing.State(leader=True))

    assert state_out.workload_version == "6.1"
    assert state_out.unit_status == testing.ActiveStatus(
        "snap 5.21.3 rev 33110, cluster member lxd-node1"
    )


def test_metrics_relation_enables_listener_and_publishes_tls_scrape_job(monkeypatch):
    """A metrics relation should enable LXD metrics and publish TLS scrape data."""
    ctx = _context()
    relation = Relation("metrics-endpoint", interface="prometheus_scrape", remote_app_name="alloy")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "")
    monkeypatch.setattr("metrics.lxd.set_config", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr("metrics.lxd.ensure_metrics_certificate_trusted", lambda **_: None)
    monkeypatch.setattr(
        "metrics.generate_client_certificate",
        lambda common_name: __import__("metrics").MetricsCredentials(
            certificate_pem="-----BEGIN CERTIFICATE-----\nclient\n-----END CERTIFICATE-----\n",
            private_key_pem="-----BEGIN PRIVATE KEY-----\nclient\n-----END PRIVATE KEY-----\n",
            trust_name="",
        ),
    )
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(
        ctx.on.relation_joined(relation),
        testing.State(leader=True, relations=[relation]),
    )

    relation_out = next(rel for rel in state_out.relations if rel.endpoint == "metrics-endpoint")
    scrape_jobs = json.loads(relation_out.local_app_data["scrape_jobs"])

    assert calls == [("core.metrics_address", "192.0.2.0:8444")]
    assert scrape_jobs[0]["metrics_path"] == "/1.0/metrics"
    assert scrape_jobs[0]["scrape_interval"] == "15s"
    assert scrape_jobs[0]["scheme"] == "https"
    assert scrape_jobs[0]["tls_config"]["insecure_skip_verify"] is True
    assert "BEGIN CERTIFICATE" in scrape_jobs[0]["tls_config"]["cert_file"]
    assert "BEGIN PRIVATE KEY" in scrape_jobs[0]["tls_config"]["key_file"]
    assert relation_out.local_app_data["metrics_trust_name"] == "juju-lxd-host-metrics-1"
    assert relation_out.local_unit_data["metrics_job_name"] == "lxd-node1"


def test_update_status_without_metrics_relation_disables_listener(monkeypatch):
    """Without a metrics relation the charm should disable `core.metrics_address`."""
    ctx = _context()
    calls: list[str] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="127.0.0.1:8444",
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "127.0.0.1:8444")
    monkeypatch.setattr("metrics.lxd.unset_config", lambda key: calls.append(key))
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(ctx.on.update_status(), testing.State())

    assert calls == ["core.metrics_address"]
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_non_leader_metrics_relation_uses_peer_credentials(monkeypatch):
    """Follower units should trust leader-published metrics credentials from peer app data."""
    ctx = _context()
    peer = PeerRelation(
        endpoint="cluster",
        interface="lxd_host_peers",
        local_app_data={
            "metrics_client_cert": (
                "-----BEGIN CERTIFICATE-----\npeer\n-----END CERTIFICATE-----\n"
            ),
            "metrics_client_key": (
                "-----BEGIN PRIVATE KEY-----\npeer\n-----END PRIVATE KEY-----\n"
            ),
            "metrics_trust_name": "juju-lxd-host-metrics-1",
        },
    )
    relation = Relation("metrics-endpoint", interface="prometheus_scrape", remote_app_name="alloy")
    trusted: list[tuple[str, str]] = []
    configured: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node2",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "")
    monkeypatch.setattr(
        "metrics.lxd.set_config",
        lambda key, value: configured.append((key, value)),
    )
    monkeypatch.setattr(
        "metrics.lxd.ensure_metrics_certificate_trusted",
        lambda **kwargs: trusted.append((kwargs["trust_name"], kwargs["certificate_pem"])),
    )
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(
        ctx.on.relation_created(relation),
        testing.State(relations=[peer, relation]),
    )

    assert trusted == [
        (
            "juju-lxd-host-metrics-1",
            "-----BEGIN CERTIFICATE-----\npeer\n-----END CERTIFICATE-----",
        )
    ]
    assert configured == [("core.metrics_address", "192.0.2.0:8444")]
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_non_leader_metrics_relation_broken_uses_peer_trust_name(monkeypatch):
    """Follower units should remove the mirrored trust entry during relation teardown."""
    ctx = _context()
    peer = PeerRelation(
        endpoint="cluster",
        interface="lxd_host_peers",
        local_app_data={
            "metrics_client_cert": (
                "-----BEGIN CERTIFICATE-----\npeer\n-----END CERTIFICATE-----\n"
            ),
            "metrics_client_key": (
                "-----BEGIN PRIVATE KEY-----\npeer\n-----END PRIVATE KEY-----\n"
            ),
            "metrics_trust_name": "juju-lxd-host-metrics-1",
        },
    )
    relation = Relation("metrics-endpoint", interface="prometheus_scrape", remote_app_name="alloy")
    removed: list[str] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node2",
            server_clustered=False,
            metrics_address="127.0.0.1:8444",
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "127.0.0.1:8444")
    monkeypatch.setattr("metrics.lxd.unset_config", lambda key: None)
    monkeypatch.setattr(
        "metrics.lxd.remove_trusted_certificate_by_name",
        lambda trust_name: removed.append(trust_name),
    )
    monkeypatch.setattr("charm.logging_config.active_loki_endpoint", lambda charm: None)
    monkeypatch.setattr("charm.logging_config.active_syslog_target", lambda charm: None)

    state_out = ctx.run(
        ctx.on.relation_broken(relation),
        testing.State(relations=[peer, relation]),
    )

    assert removed == ["juju-lxd-host-metrics-1"]
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_logging_relation_configures_lxd_for_direct_loki(monkeypatch):
    """A logging relation should set native LXD Loki config and clear syslog mode."""
    ctx = _context()
    relation = Relation("logging", interface="loki_push_api", remote_app_name="loki")
    writes: list[tuple[str, str]] = []
    unsets: list[str] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr(
        "charm.logging_config.active_loki_endpoint",
        lambda charm: "http://loki.example:3100",
    )
    disabled_syslog: list[str] = []
    existing_values = {
        "loki.api.url": "",
        "loki.types": "",
        "loki.auth.username": "",
        "loki.auth.password": "",
    }
    monkeypatch.setattr("logging_config.lxd.get_config", lambda key: existing_values.get(key, ""))
    monkeypatch.setattr(
        "logging_config.lxd.set_config",
        lambda key, value: writes.append((key, value)),
    )
    monkeypatch.setattr("logging_config.lxd.unset_config", lambda key: unsets.append(key))
    monkeypatch.setattr("logging_config.lxd.get_snap_option", lambda key: "true")
    monkeypatch.setattr(
        "logging_config.lxd.set_snap_option",
        lambda key, value: disabled_syslog.append(value),
    )
    monkeypatch.setattr("logging_config.syslog_forwarder.disable_forwarding", lambda: None)

    state_out = ctx.run(
        ctx.on.relation_joined(relation),
        testing.State(relations=[relation]),
    )

    assert ("loki.api.url", "http://loki.example:3100") in writes
    assert ("loki.types", "logging,lifecycle") in writes
    assert disabled_syslog == ["false"]
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_active_loki_endpoint_picks_first_sorted_url():
    """Multiple endpoints should collapse to one deterministic selected base URL."""

    class _FakeConsumer:
        loki_endpoints = [
            {"url": "http://zulu.example/loki/api/v1/push"},
            {"url": "http://alpha.example/loki/api/v1/push"},
        ]

    class _FakeCharm:
        _loki_consumer = _FakeConsumer()

    endpoint = __import__("logging_config").active_loki_endpoint(_FakeCharm())

    assert endpoint == "http://alpha.example"


def test_update_status_without_logging_relation_clears_loki_keys(monkeypatch):
    """Without a logging relation the charm should clear direct Loki config keys."""
    ctx = _context()
    unsets: list[str] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.active_loki_endpoint", lambda charm: None)
    monkeypatch.setattr("charm.logging_config.active_syslog_target", lambda charm: None)
    existing_values = {
        "loki.api.url": "http://old-loki:3100/loki/api/v1/push",
        "loki.auth.username": "",
        "loki.auth.password": "",
        "loki.types": "logging,lifecycle",
    }
    monkeypatch.setattr("logging_config.lxd.get_config", lambda key: existing_values.get(key, ""))
    monkeypatch.setattr("logging_config.lxd.unset_config", lambda key: unsets.append(key))
    monkeypatch.setattr("logging_config.lxd.get_snap_option", lambda key: "")
    monkeypatch.setattr("logging_config.syslog_forwarder.disable_forwarding", lambda: None)

    state_out = ctx.run(ctx.on.update_status(), testing.State())

    assert unsets == ["loki.api.url", "loki.types"]
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_syslog_relation_enables_daemon_syslog_and_forwarder(monkeypatch):
    """A ready syslog relation should enable snap syslog mode and host forwarding."""
    ctx = _context()
    relation = Relation(
        "syslog",
        interface="syslog",
        remote_app_name="alloy",
        remote_app_data={
            "address": "10.0.0.10",
            "port": "1514",
            "protocols": "tcp,udp",
            "recommended-protocol": "tcp",
            "ready": "true",
            "reason": "ready",
        },
    )
    forwarded = []
    snap_changes: list[str] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.active_loki_endpoint", lambda charm: None)
    monkeypatch.setattr("logging_config.lxd.get_config", lambda key: "")
    monkeypatch.setattr("logging_config.lxd.get_snap_option", lambda key: "")
    monkeypatch.setattr(
        "logging_config.lxd.set_snap_option",
        lambda key, value: snap_changes.append(value),
    )
    monkeypatch.setattr(
        "logging_config.syslog_forwarder.ensure_forwarding",
        lambda target, topology: forwarded.append((target, topology)),
    )

    state_out = ctx.run(
        ctx.on.relation_joined(relation),
        testing.State(relations=[relation]),
    )

    assert snap_changes == ["true"]
    assert forwarded[0][0].address == "10.0.0.10"
    assert forwarded[0][0].port == "1514"
    assert forwarded[0][0].protocol == "tcp"
    assert forwarded[0][1].application == "lxd-host"
    assert forwarded[0][1].unit == "lxd-host/0"
    assert forwarded[0][1].lxd_host == "lxd-node1"
    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_active_syslog_target_uses_ready_relation_data():
    """Only ready syslog relations with address data should produce a forwarding target."""

    class _RelationData(dict):
        pass

    class _Relation:
        app = object()

        def __init__(self):
            self.data = {
                self.app: _RelationData(
                    {
                        "address": "10.0.0.10",
                        "port": "1514",
                        "protocols": "tcp,udp",
                        "recommended-protocol": "tcp",
                        "ready": "true",
                    }
                )
            }

    class _Model:
        relations = {"syslog": [_Relation()]}

    class _Charm:
        model = _Model()

    target = __import__("logging_config").active_syslog_target(_Charm())

    assert target is not None
    assert target.address == "10.0.0.10"
    assert target.port == "1514"
    assert target.protocol == "tcp"


def test_syslog_forwarder_render_config_contains_topology_fields():
    """Rendered rsyslog config should stamp source identity into syslog fields Alloy labels."""
    module = __import__("syslog_forwarder")

    rendered = module.render_config(
        module.SyslogTarget(address="10.0.0.10", port="1514", protocol="tcp"),
        module.SyslogTopology(
            model="lxd-host-charming",
            model_uuid="uuid-1",
            application="lxd-host",
            unit="lxd-host/0",
            charm="lxd-host",
            lxd_host="lxd-node1",
        ),
    )

    assert 'target="10.0.0.10"' in rendered
    assert 'protocol="tcp"' in rendered
    assert "lxd-node1 lxd-host lxd-host/0" in rendered
    assert 'application=\\"lxd-host\\"' in rendered


def test_lxd_set_config_retries_transient_validation_errors(monkeypatch):
    """LXD config writes should retry short-lived daemon validation failures."""
    calls: list[tuple[str, ...]] = []
    attempts = iter(
        [
            LXDValidationError("temporary failure"),
            LXDValidationError("temporary failure"),
            "ok",
        ]
    )

    def _run_command(*args):
        calls.append(args)
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("lxd.run_command", _run_command)
    monkeypatch.setattr("lxd.time.sleep", lambda _: None)

    __import__("lxd").set_config("loki.api.url", "http://loki.example/loki/api/v1/push")

    assert calls == [
        ("lxc", "config", "set", "loki.api.url", "http://loki.example/loki/api/v1/push"),
        ("lxc", "config", "set", "loki.api.url", "http://loki.example/loki/api/v1/push"),
        ("lxc", "config", "set", "loki.api.url", "http://loki.example/loki/api/v1/push"),
    ]


def test_logging_relation_does_not_reconcile_metrics(monkeypatch):
    """Logging-only relation hooks should not re-run metrics trust reconciliation."""
    ctx = _context()
    relation = Relation("logging", interface="loki_push_api", remote_app_name="loki")

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="127.0.0.1:8444",
        ),
    )
    monkeypatch.setattr(
        "charm.metrics.reconcile",
        lambda charm, inventory: (_ for _ in ()).throw(
            AssertionError("metrics should not run")
        ),
    )
    monkeypatch.setattr("charm.metrics.has_metrics_relation", lambda charm: True)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: True)
    monkeypatch.setattr(
        "charm.logging_config.active_loki_endpoint",
        lambda charm: "http://loki.example:3100/loki/api/v1/push",
    )

    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    assert state_out.unit_status == testing.ActiveStatus("snap 5.21.3 rev 33110, standalone")


def test_transient_loki_error_sets_waiting_status(monkeypatch):
    """Transient direct-Loki readiness failures should not fail the hook."""
    ctx = _context()
    relation = Relation("logging", interface="loki_push_api", remote_app_name="loki")

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr(
        "charm.logging_config.reconcile",
        lambda charm, inventory: (_ for _ in ()).throw(
            __import__("logging_config").TransientLokiError(
                "Loki is not ready, server returned HTTP status 404 Not Found"
            )
        ),
    )

    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    assert state_out.unit_status == testing.WaitingStatus(
        "Waiting for Loki readiness: Loki is not ready, server returned HTTP status 404 Not Found"
    )


def test_ensure_metrics_certificate_trusted_ignores_missing_remove(monkeypatch):
    """Cluster races removing the same trust entry should not fail reconciliation."""
    module = __import__("lxd")
    monkeypatch.setattr(
        "lxd.list_trust_entries",
        lambda: [
            module.TrustEntry(
                name="juju-lxd-host-metrics-1",
                fingerprint="abc",
                certificate="old-cert",
                type="metrics",
            )
        ],
    )

    calls: list[tuple[str, ...]] = []

    def _run_command(*args):
        calls.append(args)
        if args[:5] == ("lxc", "config", "trust", "remove", "abc"):
            raise LXDValidationError("Error: Certificate not found")
        return ""

    monkeypatch.setattr("lxd.run_command", _run_command)

    module.ensure_metrics_certificate_trusted(
        trust_name="juju-lxd-host-metrics-1",
        certificate_pem="new-cert",
    )

    assert ("lxc", "config", "trust", "remove", "abc") in calls


def test_ensure_metrics_certificate_trusted_ignores_raced_add(monkeypatch):
    """A concurrent add should be accepted once the desired cert is visible."""
    module = __import__("lxd")
    entries = [
        module.TrustEntry(
            name="juju-lxd-host-metrics-1",
            fingerprint="abc",
            certificate="old-cert",
            type="metrics",
        )
    ]

    def _list_trust_entries():
        return list(entries)

    def _retry_command(*args, **kwargs):
        if args[:5] == ("lxc", "config", "trust", "remove", "abc"):
            entries.clear()
            return ""
        if args[:4] == ("lxc", "config", "trust", "add"):
            entries.append(
                module.TrustEntry(
                    name="juju-lxd-host-metrics-1",
                    fingerprint="new",
                    certificate="new-cert",
                    type="metrics",
                )
            )
            raise LXDValidationError("Error: Certificate already in trust store")
        return ""

    monkeypatch.setattr("lxd.list_trust_entries", _list_trust_entries)
    monkeypatch.setattr("lxd.retry_command", _retry_command)

    module.ensure_metrics_certificate_trusted(
        trust_name="juju-lxd-host-metrics-1",
        certificate_pem="new-cert",
    )

    assert entries == [
        module.TrustEntry(
            name="juju-lxd-host-metrics-1",
            fingerprint="new",
            certificate="new-cert",
            type="metrics",
        )
    ]


def test_cluster_follower_skips_trust_reconciliation(monkeypatch):
    """Cluster followers should not mutate the shared LXD trust store."""
    ctx = _context()
    peer = PeerRelation(
        endpoint="cluster",
        interface="lxd_host_peers",
        local_app_data={
            "metrics_client_cert": (
                "-----BEGIN CERTIFICATE-----\npeer\n-----END CERTIFICATE-----\n"
            ),
            "metrics_client_key": (
                "-----BEGIN PRIVATE KEY-----\npeer\n-----END PRIVATE KEY-----\n"
            ),
            "metrics_trust_name": "juju-lxd-host-metrics-1",
        },
        peers_data={
            1: {
                "snap_version": "5.21.3",
                "snap_revision": "33110",
                "server_version": "6.1",
                "server_name": "lxd-node1",
                "server_clustered": "true",
                "cluster_members": '["lxd-node1", "lxd-node2", "lxd-node3"]',
                "metrics_enabled": "true",
                "log_sink": "none",
            },
            2: {
                "snap_version": "5.21.3",
                "snap_revision": "33110",
                "server_version": "6.1",
                "server_name": "lxd-node3",
                "server_clustered": "true",
                "cluster_members": '["lxd-node1", "lxd-node2", "lxd-node3"]',
                "metrics_enabled": "true",
                "log_sink": "none",
            },
        },
    )
    relation = Relation("metrics-endpoint", interface="prometheus_scrape", remote_app_name="alloy")
    configured: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node2",
            server_clustered=True,
            cluster_members=("lxd-node1", "lxd-node2", "lxd-node3"),
            metrics_address="",
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "")
    monkeypatch.setattr(
        "metrics.lxd.set_config",
        lambda key, value: configured.append((key, value)),
    )
    monkeypatch.setattr(
        "metrics.lxd.ensure_metrics_certificate_trusted",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected trust mutation")),
    )
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(
        ctx.on.update_status(),
        testing.State(relations=[peer, relation], leader=False, planned_units=3),
    )

    assert configured == [("core.metrics_address", "192.0.2.0:8444")]
    assert state_out.unit_status == testing.ActiveStatus(
        "snap 5.21.3 rev 33110, cluster member lxd-node2"
    )


def test_leader_reports_healthy_cluster_summary(monkeypatch):
    """A consistent 3-node cluster should render a healthy aggregate summary."""
    ctx = _context()
    peer = PeerRelation(
        endpoint="cluster",
        interface="lxd_host_peers",
        peers_data={
            1: {
                "snap_version": "5.21.3",
                "snap_revision": "33110",
                "server_version": "6.1",
                "server_name": "lxd-node2",
                "server_clustered": "true",
                "cluster_members": '["lxd-node1", "lxd-node2", "lxd-node3"]',
                "metrics_enabled": "false",
                "log_sink": "none",
            },
            2: {
                "snap_version": "5.21.3",
                "snap_revision": "33110",
                "server_version": "6.1",
                "server_name": "lxd-node3",
                "server_clustered": "true",
                "cluster_members": '["lxd-node1", "lxd-node2", "lxd-node3"]',
                "metrics_enabled": "false",
                "log_sink": "none",
            },
        },
    )

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=True,
            cluster_members=("lxd-node1", "lxd-node2", "lxd-node3"),
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(
        ctx.on.update_status(),
        testing.State(leader=True, relations=[peer], planned_units=3),
    )

    assert state_out.unit_status == testing.ActiveStatus(
        "snap 5.21.3 rev 33110, cluster member lxd-node1; cluster healthy 3 units/3 members"
    )


def test_leader_blocks_on_mixed_cluster_state(monkeypatch):
    """A mixed standalone/clustered peer set should block with a clear mismatch status."""
    ctx = _context()
    peer = PeerRelation(
        endpoint="cluster",
        interface="lxd_host_peers",
        peers_data={
            1: {
                "snap_version": "5.21.3",
                "snap_revision": "33110",
                "server_version": "6.1",
                "server_name": "lxd-node2",
                "server_clustered": "true",
                "cluster_members": '["lxd-node1", "lxd-node2"]',
                "metrics_enabled": "false",
                "log_sink": "none",
            }
        },
    )

    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="33110",
            server_version="6.1",
            server_name="lxd-node1",
            server_clustered=False,
            metrics_address="",
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda charm, inventory: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda charm, inventory: False)

    state_out = ctx.run(
        ctx.on.update_status(),
        testing.State(leader=True, relations=[peer], planned_units=2),
    )

    assert state_out.unit_status == testing.BlockedStatus(
        "Cluster mismatch: mixed standalone and clustered units"
    )
