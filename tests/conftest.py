import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    if "ship_reel" in sys.modules:
        return sys.modules["ship_reel"]
    spec = importlib.util.spec_from_file_location("ship_reel", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ship_reel"] = mod
    spec.loader.exec_module(mod)
    return mod


_load()


import pytest  # noqa: E402

# Unit tests must not depend on real DNS: fake resolver for test hostnames.
FAKE_DNS = {
    "internal.test": ["10.0.0.5"],
    "metadata.test": ["169.254.169.254"],
    "mixed.test": ["93.184.216.34", "192.168.1.10"],
    "mapped.test": ["::ffff:127.0.0.1"],
    "cgnat.test": ["100.64.1.2"],
}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch, request):
    if "real_dns" in request.keywords:
        return
    from ship_reel.engine import netguard

    def fake(host, port):
        return FAKE_DNS.get(host, ["93.184.216.34"])

    monkeypatch.setattr(netguard, "_getaddrinfo", fake)
    monkeypatch.delenv(netguard.ENV_ALLOW, raising=False)
    netguard.set_setting_reader(None)
