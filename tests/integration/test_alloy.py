"""End-to-end Alloy metrics and logs integration coverage."""

from __future__ import annotations

from . import helpers


def test_alloy_scenario(testbed: helpers.Testbed, charm_path):
    """Relate three manual LXD hosts to Alloy and verify metrics plus syslog plumbing."""
    mimir_app = helpers.consume_offer(testbed.juju, "mimir-vm")
    loki_app = helpers.consume_offer(testbed.juju, "loki-loadbalancer-vm")

    testbed.juju.deploy(
        "alloy-vm",
        channel="latest/edge",
        base="ubuntu@24.04",
        config={"enable-syslogreceivers": True},
    )
    helpers.deploy_lxd_host(testbed, charm_path)

    testbed.juju.integrate("lxd-host:metrics-endpoint", "alloy-vm:metrics-endpoint")
    testbed.juju.integrate("lxd-host:syslog", "alloy-vm:syslog-receiver")
    testbed.juju.integrate("alloy-vm:send-remote-write", f"{mimir_app}:receive-remote-write")
    testbed.juju.integrate("alloy-vm:send-loki-logs", f"{loki_app}:loki_push_api")

    payload = helpers.wait_for_units_active(testbed.juju, "lxd-host", expected_units=3)
    helpers.wait_for_units_active(testbed.juju, "alloy-vm", expected_units=1)

    lxd_machine = helpers.unit_machine(payload, "lxd-host/0")
    metrics_address = testbed.juju.ssh(
        lxd_machine, "sudo lxc config get core.metrics_address"
    ).strip()
    forwarder = helpers.wait_for_machine_command_output(
        testbed.juju,
        lxd_machine,
        "sudo cat /etc/rsyslog.d/90-lxd-host-forward.conf",
    )
    trust_listing = helpers.wait_for_machine_command_output(
        testbed.juju,
        lxd_machine,
        "sudo lxc config trust list --format=json",
    )

    assert metrics_address.endswith(":8444")
    assert 'target="' in forwarder
    assert 'protocol="tcp"' in forwarder
    assert '"type":"metrics"' in trust_listing

    alloy_machine = helpers.unit_machine(helpers.status_json(testbed.juju), "alloy-vm/0")
    alloy_config = helpers.wait_for_machine_command_output(
        testbed.juju,
        alloy_machine,
        "sudo cat /etc/alloy/config.alloy",
    )

    assert "/1.0/metrics" in alloy_config
    assert "lxd-host" in alloy_config
