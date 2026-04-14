#!/usr/bin/env python3

"""Charm entrypoint for the LXD host operator."""

from __future__ import annotations

import logging

import ops
from charms.loki_k8s.v1.loki_push_api import LokiPushApiConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider

import cluster_state
import inventory
import logging_config
import lxd
import metrics
import status

logger = logging.getLogger(__name__)


class LxdHostCharm(ops.CharmBase):
    """Observe lifecycle events and report local LXD validity and identity."""

    def __init__(self, framework: ops.Framework):
        """Wire the phase-1 lifecycle handlers."""
        super().__init__(framework)
        self._metrics_provider = MetricsEndpointProvider(
            self,
            relation_name=metrics.METRICS_RELATION_NAME,
            jobs=metrics.base_scrape_jobs(),
            refresh_event=[self.on.start, self.on.update_status],
            forward_alert_rules=False,
        )
        self._loki_consumer = LokiPushApiConsumer(
            self,
            relation_name=logging_config.LOGGING_RELATION_NAME,
            forward_alert_rules=False,
        )
        framework.observe(self.on.install, self._on_reconcile)
        framework.observe(self.on.start, self._on_reconcile)
        framework.observe(self.on.update_status, self._on_reconcile)
        framework.observe(self.on.leader_elected, self._on_reconcile)
        for peer_event in (
            self.on[cluster_state.PEER_RELATION_NAME].relation_created,
            self.on[cluster_state.PEER_RELATION_NAME].relation_joined,
            self.on[cluster_state.PEER_RELATION_NAME].relation_changed,
            self.on[cluster_state.PEER_RELATION_NAME].relation_departed,
        ):
            framework.observe(peer_event, self._on_relation_event)
        framework.observe(
            self.on[metrics.METRICS_RELATION_NAME].relation_created,
            self._on_relation_event,
        )
        framework.observe(
            self.on[metrics.METRICS_RELATION_NAME].relation_joined,
            self._on_relation_event,
        )
        framework.observe(
            self.on[metrics.METRICS_RELATION_NAME].relation_changed,
            self._on_relation_event,
        )
        framework.observe(
            self.on[metrics.METRICS_RELATION_NAME].relation_broken,
            self._on_relation_broken,
        )
        for relation_name in (
            logging_config.LOGGING_RELATION_NAME,
            logging_config.SYSLOG_RELATION_NAME,
        ):
            framework.observe(self.on[relation_name].relation_created, self._on_relation_event)
            framework.observe(self.on[relation_name].relation_joined, self._on_relation_event)
            framework.observe(self.on[relation_name].relation_changed, self._on_relation_event)
            framework.observe(self.on[relation_name].relation_broken, self._on_relation_broken)

    def _on_reconcile(
        self,
        _: ops.EventBase,
        *,
        reconcile_metrics: bool = True,
        reconcile_logging: bool = True,
    ) -> None:
        """Validate the local LXD host and publish phase-1 status/version facts."""
        try:
            local_inventory = inventory.collect_local_inventory()
        except lxd.LXDValidationError as exc:
            self.unit.status = ops.BlockedStatus(f"LXD unavailable: {exc}")
            return

        metrics_enabled = (
            metrics.reconcile(self, local_inventory)
            if reconcile_metrics
            else metrics.has_metrics_relation(self)
        )
        try:
            logging_enabled = (
                logging_config.reconcile(self, local_inventory)
                if reconcile_logging
                else bool(
                    logging_config.active_loki_endpoint(self)
                    or logging_config.active_syslog_target(self)
                )
            )
        except logging_config.TransientLokiError as exc:
            self.unit.status = ops.WaitingStatus(f"Waiting for Loki readiness: {exc}")
            return
        log_sink = "none"
        if logging_config.active_loki_endpoint(self):
            log_sink = "loki"
        elif logging_enabled:
            log_sink = "syslog"
        cluster_state.publish_local_state(
            self,
            local_inventory,
            metrics_enabled=metrics_enabled,
            log_sink=log_sink,
        )
        cluster_assessment = cluster_state.reconcile(self, local_inventory)
        if self.unit.is_leader():
            self.unit.set_workload_version(local_inventory.server_version)
        if not cluster_assessment.healthy:
            self.unit.status = ops.BlockedStatus(cluster_assessment.message)
            return
        self.unit.status = status.render_unit_status(local_inventory, cluster_assessment.summary)

    def _on_relation_event(self, event: ops.RelationEvent) -> None:
        """Reconcile when relation changes may affect metrics publication."""
        if event.relation.name not in {
            cluster_state.PEER_RELATION_NAME,
            metrics.METRICS_RELATION_NAME,
            logging_config.LOGGING_RELATION_NAME,
            logging_config.SYSLOG_RELATION_NAME,
        }:
            return
        if event.relation.name == metrics.METRICS_RELATION_NAME:
            try:
                local_inventory = inventory.collect_local_inventory()
            except lxd.LXDValidationError as exc:
                self.unit.status = ops.BlockedStatus(f"LXD unavailable: {exc}")
                return
            metrics.publish_unit_metadata(self, event.relation, local_inventory)
        self._on_reconcile(
            event,
            reconcile_metrics=event.relation.name
            in {cluster_state.PEER_RELATION_NAME, metrics.METRICS_RELATION_NAME},
            reconcile_logging=event.relation.name
            in {
                cluster_state.PEER_RELATION_NAME,
                logging_config.LOGGING_RELATION_NAME,
                logging_config.SYSLOG_RELATION_NAME,
            },
        )

    def _on_relation_broken(self, event: ops.RelationBrokenEvent) -> None:
        """Cleanup trust for a broken metrics relation and reconcile remaining state."""
        if event.relation.name == metrics.METRICS_RELATION_NAME:
            try:
                local_inventory = inventory.collect_local_inventory()
            except lxd.LXDValidationError as exc:
                self.unit.status = ops.BlockedStatus(f"LXD unavailable: {exc}")
                return
            metrics.cleanup_relation(self, event.relation, local_inventory)
            self._on_reconcile(event)
            return
        if event.relation.name == cluster_state.PEER_RELATION_NAME:
            self._on_reconcile(event)
            return
        if event.relation.name not in {
            logging_config.LOGGING_RELATION_NAME,
            logging_config.SYSLOG_RELATION_NAME,
        }:
            return
        self._on_reconcile(event, reconcile_metrics=False)


if __name__ == "__main__":
    ops.main(LxdHostCharm)
