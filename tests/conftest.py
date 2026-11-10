"""Make the add-on's modules importable without the heavy model libraries."""
from pathlib import Path
import sys
import types

ENGINE = Path(__file__).parents[1] / "addons" / "sentient_voice_engine"
sys.path.insert(0, str(ENGINE))
sys.modules.setdefault("sherpa_onnx", types.ModuleType("sherpa_onnx"))  # only the real engine needs it

try:  # Home Assistant 2026.11 ships probatio; on older cores only the import is needed for these tests
    import probatio  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock

    sys.modules["probatio"] = MagicMock()

pytest_plugins = "pytest_homeassistant_custom_component"


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
