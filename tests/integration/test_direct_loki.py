"""End-to-end direct Loki integration coverage."""

from __future__ import annotations

from . import helpers


def test_direct_loki_scenario(testbed: helpers.Testbed, charm_path):
    """Relate three manual LXD hosts directly to Loki and verify native LXD config."""
    loki_app = helpers.consume_offer(testbed.juju, "loki-loadbalancer-vm")

    helpers.deploy_lxd_host(testbed, charm_path)
    testbed.juju.integrate("lxd-host:logging", loki_app)

    payload = helpers.wait_for_units_active(testbed.juju, "lxd-host", expected_units=3)

    for unit_name in ("lxd-host/0", "lxd-host/1", "lxd-host/2"):
        unit = helpers.show_unit_json(testbed.juju, unit_name)
        unit_status = payload["applications"]["lxd-host"]["units"][unit_name]["workload-status"]
        if unit_name == "lxd-host/0":
            assert unit["workload-version"] == "5.21.4"
        message = unit_status["message"]
        assert "standalone" in message
        assert "standalone set 3 units" in message

        machine = helpers.unit_machine(payload, unit_name)
        loki_url = testbed.juju.ssh(machine, "sudo lxc config get loki.api.url").strip()
        loki_types = testbed.juju.ssh(machine, "sudo lxc config get loki.types").strip()
        syslog_mode = testbed.juju.ssh(machine, "sudo snap get lxd daemon.syslog || true").strip()

        assert loki_url.startswith("http://")
        assert "/loki/api/v1/push" not in loki_url
        assert loki_types == "logging,lifecycle"
        assert syslog_mode in {"", "false"}
