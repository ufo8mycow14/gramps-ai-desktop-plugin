"""Portable installer: no package installation, tree access or application restart."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from configure import configure_text
from platform_paths import addon_dir, SUPPORTED_GRAMPS


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    os.replace(temp, path)


def install(project, version='6.1', standalone=False, addon=None, dry_run=False, codex=None):
    source = Path(__file__).resolve().parent
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('The target workspace must exist')
    if version not in SUPPORTED_GRAMPS:
        raise ValueError('Supported installer targets are Gramps 6.0 and 6.1')
    target = (Path(addon) if addon else addon_dir(version)).resolve()
    marketplace = json.loads((source / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
    manifest = json.loads((source / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    identity = manifest['name'] + '@' + marketplace['name']
    config = project / '.codex/config.toml'
    original = config.read_text(encoding='utf-8') if config.exists() else ''
    updated = configure_text(original, standalone, Path(sys.executable), source / 'server.py', identity)
    for name in ('DesktopControl.py', 'DesktopControl.gpr.py'):
        existing = target / name
        if existing.exists() and not any(marker in existing.read_text(encoding='utf-8') for marker in
                                         ('bridge_source.json', 'Desktop MCP Control')):
            raise ValueError('Unrelated add-on preserved: ' + str(existing))
    for file in source.glob('*.py'):
        compile(file.read_text(encoding='utf-8'), file.name, 'exec')
    executable = codex or shutil.which('codex')
    if not standalone and not executable:
        raise ValueError('Codex CLI with plugin commands is required; otherwise use --standalone')
    result = {'addon': str(target), 'config': str(config), 'plugin': identity,
              'gramps_target': version, 'dry_run': dry_run, 'standalone': standalone}
    if dry_run:
        return result
    target.mkdir(parents=True, exist_ok=True)
    for name in ('DesktopControl.py', 'DesktopControl.gpr.py'):
        text = (source / name).read_text(encoding='utf-8')
        if name.endswith('.gpr.py'):
            text = text.replace("gramps_target_version='6.1'", "gramps_target_version=%r" % version)
        atomic_text(target / name, text)
    atomic_text(target / 'bridge_source.json', json.dumps({
        'source': str(source / 'desktop_bridge.py'), 'version': manifest['version']}, indent=2) + '\n')
    transport = {'mcpServers': {'gramps_desktop': {
        'command': str(Path(sys.executable).resolve()), 'args': ['server.py'], 'cwd': '.',
        'startup_timeout_sec': 15, 'tool_timeout_sec': 120}}}
    atomic_text(source / '.mcp.json', json.dumps(transport, indent=2) + '\n')
    if not standalone:
        subprocess.run([executable, 'plugin', 'marketplace', 'add', str(source), '--json'], check=True)
        subprocess.run([executable, 'plugin', 'add', identity, '--json'], check=True)
    if updated != original:
        atomic_text(config, updated)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--gramps-version', choices=SUPPORTED_GRAMPS, default='6.1')
    parser.add_argument('--addon-dir', type=Path, help='Override the native Gramps add-on directory')
    parser.add_argument('--standalone', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required')
    print(json.dumps(install(args.project, args.gramps_version, args.standalone,
                             args.addon_dir, args.dry_run)))


if __name__ == '__main__':
    main()
