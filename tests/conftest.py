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
