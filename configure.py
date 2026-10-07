"""Narrow installer configuration edits; preserve unrelated TOML text and settings."""
import argparse
import json
from pathlib import Path
import re
import tomllib

PLUGIN = 'gramps-desktop@family-tree-local'
HEADER = '[plugins."' + PLUGIN + '"]'


def set_enabled(text, header, enabled, plugin_id=PLUGIN):
    pattern = re.compile(r'(?m)^\[[ \t]*plugins[ \t]*\.[ \t]*["\']' + re.escape(plugin_id) + r'["\'][ \t]*\][ \t]*(?:#[^\r\n]*)?\r?$')
    found = pattern.search(text)
    value = 'enabled = ' + str(enabled).lower()
    if not found:
        return text.rstrip() + '\n\n' + header + '\n' + value + '\n'
    next_header = re.search(r'(?m)^\[', text[found.end():])
    end = found.end() + next_header.start() if next_header else len(text)
    block = text[found.end():end]
    if re.search(r'(?m)^\s*enabled\s*=', block):
        block = re.sub(r'(?m)^(\s*)enabled\s*=[^\r\n]*', lambda m: m[1] + value, block)
    else:
        block = '\n' + value + block
    return text[:found.end()] + block + text[end:]


def remove_standalone(text):
    sections = list(re.finditer(r'(?m)^\[([^\r\n]+?)\][ \t]*(?:#[^\r\n]*)?\r?$', text))
    for i in reversed(range(len(sections))):
        match = sections[i]
        name = re.sub(r'[ \t"\']', '', match[1])
        if name != 'mcp_servers.gramps_desktop' and not name.startswith('mcp_servers.gramps_desktop.'):
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        # Retain trailing comments belonging to the next section.
        lines = text[match.end():end].splitlines(keepends=True)
        tail = []
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith('#')):
            tail.insert(0, lines.pop())
        text = text[:match.start()] + ''.join(tail) + text[end:]
    return text


def configure_text(text, standalone, python, server, plugin_id=PLUGIN):
    text = remove_standalone(text)
    text = set_enabled(text, '[plugins.' + json.dumps(plugin_id) + ']', not standalone, plugin_id)
    if standalone:
        text = text.rstrip() + '\n\n[mcp_servers.gramps_desktop]\n' + '\n'.join([
            'command = ' + json.dumps(str(python).replace('\\', '/')),
            'args = [' + json.dumps(str(server).replace('\\', '/')) + ']',
            'enabled = true', 'startup_timeout_sec = 15', 'tool_timeout_sec = 60']) + '\n'
    parsed = tomllib.loads(text)
    assert parsed['plugins'][plugin_id]['enabled'] == (not standalone)
    assert ('gramps_desktop' in parsed.get('mcp_servers', {})) == standalone
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--addon', type=Path, required=True)
    parser.add_argument('--standalone', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--plugin-id', default=PLUGIN)
    args = parser.parse_args()
    config = args.project / '.codex/config.toml'
    text = config.read_text(encoding='utf-8') if config.is_file() else ''
    updated = configure_text(text, args.standalone, args.python, args.tools / 'server.py', args.plugin_id)
    if updated != text and not args.prepare_only:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(updated, encoding='utf-8')
    locator = {'source': str((args.tools / 'desktop_bridge.py').resolve()), 'version': '2.2.1'}
    (args.addon / 'bridge_source.json').write_text(json.dumps(locator, indent=2) + '\n', encoding='utf-8')
    transport = {'mcpServers': {'gramps_desktop': {'command': str(args.python.resolve()).replace('\\', '/'),
                 'args': ['server.py'], 'cwd': '.',
                 'startup_timeout_sec': 15, 'tool_timeout_sec': 60}}}
    (args.tools / '.mcp.json').write_text(json.dumps(transport, indent=2) + '\n', encoding='utf-8')
