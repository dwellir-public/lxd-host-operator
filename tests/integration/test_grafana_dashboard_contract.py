"""End-to-end validation for the LXD Grafana dashboard metrics/logs contract."""

from __future__ import annotations

import json
import re
import time

import pytest

from . import helpers


@pytest.mark.parametrize("app_name", ["lxd-host", "lxd-cluster"])
def test_dashboard_contract_scenario(testbed: helpers.Testbed, charm_path, app_name: str):
    """Verify Mimir job labels match Loki instance labels for Grafana dashboard 19131."""
    start_ns = time.time_ns()
    model_name = helpers.status_json(testbed.juju)["model"]["name"]
    expected_instances = {node.name for node in helpers.NODES}
    mimir_app = helpers.consume_offer(testbed.juju, "mimir-vm")
    loki_app = helpers.consume_offer(testbed.juju, "loki-loadbalancer-vm")

    testbed.juju.deploy("alloy-vm", channel="latest/edge", base="ubuntu@24.04")
    helpers.deploy_lxd_app(testbed, charm_path, app_name=app_name)

    testbed.juju.integrate(f"{app_name}:metrics-endpoint", "alloy-vm:metrics-endpoint")
    testbed.juju.integrate(f"{app_name}:logging", loki_app)
    testbed.juju.integrate("alloy-vm:send-remote-write", f"{mimir_app}:receive-remote-write")

    helpers.wait_for_units_active(testbed.juju, app_name, expected_units=3)
    helpers.wait_for_units_active(testbed.juju, "alloy-vm", expected_units=1)

    alloy_unit = helpers.show_unit_json(testbed.juju, "alloy-vm/0")
    remote_write_relation = helpers.relation_info_for_endpoint(alloy_unit, "send-remote-write")
    mimir_base_url = remote_write_relation["application-data"]["selected-url"].rstrip("/")

    lxd_unit = helpers.show_unit_json(testbed.juju, f"{app_name}/0")
    logging_relation = helpers.relation_info_for_endpoint(lxd_unit, "logging")
    loki_push_url = json.loads(
        logging_relation["related-units"]["loki-loadbalancer-vm/0"]["data"]["endpoint"]
    )["url"]
    loki_base_url = loki_push_url.removesuffix("/loki/api/v1/push").rstrip("/")

    deadline = time.time() + 300.0
    instance_matcher = "|".join(sorted(re.escape(name) for name in expected_instances))
    last_metric_jobs: set[str] = set()
    last_log_instances: set[str] = set()

    while time.time() < deadline:
        mimir_payload = helpers.http_json(
            f"{mimir_base_url}/prometheus/api/v1/query",
            params={
                "query": f'count by (job) (up{{juju_model="{model_name}",juju_application="{app_name}"}})'
            },
        )
        last_metric_jobs = {
            sample["metric"]["job"]
            for sample in mimir_payload["data"]["result"]
            if sample.get("metric", {}).get("job")
        }

        loki_payload = helpers.http_json(
            f"{loki_base_url}/loki/api/v1/query_range",
            params={
                "query": f'{{app="lxd",instance=~"{instance_matcher}"}}',
                "start": str(start_ns),
                "end": str(time.time_ns()),
                "limit": "200",
            },
        )
        last_log_instances = {
            stream["stream"]["instance"]
            for stream in loki_payload["data"]["result"]
            if stream.get("stream", {}).get("instance")
        }

        if last_metric_jobs == expected_instances and last_log_instances == expected_instances:
            return
        time.sleep(5.0)

    raise AssertionError(
        "dashboard contract did not converge: "
        f"metric jobs={sorted(last_metric_jobs)}, "
        f"log instances={sorted(last_log_instances)}, "
        f"expected={sorted(expected_instances)}"
    )
