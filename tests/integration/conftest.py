"""Pytest fixtures for Jubilant-backed LXD host integration tests."""

from __future__ import annotations

import pathlib
from collections.abc import Generator

import jubilant
import pytest

from . import helpers


@pytest.fixture()
def charm_path() -> pathlib.Path:
    """Resolve the local charm artifact used for Juju-backed integration runs."""
    return helpers.resolve_charm_path()


@pytest.fixture()
def testbed() -> Generator[helpers.Testbed]:
    """Create one temporary model with three restored manual machines attached."""
    helpers.prepare_nodes()
    try:
        with jubilant.temp_model(controller=helpers.CONTROLLER, cloud=helpers.CLOUD) as juju:
            juju.wait_timeout = 15 * 60
            machines = helpers.attach_manual_machines(juju)
            yield helpers.Testbed(juju=juju, machines=machines)
    finally:
        helpers.restore_nodes()
