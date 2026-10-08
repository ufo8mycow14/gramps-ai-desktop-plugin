"""Small installed loader; implementation remains in the plugin checkout."""

import importlib.util
from pathlib import Path
import json
from gramps.gen.plug import Gramplet

SETTINGS = json.loads(
    Path(__file__).with_name("bridge_source.json").read_text(encoding="utf-8")
)
SOURCE = Path(SETTINGS["source"])
if not SOURCE.is_absolute() or not SOURCE.is_file():
    raise RuntimeError(
        "Gramps AI Desktop Plugin bridge source missing; rerun its installer from the current tools directory"
    )
_spec = importlib.util.spec_from_file_location("gramps_desktop_bridge", SOURCE)
_bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bridge)


def load_on_reg(dbstate, uistate, plugin):
    _bridge.start(dbstate, uistate, runtime=SETTINGS.get("runtime"))
    return []


class DesktopControl(Gramplet):
    def init(self):
        instance = _bridge.start(
            self.dbstate, self.uistate, runtime=SETTINGS.get("runtime")
        )
        self.set_text(
            "Gramps AI Desktop Plugin connected locally. Session: "
            + instance.session[:12]
        )
