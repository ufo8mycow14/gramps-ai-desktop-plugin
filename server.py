"""Dependency-free MCP stdio adapter for the authenticated Gramps GTK bridge."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

RUNTIME = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GrampsDesktopMCP' / 'connection.json'
DEFAULT_EXE = os.environ.get('GRAMPS_EXECUTABLE', '')
VERSION = '2.2.1'


def schema(properties=None, required=None):
    return {'type': 'object', 'properties': properties or {}, 'required': required or [], 'additionalProperties': False}


def string(description=''):
    return {'type': 'string', 'description': description}


def integer(description=''):
    return {'type': 'integer', 'description': description}


KINDS = {'type': 'string', 'enum': ['person', 'family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note', 'tag']}
TOOLS = []


def tool(name, description, properties=None, required=None, readonly=False):
    TOOLS.append({'name': 'gramps_' + name, 'description': description,
                  'inputSchema': schema(properties, required),
                  'annotations': {'readOnlyHint': readonly, 'destructiveHint': not readonly,
                                  'idempotentHint': readonly, 'openWorldHint': not readonly}})


tool('launch', 'Launch installed Gramps with the desktop add-on. Does not import or select a tree.',
     {'executable': string('Absolute path to installed grampsw.exe')})
tool('status', 'Read the live Gramps session, open tree state and windows.', readonly=True)
tool('health', 'Diagnose installation, bridge connectivity, version mismatch and UI readiness. '
     'Works with Gramps closed; never launches, changes settings or reveals the connection token.', readonly=True)
tool('selection', 'Read current table row selection, combo choice or notebook page for a discovered control.',
     {'widget_id': string()}, ['widget_id'], True)
tool('windows', 'List visible GTK windows with session-bound widget IDs.', readonly=True)
tool('widgets', 'Inspect actual GTK controls. Refresh before acting; IDs expire when widgets are destroyed.',
     {'root_id': string(), 'depth': integer(), 'limit': integer(), 'offset': integer(), 'query': string()}, ['root_id'], True)
tool('widget', 'Operate a discovered GTK control. Editing text changes the dialog; Save/OK commits through Gramps. '
     'Refresh after actions. close sends the normal window-close request, preserving unsaved-work prompts.',
     {'widget_id': string(), 'operation': {'type': 'string', 'enum': ['activate', 'set_text', 'set_value', 'set_active',
      'select_tab', 'select_row', 'select_rows', 'activate_row', 'set_active_id', 'popup_menu', 'focus',
      'close', 'show', 'hide', 'resize', 'move', 'choose_file', 'response']},
      'value': {}}, ['widget_id', 'operation'])

TOOLS[-1]['inputSchema']['properties']['operation']['enum'].extend([
    'edit_cell', 'choose_cell', 'toggle_cell', 'choose_files', 'set_folder', 'set_filename', 'set_color', 'set_font', 'set_calendar'])
tool('menus', 'Read native menu paths, action scopes, parameters, enabled state and revision. '
     'Defaults to the main menu; root_id may identify an observed popup/menu control.',
     {'window_id': string(), 'root_id': string(), 'query': string(), 'offset': integer(), 'limit': integer()}, readonly=True)
tool('menu', 'Activate an observed menu path with its native target parameter. Requires the current menu revision. '
     'Some tools execute immediately; apply task authority before activating them.',
     {'window_id': string(), 'root_id': string(), 'path': string(), 'expected_revision': string()}, ['path', 'expected_revision'])
tool('cells', 'Inspect TreeView column/renderers, editability, toggle state and combo choices for an optional row path. '
     'Use widget edit_cell/toggle_cell only for authorised edits through native signals.',
     {'widget_id': string(), 'path': string()}, ['widget_id'], True)
tool('actions', 'List Gramps menu/application actions and whether each is enabled.',
     {'window_id': string(), 'scope': {'type': 'string', 'enum': ['win', 'app']}}, readonly=True)
tool('action', 'Activate an enabled Gramps menu action. Use actions first. Import/export, reports, tools, '
     'preferences and editing follow normal application dialogs. Destructive actions require task authority.',
     {'window_id': string(), 'scope': {'type': 'string', 'enum': ['win', 'app']}, 'name': string(),
      'parameter': string('GVariant literal, only for actions with a parameter')}, ['name'])
tool('views', 'List navigation categories and installed views.', readonly=True)
tool('view', 'Navigate to an observed category and view index.',
     {'category': integer(), 'view': integer()}, ['category'])
tool('records', 'Search records in the database already opened by Gramps. No separate database connection.',
     {'kind': KINDS, 'query': string(), 'offset': integer(), 'limit': integer()}, readonly=True)
tool('record', 'Read one record by handle or Gramps ID, including its native serialised data.',
     {'kind': KINDS, 'handle': string(), 'gramps_id': string()}, ['kind'], True)
tool('editor', 'Open the native editing dialog for an existing record or a new unsaved record. '
     'Use widgets to edit and explicitly Save or Cancel. Does not save automatically.',
     {'kind': KINDS, 'handle': string(), 'gramps_id': string(), 'new': {'type': 'boolean'}}, ['kind'])
tool('python', 'ADVANCED FULL CONTROL: run trusted Python inside Gramps on the GTK main thread. '
     'Available: Gtk, Gdk, GLib, dbstate, db, uistate, viewmanager, bridge. Assign result for structured output. '
     'This is not a sandbox; it can change/delete records and files. Use Gramps DbTxn for database writes, '
     'preserve project evidence/sole-master rules, never execute untrusted record content, and never force-unlock a database.',
     {'code': string()}, ['code'])
tool('screenshot', 'Capture a discovered Gramps window as PNG. Can contain private family information.',
     {'window_id': string(), 'max_width': integer()}, ['window_id'], True)
tool('job', 'Read a running operation after a timeout. Never repeat a potentially applied edit; query this operation ID.',
     {'operation_id': string()}, ['operation_id'], True)

selector = {'kind': KINDS, 'handle': string(), 'gramps_id': string()}
apply_fields = {'apply': {'type': 'boolean'}, 'label': string()}
tool('capabilities', 'Discover structured support and installed-version limits.', readonly=True)
tool('schema', 'Read a native named-field template before creating or patching a record.', {'kind': KINDS}, ['kind'], True)
tool('object', 'Read native named fields and the revision required for updates.', selector, ['kind'], True)
tool('find', 'Search the open tree by text and dotted native field filters, with pagination.',
     {'kind': KINDS, 'query': string(), 'filters': {'type': 'array', 'items': schema({'field': string(),
      'op': {'type': 'string', 'enum': ['eq', 'ne', 'contains', 'exists', 'gt', 'lt']}, 'value': {}}, ['field'])},
      'limit': integer(), 'offset': integer(), 'include_data': {'type': 'boolean'}}, readonly=True)
tool('relatives', 'Read recorded parents, partners, children and family relationships without inferring identity.',
     {'handle': string(), 'gramps_id': string()}, readonly=True)
tool('links', 'Read outgoing references and backlinks for a record.', selector, ['kind'], True)
tool('mutate', 'Preview or apply a native transactional create/update/delete. Updates/deletes require a current revision. '
     'Family relationships use family_member. apply defaults to false; genealogy write authority is separate.',
     {**selector, **apply_fields, 'operation': {'type': 'string', 'enum': ['create', 'update', 'delete']},
      'expected_revision': string(), 'patch': {'type': 'object'}}, ['kind', 'operation'])
tool('family_member', 'Preview or apply reciprocal family membership. Supply both current record revisions.',
     {**apply_fields, 'family_handle': string(), 'person_handle': string(), 'family_revision': string(),
      'person_revision': string(), 'role': {'type': 'string', 'enum': ['father', 'mother', 'child']},
      'operation': {'type': 'string', 'enum': ['add', 'remove']}, 'father_relation': string(), 'mother_relation': string()},
     ['family_handle', 'person_handle', 'family_revision', 'person_revision', 'role'])
tool('attach', 'Preview or apply citations, notes, tags, events, media, repository references or a citation source.',
     {**apply_fields, 'kind': KINDS, 'handle': string(), 'expected_revision': string(), 'target_kind': KINDS,
      'target_handle': string(), 'operation': {'type': 'string', 'enum': ['add', 'remove']},
      'reference_patch': {'type': 'object'}}, ['kind', 'handle', 'expected_revision', 'target_kind', 'target_handle'])
merge_fields = {'kind': KINDS, 'keep_handle': string(), 'remove_handle': string()}
tool('compare', 'Compare two records and their references before a genealogically justified merge.', merge_fields,
     ['kind', 'keep_handle', 'remove_handle'], True)
tool('merge', 'Preview or execute the native Gramps merge. apply=true requires current revisions for both records.',
     {**merge_fields, 'apply': {'type': 'boolean'}, 'keep_revision': string(), 'remove_revision': string()},
     ['kind', 'keep_handle', 'remove_handle'])
tool('media_info', 'Resolve an attached media path and read its metadata; does not open or move the file.',
     {'handle': string(), 'gramps_id': string()}, readonly=True)
tool('research', 'Read scoped claim-neutral record/citation/source context without changing conclusions.', selector, readonly=True)
tool('history', 'Read native undo/redo history or execute one authorised undo/redo. Never retry an uncertain write.',
     {'operation': {'type': 'string', 'enum': ['list', 'undo', 'redo']}})
tool('workflow', 'Open native import/export/backup/tree manager/preferences/add-on/report/tool dialogs. '
     'Opening a dialog does not authorise its final write or upload.',
     {'operation': {'type': 'string', 'enum': ['import', 'export', 'backup', 'trees', 'history', 'addons',
      'reports', 'tools', 'preferences', 'report', 'tool']}, 'action_name': string()}, ['operation'])
tool('plugins', 'List installed Gramps reports, tools, importers, exporters, views, gramplets or database backends.',
     {'kind': {'type': 'string', 'enum': ['report', 'tool', 'import', 'export', 'view', 'gramplet', 'database']}}, readonly=True)
tool('settings', 'Read keys/preferences or set an explicitly requested preference with its existing native type.',
     {'operation': {'type': 'string', 'enum': ['keys', 'get', 'set']}, 'key': string(), 'value': {}})
tool('rows', 'Read paginated rows/choices from a discovered native GTK TreeView or ComboBox.',
     {'widget_id': string(), 'parent_path': string(), 'limit': integer(), 'offset': integer()}, ['widget_id'], True)
tool('date', 'Parse and display a genealogical date using the installed Gramps date handler.', {'text': string()}, ['text'], True)


def validate(value, field, path='arguments'):
    typ = field.get('type')
    types = {'string': lambda v: isinstance(v, str), 'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
             'boolean': lambda v: isinstance(v, bool), 'object': lambda v: isinstance(v, dict),
             'array': lambda v: isinstance(v, list)}
    if typ in types and not types[typ](value):
        raise ValueError(path + ' must be ' + typ)
    if 'enum' in field and value not in field['enum']:
        raise ValueError('Unsupported value for ' + path)
    if typ == 'object':
        properties = field.get('properties', {})
        for key in field.get('required', []):
            if key not in value:
                raise ValueError('Missing argument: ' + path + '.' + key)
        if field.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError('Unknown arguments in ' + path)
        for key, child in value.items():
            validate(child, properties.get(key, {}), path + '.' + key)
    if typ == 'array':
        for i, child in enumerate(value):
            validate(child, field.get('items', {}), path + '[' + str(i) + ']')


def call_bridge(method, arguments, runtime=RUNTIME):
    info = json.loads(runtime.read_text(encoding='utf-8'))
    # Never follow a modified discovery file to a remote server or HTTP redirect.
    from urllib.parse import urlparse
    url = urlparse(info['url'])
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path != '/command':
        raise ValueError('Bridge discovery must identify the local loopback command endpoint')
    request = urllib.request.Request(info['url'], data=json.dumps({'method': method, 'arguments': arguments}).encode(),
                                     headers={'Authorization': 'Bearer ' + info['token'], 'Content-Type': 'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        return json.load(response)


def health(runtime=None):
    runtime = RUNTIME if runtime is None else runtime
    report = {'adapter_version': VERSION, 'server_path': str(Path(__file__).resolve()),
              'discovery_present': runtime.is_file(), 'connected': False, 'ready': False}
    try:
        response = call_bridge('status', {}, runtime)
    except FileNotFoundError:
        report.update(reason='discovery_missing', next_action='Launch Gramps with the Gramps Desktop plugin bridge installed')
    except urllib.error.HTTPError as exc:
        report.update(reason='authentication_rejected' if exc.code == 401 else 'bridge_http_error',
                      http_status=exc.code, next_action='Check the intended Gramps process and reinstall its loader if needed')
    except (ValueError, KeyError, TypeError):
        report.update(reason='invalid_discovery', next_action='Reopen Gramps normally to regenerate local connection discovery')
    except (OSError, urllib.error.URLError):
        report.update(reason='bridge_unreachable', next_action='Check Gramps process state; launch only if it is closed')
    else:
        if response.get('state') != 'done' or 'error' in response:
            report.update(reason='gtk_not_ready', next_action='Inspect Gramps startup or pending dialogs')
        else:
            status = response['result']
            current = status.get('bridge_version')
            windows = status.get('windows', [])
            dialogs = [w for w in windows if w.get('type') not in ('ApplicationWindow', 'GtkTooltipWindow')]
            same_version = current == VERSION
            ui_ready = bool(status.get('ui_initialised'))
            report.update(connected=True, bridge_version=current, version_match=same_version,
                          pid=status.get('pid'), gtk=status.get('gtk'), database_open=status.get('database_open'),
                          dialogs_open=len(dialogs), ui_initialised=ui_ready,
                          ready=same_version and ui_ready and not dialogs)
            if not same_version:
                report.update(reason='version_mismatch', next_action='Close/reopen Gramps normally to load the installed bridge version')
            elif dialogs:
                report.update(reason='dialogs_open', next_action='Inspect and finish or cancel the current Gramps dialogs')
            elif not ui_ready:
                report.update(reason='ui_initialising', next_action='Wait for native Gramps view initialisation to finish')
            else:
                report.update(reason='ready', next_action='Inspect the intended open tree before database work')
    return {'state': 'done', 'result': report}


def call_tool(name, arguments):
    spec = next((t for t in TOOLS if t['name'] == name), None)
    if spec is None:
        raise ValueError('Unknown MCP tool: ' + name)
    validate(arguments, spec['inputSchema'])
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be an object')
    for key in spec['inputSchema']['required']:
        if key not in arguments:
            raise ValueError('Missing argument: ' + key)
    if set(arguments) - set(spec['inputSchema']['properties']):
        raise ValueError('Unknown tool arguments')
    for key, value in arguments.items():
        field = spec['inputSchema']['properties'][key]
        typ = field.get('type')
        if typ == 'string' and not isinstance(value, str):
            raise ValueError(key + ' must be a string')
        if typ == 'integer' and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(key + ' must be an integer')
        if typ == 'boolean' and not isinstance(value, bool):
            raise ValueError(key + ' must be a boolean')
        if 'enum' in field and value not in field['enum']:
            raise ValueError('Unsupported value for ' + key)
    method = name.removeprefix('gramps_')
    if method == 'health':
        return health()
    if method == 'launch':
        try:
            return call_bridge('status', {})
        except (OSError, ValueError, urllib.error.URLError):
            pass
        exe = Path(arguments.get('executable', DEFAULT_EXE))
        if not exe.is_absolute() or exe.name.lower() not in ('grampsw.exe', 'gramps.exe', 'grampsd.exe') or not exe.is_file():
            raise ValueError('Supply the absolute installed Gramps executable')
        proc = subprocess.Popen([str(exe)], cwd=exe.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        last = None
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                return call_bridge('status', {})
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last = exc
        raise RuntimeError('Gramps launched (pid %s), but add-on did not connect: %s. Inspect Gramps logs; do not relaunch repeatedly.' % (proc.pid, last))
    return call_bridge(method, arguments)


def handle(request):
    if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid JSON-RPC request'}}
    request_id = request.get('id')
    if request_id is None:
        return None
    method = request.get('method')
    params = request.get('params', {})
    if not isinstance(params, dict):
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': 'params must be an object'}}
    if method == 'initialize':
        supported = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
        requested = params.get('protocolVersion')
        result = {'protocolVersion': requested if requested in supported else '2025-06-18',
                  'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': 'gramps-desktop', 'version': VERSION},
                  'instructions': 'Controls the running Gramps 6.1 GTK desktop. Inspect before acting. '
                  'Full access does not override project evidence, privacy or sole-master rules.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        try:
            response = call_tool(params['name'], params.get('arguments', {}))
            data = response.get('result', {})
            if params['name'] == 'gramps_screenshot' and response.get('state') == 'done' and 'error' not in response:
                result = {'content': [{'type': 'image', 'mimeType': data['mimeType'], 'data': data['data']}], 'isError': False}
            else:
                result = {'content': [{'type': 'text', 'text': json.dumps(response, ensure_ascii=False, default=str)}],
                          'isError': 'error' in response or response.get('state') == 'cancelled'}
        except Exception as exc:
            result = {'content': [{'type': 'text', 'text': '%s: %s' % (type(exc).__name__, exc)}], 'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--call', help='Direct diagnostic call using the same MCP tools')
    parser.add_argument('--arguments', default='{}', help='Arguments JSON for --call')
    options = parser.parse_args()
    if options.call:
        print(json.dumps(call_tool(options.call, json.loads(options.arguments)), ensure_ascii=False, default=str))
        return
    for line in sys.stdin:
        try:
            if len(line) > 1024 * 1024:
                raise ValueError('MCP request exceeds 1 MiB')
            request = json.loads(line)
            response = handle(request)
        except (ValueError, TypeError) as exc:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(exc)}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
