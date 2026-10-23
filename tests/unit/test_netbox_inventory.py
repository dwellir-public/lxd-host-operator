"""Verify the principal's unit-scoped subordinate integration contract."""

import json
from unittest.mock import Mock

import pytest
from ops import testing
from test_charm import META

from charm import LxdHostCharm
from inventory import LocalLXDInventory
from lxd import LXDValidationError


@pytest.mark.parametrize("clustered", [False, True])
@pytest.mark.parametrize("leader", [False, True])
def test_publishes_local_identity_without_reconciling_observability(
    monkeypatch, clustered, leader
):
    ctx = testing.Context(LxdHostCharm, meta=META)
    relation = testing.SubordinateRelation("netbox-inventory", interface="lxd_inventory")
    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="1",
            server_version="5.21.3",
            server_name="host-a",
            server_clustered=clustered,
            cluster_members=("host-a", "host-b") if clustered else (),
        ),
    )
    forbidden = Mock(side_effect=AssertionError("NetBox events must not reconcile observability"))
    monkeypatch.setattr("charm.metrics.reconcile", forbidden)
    monkeypatch.setattr("charm.logging_config.reconcile", forbidden)
    result = ctx.run(
        ctx.on.relation_joined(relation), testing.State(relations={relation}, leader=leader)
    )
    data = result.get_relation(relation.id).local_unit_data
    assert data == {
        "version": "1",
        "ready": "true",
        "hostname": "host-a",
        "clustered": json.dumps(clustered),
        "members": json.dumps(["host-a", "host-b"] if clustered else []),
        "socket": "/var/snap/lxd/common/lxd/unix.socket",
    }
    assert not result.get_relation(relation.id).local_app_data
    forbidden.assert_not_called()


def test_unavailable_lxd_revokes_stale_readiness(monkeypatch):
    ctx = testing.Context(LxdHostCharm, meta=META)
    relation = testing.SubordinateRelation(
        "netbox-inventory",
        interface="lxd_inventory",
        local_unit_data={"ready": "true", "hostname": "old"},
    )

    def unavailable():
        raise LXDValidationError("unavailable")

    monkeypatch.setattr("charm.inventory.collect_local_inventory", unavailable)
    result = ctx.run(ctx.on.relation_changed(relation), testing.State(relations={relation}))
    assert result.get_relation(relation.id).local_unit_data == {"version": "1", "ready": "false"}


@pytest.mark.parametrize("event_name", ["update_status", "metrics_joined", "metrics_broken"])
def test_validation_failure_revokes_readiness_through_other_handlers(monkeypatch, event_name):
    ctx = testing.Context(LxdHostCharm, meta=META)
    relation = testing.SubordinateRelation(
        "netbox-inventory",
        interface="lxd_inventory",
        local_unit_data={"ready": "true", "hostname": "stale", "members": '["stale"]'},
    )
    metrics = testing.Relation("metrics-endpoint", interface="prometheus_scrape")

    def unavailable():
        raise LXDValidationError("unavailable")

    monkeypatch.setattr("charm.inventory.collect_local_inventory", unavailable)
    event = {
        "update_status": ctx.on.update_status(),
        "metrics_joined": ctx.on.relation_joined(metrics),
        "metrics_broken": ctx.on.relation_broken(metrics),
    }[event_name]
    result = ctx.run(event, testing.State(relations={relation, metrics}))
    assert result.get_relation(relation.id).local_unit_data == {"version": "1", "ready": "false"}


def test_regular_reconciliation_updates_member_facts(monkeypatch):
    ctx = testing.Context(LxdHostCharm, meta=META)
    relation = testing.SubordinateRelation("netbox-inventory", interface="lxd_inventory")
    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory(
            snap_version="5.21.3",
            snap_revision="1",
            server_version="5.21.3",
            server_name="host-a",
            server_clustered=True,
            cluster_members=("host-a", "host-c"),
        ),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda *_args: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda *_args: False)
    result = ctx.run(ctx.on.update_status(), testing.State(relations={relation}))
    assert json.loads(result.get_relation(relation.id).local_unit_data["members"]) == [
        "host-a",
        "host-c",
    ]


def https_context(monkeypatch):
    monkeypatch.setattr(
        "charm.inventory.collect_local_inventory",
        lambda: LocalLXDInventory("5.21.8", "1", "5.21.8", "host-a", False),
    )
    monkeypatch.setattr("charm.metrics.reconcile", lambda *_args: False)
    monkeypatch.setattr("charm.logging_config.reconcile", lambda *_args: False)
    monkeypatch.setattr(
        "netbox_inventory.netbox_access.certificate_fingerprint", lambda _cert: "fingerprint"
    )
    return testing.Context(LxdHostCharm, meta=META)


def test_https_extension_preserves_v1_and_registers_only_public_certificate(monkeypatch):
    ctx = https_context(monkeypatch)
    relation = testing.SubordinateRelation(
        "netbox-inventory",
        interface="lxd_inventory",
        remote_unit_data={
            "versions": "2",
            "client-certificate": "public certificate",
        },
    )
    register = Mock(
        return_value={
            "https-url": "https://host-a:8443",
            "server-certificate": "public server",
            "client-fingerprint": "fingerprint",
        }
    )
    monkeypatch.setattr("netbox_inventory.netbox_access.reconcile", register)
    state = ctx.run(ctx.on.relation_changed(relation), testing.State(relations={relation}))
    data = state.get_relation(relation.id).local_unit_data
    assert data["version"] == "1" and data["ready"] == "true"
    assert data["socket"] == "/var/snap/lxd/common/lxd/unix.socket"
    assert data["https-version"] == "2" and data["https-ready"] == "true"
    assert register.call_args.args[-1] == "public certificate"
    assert not state.get_relation(relation.id).local_app_data
    saved = next(s for s in state.stored_states if s.name == "_netbox_state").content
    assert len(saved["registrations"]) == 1


def test_https_setup_failure_does_not_break_legacy_consumer_contract(monkeypatch):
    from netbox_access import AccessError

    ctx = https_context(monkeypatch)
    relation = testing.SubordinateRelation(
        "netbox-inventory",
        interface="lxd_inventory",
        remote_unit_data={
            "versions": "2",
            "client-certificate": "public certificate",
        },
    )
    monkeypatch.setattr(
        "netbox_inventory.netbox_access.reconcile", Mock(side_effect=AccessError("Enable HTTPS"))
    )
    state = ctx.run(ctx.on.relation_changed(relation), testing.State(relations={relation}))
    data = state.get_relation(relation.id).local_unit_data
    assert data["ready"] == "true" and data["version"] == "1"
    assert data["https-ready"] == "false" and data["https-error"] == "Enable HTTPS"


def test_broken_relation_revokes_saved_owned_identity_and_retries_api_failure(monkeypatch):
    from dataclasses import replace

    from netbox_access import AccessError

    ctx = https_context(monkeypatch)
    relation = testing.SubordinateRelation(
        "netbox-inventory",
        interface="lxd_inventory",
        remote_unit_data={
            "versions": "2",
            "client-certificate": "public certificate",
        },
    )
    monkeypatch.setattr("netbox_inventory.netbox_access.reconcile", Mock(return_value={}))
    state = ctx.run(ctx.on.relation_changed(relation), testing.State(relations={relation}))
    revoke = Mock(side_effect=AccessError("temporarily unavailable"))
    monkeypatch.setattr("netbox_inventory.netbox_access.revoke", revoke)
    state = ctx.run(ctx.on.relation_broken(state.get_relation(relation.id)), state)
    saved = next(s for s in state.stored_states if s.name == "_netbox_state").content
    assert len(saved["registrations"]) == 1
    revoke.side_effect = None
    state = ctx.run(ctx.on.update_status(), replace(state, relations=frozenset()))
    saved = next(s for s in state.stored_states if s.name == "_netbox_state").content
    assert not saved["registrations"]
    assert revoke.call_count == 2
