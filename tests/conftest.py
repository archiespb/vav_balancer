"""Shared pytest fixtures for the VAV Balancer test suite.

pytest-homeassistant-custom-component resolves hass.config.config_dir to
its own bundled testing_config/ directory by default, so a real custom
component living outside that package is invisible to the loader unless
it is reachable from there. We bridge that with a symlink created once per
session, combined with the library's own `enable_custom_integrations`
fixture (which clears the cached component list so the symlinked folder
is actually picked up).
"""
from __future__ import annotations

import os

import pytest
from pytest_homeassistant_custom_component.common import get_test_config_dir

pytest_plugins = "pytest_homeassistant_custom_component"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOMAIN = "vav_balancer"


@pytest.fixture(scope="session", autouse=True)
def _link_custom_component() -> None:
    """Make our custom_components/vav_balancer visible to the test hass."""
    target_dir = get_test_config_dir("custom_components")
    os.makedirs(target_dir, exist_ok=True)
    link_path = os.path.join(target_dir, _DOMAIN)
    source_path = os.path.join(_REPO_ROOT, "custom_components", _DOMAIN)
    if not os.path.islink(link_path) and not os.path.exists(link_path):
        os.symlink(source_path, link_path)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Every test in this suite needs our custom component loadable."""
    yield
