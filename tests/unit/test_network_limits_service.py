"""Verify opt-in collector installation and integration lifecycle."""

import json
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from ops import testing
from test_charm import META

import metrics
import network_limits_service as service
from charm import LxdHostCharm
from inventory import LocalLXDInventory

CONFIG = {"options": {"network-limits-enabled": {"type": "boolean", "default": False}}}


@pytest.fixture()
def context(monkeypatch):
    ctx = testing.Context(LxdHostCharm, meta=META, config=CONFIG)
    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.8",
            snap_revision="1",
            server_version="5.21.8",
            server_name="host-a",
            server_clustered=False,
        ),
    )
    monkeypatch.setattr("metrics.lxd.get_config", lambda key: "192.0.2.0:8444")
    monkeypatch.setattr("metrics.lxd.set_config", Mock())
    monkeypatch.setattr("metrics.lxd.unset_config", Mock())
    monkeypatch.setattr("metrics.lxd.ensure_metrics_certificate_trusted", Mock())
    monkeypatch.setattr("metrics.lxd.remove_trusted_certificate_by_name", Mock())
    monkeypatch.setattr(
        "metrics.generate_client_certificate",
        lambda name: metrics.MetricsCredentials(
            certificate_pem="public-client-cert",
            private_key_pem="private-client-key",
            trust_name=name,
        ),
    )
    monkeypatch.setattr("charm.logging_config.reconcile", lambda *args: False)
    monkeypatch.setattr(service, "ensure", Mock())
    monkeypatch.setattr(service, "disable", Mock())
    return ctx


def test_default_keeps_native_job_and_does_not_start_collector(context):
    relation = testing.Relation("metrics-endpoint", interface="prometheus_scrape")
    state = context.run(
        context.on.upgrade_charm(), testing.State(leader=True, relations={relation})
    )
    jobs = json.loads(state.get_relation(relation.id).local_app_data["scrape_jobs"])
    assert len(jobs) == 1 and "relabel_configs" not in jobs[0]
    cast(Mock, service.ensure).assert_not_called()


def test_opt_in_publishes_two_mtls_jobs_with_common_host_identity(context):
    relation = testing.Relation("metrics-endpoint", interface="prometheus_scrape")
    state = context.run(
        context.on.config_changed(),
        testing.State(leader=True, config={"network-limits-enabled": True}, relations={relation}),
    )
    jobs = json.loads(state.get_relation(relation.id).local_app_data["scrape_jobs"])
    assert len(jobs) == 2
    assert jobs[0]["static_configs"][0]["targets"] == ["*:8444"]
    assert jobs[1]["static_configs"][0]["targets"] == ["*:9445"]
    assert jobs[0]["relabel_configs"] == jobs[1]["relabel_configs"]
    assert all(job["tls_config"]["cert_file"] == "public-client-cert" for job in jobs)
    assert all(job["tls_config"]["key_file"] == "private-client-key" for job in jobs)
    cast(Mock, service.ensure).assert_called_once_with("192.0.2.0", ["public-client-cert"])
    cast(Mock, service.disable).assert_not_called()


def test_enabled_without_metrics_consumer_keeps_collector_disabled(context):
    context.run(
        context.on.config_changed(), testing.State(config={"network-limits-enabled": True})
    )
    cast(Mock, service.ensure).assert_not_called()
    cast(Mock, service.disable).assert_called_once()


def test_turning_option_off_removes_companion_job(context):
    relation = testing.Relation("metrics-endpoint", interface="prometheus_scrape")
    state = context.run(
        context.on.config_changed(),
        testing.State(leader=True, config={"network-limits-enabled": False}, relations={relation}),
    )
    assert len(json.loads(state.get_relation(relation.id).local_app_data["scrape_jobs"])) == 1
    cast(Mock, service.ensure).assert_not_called()
    cast(Mock, service.disable).assert_called_once()


def test_service_failure_is_actionable_blocked_status(context):
    relation = testing.Relation("metrics-endpoint", interface="prometheus_scrape")
    cast(Mock, service.ensure).side_effect = service.ServiceError("collector service failed")
    state = context.run(
        context.on.config_changed(),
        testing.State(leader=True, config={"network-limits-enabled": True}, relations={relation}),
    )
    assert state.unit_status == testing.BlockedStatus("host-a: collector service failed")


def test_remove_only_cleans_collector(context, monkeypatch):
    cleanup = Mock()
    monkeypatch.setattr(service, "remove", cleanup)
    context.run(context.on.remove(), testing.State())
    cleanup.assert_called_once()
    cast(Mock, service.ensure).assert_not_called()


@pytest.mark.parametrize(
    "target,expected",
    [
        ("192.0.2.1:8444", "192.0.2.1"),
        ("192.0.2.1:9445", "192.0.2.1"),
        ("[2001:db8::1]:9445", "[2001:db8::1]"),
        ("lxd.example:8444", "lxd.example"),
    ],
)
def test_host_relabel_does_not_join_on_guest_name_or_service_port(target, expected):
    import re

    rule = metrics.host_relabel_configs()[0]
    assert re.fullmatch(rule["regex"], target)[1] == expected


def test_lifecycle_idempotence_and_certificate_rotation(tmp_path, monkeypatch):
    directory, unit = tmp_path / "collector", tmp_path / "collector.service"
    monkeypatch.setattr(service, "DIRECTORY", directory)
    monkeypatch.setattr(service, "UNIT_PATH", unit)
    commands = []

    def run(*args):
        commands.append(args)
        if args[0] == "openssl":
            Path(args[args.index("-out") + 1]).write_text("server-cert")
            Path(args[args.index("-keyout") + 1]).write_text("server-key")

    monkeypatch.setattr(service, "run", run)
    service.ensure("127.0.0.1", ["client-cert"])
    assert (directory / "clients.pem").read_text() == "client-cert"
    assert (directory / "server.key").stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    assert "ProtectSystem=strict" in unit.read_text()
    assert "--client-ca" in unit.read_text()
    commands.clear()
    service.ensure("127.0.0.1", ["client-cert"])
    assert commands == [
        ("systemctl", "enable", "--now", service.SERVICE),
        ("systemctl", "is-active", "--quiet", service.SERVICE),
    ]
    service.ensure("127.0.0.1", ["rotated-cert"])
    assert (directory / "clients.pem").read_text() == "rotated-cert"
    assert commands[-2] == ("systemctl", "restart", service.SERVICE)
    service.remove()
    assert not unit.exists() and not list(directory.iterdir())
    assert not any("lxd.daemon" in str(args) for args in commands)


def test_last_integration_removed_stops_collector_even_if_lxd_is_unavailable(context, monkeypatch):
    from lxd import LXDValidationError

    def unavailable():
        raise LXDValidationError("daemon unavailable")

    monkeypatch.setattr("charm.inventory.collect_local_inventory", unavailable)
    relation = testing.Relation("metrics-endpoint", interface="prometheus_scrape")
    state = context.run(
        context.on.relation_broken(relation),
        testing.State(leader=True, config={"network-limits-enabled": True}, relations={relation}),
    )
    cast(Mock, service.disable).assert_called_once()
    assert isinstance(state.unit_status, testing.BlockedStatus)
