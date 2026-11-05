"""Real Home Assistant boundary tests, intentionally separate from HA stubs."""
import pytest


@pytest.fixture(autouse=True)
def allow_custom_components(enable_custom_integrations):
    """Allow the repository's custom component without loading real devices."""
    yield
