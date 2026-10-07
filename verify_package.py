"""Offline release checks; no Gramps launch, database access or user config edits."""
import json
import ast
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import configure
import server


def main():
    root = Path(__file__).resolve().parent
    checks = []
    for name in ('server.py', 'desktop_bridge.py', 'support.py', 'configure.py',
                 'DesktopControl.py', 'DesktopControl.gpr.py', 'package_release.py', 'verify_support.py',
                 'ui_support.py', 'verify_menu_support.py', 'batch_support.py', 'batch_files.py', 'analysis_support.py', 'tree_support.py',
                 'secondary_support.py', 'date_support.py', 'preference_support.py', 'navigation_support.py', 'workflow_support.py',
                 'native_filters.py', 'native_reports.py', 'report_output.py', 'report_media.py', 'native_exports.py', 'web_support.py', 'platform_paths.py',
                 'install.py', 'verify_batch.py', 'verify_integrations.py', 'verify_expansion.py'):
        compile((root / name).read_text(encoding='utf-8'), name, 'exec')
    checks.append('source_compiles')
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text())
    assert manifest['version'] == server.VERSION
    for name in ('desktop_bridge.py', 'support.py'):
        parsed = ast.parse((root / name).read_text(encoding='utf-8'))
        versions = [ast.literal_eval(node.value) for node in parsed.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)]
        assert versions == [server.VERSION], name
    addon = ast.parse((root / 'DesktopControl.gpr.py').read_text(encoding='utf-8'))
    versions = [ast.literal_eval(keyword.value) for node in ast.walk(addon) if isinstance(node, ast.Call)
                for keyword in node.keywords if keyword.arg == 'version']
    assert versions == [server.VERSION]
    checks.append('manifest_server_bridge_support_addon_versions_match')
    marketplace = json.loads((root / '.agents/plugins/marketplace.json').read_text())
    plugin_id = manifest['name'] + '@' + marketplace['name']
    assert marketplace['plugins'][0]['source']['path'] == './'
    assert (root / manifest['mcpServers']).is_file()
    assert (root / manifest['skills'] / 'gramps/SKILL.md').is_file()
    checks.append('standalone_marketplace_and_plugin_paths')
    unrelated = '\n# Preserve unrelated settings\n[mcp_servers.other]\ncommand = "keep"\n'
    for identity in (configure.PLUGIN, plugin_id):
        original = '[plugins.' + json.dumps(identity) + '] # enabled before\nenabled = false\n'
        original += '[mcp_servers."gramps_desktop"] # standalone before\ncommand = "old"\n'
        original += '[mcp_servers.gramps_desktop.env] # nested table\nTEST = "old"\n' + unrelated
        plugin = configure.configure_text(original, False, Path('python.exe'), Path('server.py'), identity)
        assert unrelated in plugin and '# enabled before' in plugin
        assert 'gramps_desktop' not in tomllib.loads(plugin).get('mcp_servers', {})
        assert tomllib.loads(plugin)['plugins'][identity]['enabled']
        assert configure.configure_text(plugin, False, Path('python.exe'), Path('server.py'), identity) == plugin
        standalone = configure.configure_text(plugin, True, Path('chosen-python.exe'), Path('chosen/server.py'), identity)
        parsed = tomllib.loads(standalone)
        assert parsed['mcp_servers']['gramps_desktop']['command'] == 'chosen-python.exe'
        assert not parsed['plugins'][identity]['enabled'] and unrelated in standalone
        restored = configure.configure_text(standalone, False, Path('python.exe'), Path('server.py'), identity)
        assert tomllib.loads(restored) == tomllib.loads(plugin) and unrelated in restored
    checks.append('commented_toml_headers_transitions_preservation_and_idempotence')
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': None},
        ['invalid request'],
        {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'},
    ]
    result = subprocess.run([sys.executable, str(root / 'server.py')],
                            input='\n'.join(json.dumps(r) for r in requests) + '\n',
                            text=True, encoding='utf-8', capture_output=True, timeout=15, check=True,
                            env=dict(os.environ, PYTHONIOENCODING='cp1252'))
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 5 and not result.stderr
    assert replies[0]['result']['serverInfo']['version'] == server.VERSION
    assert replies[1]['error']['code'] == -32602 and replies[1]['id'] == 2
    assert replies[2]['error']['code'] == -32600
    assert replies[3]['id'] == 3 and replies[3]['result'] == {}
    tools = replies[4]['result']['tools']
    assert len(tools) == 54 and len({t['name'] for t in tools}) == 54
    assert any('1–200' in tool['description'] for tool in tools)
    checks.append('utf8_stdio_under_windows_legacy_encoding')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    assert all('`' + tool['name'] + '`' in readme for tool in tools)
    assert 'GNU GENERAL PUBLIC LICENSE' in (root / 'LICENSE').read_text()
    checks.append('malformed_request_error_then_ping_and_54_documented_tools')
    print(json.dumps({'passed': len(checks), 'checks': checks, 'family_data_access': False}))


if __name__ == '__main__':
    main()
