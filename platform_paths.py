"""Shared paths without importing Gramps or changing process-wide settings."""
import os
from pathlib import Path
import sys

SUPPORTED_GRAMPS = ('6.0', '6.1')


def runtime_dir(env=None, platform=None, home=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if env.get('GRAMPS_DESKTOP_RUNTIME'):
        path = Path(env['GRAMPS_DESKTOP_RUNTIME'])
        if not path.is_absolute():
            raise ValueError('GRAMPS_DESKTOP_RUNTIME must be an absolute directory')
        return path
    if platform == 'win32':
        return Path(env.get('LOCALAPPDATA', home / 'AppData/Local')) / 'GrampsDesktopMCP'
    if platform == 'darwin':
        return home / 'Library/Application Support/GrampsDesktopMCP'
    return Path(env.get('XDG_STATE_HOME', home / '.local/state')) / 'GrampsDesktopMCP'


def addon_dir(version='6.1', env=None, platform=None, home=None):
    if version not in SUPPORTED_GRAMPS:
        raise ValueError('Supported installer targets are Gramps 6.0 and 6.1')
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if env.get('GRAMPSHOME'):
        base = Path(env['GRAMPSHOME']) / 'gramps'
    elif platform == 'win32':
        base = Path(env.get('APPDATA', home / 'AppData/Roaming')) / 'gramps'
    else:
        # Gramps uses the XDG data directory on both Linux and macOS.
        base = Path(env.get('XDG_DATA_HOME', home / '.local/share')) / 'gramps'
    return base / ('gramps' + version.replace('.', '')) / 'plugins/DesktopMCPControl'


def load_sibling(name, filename):
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location(name, Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
