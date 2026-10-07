"""Gramps 6.1 in-process desktop bridge. GTK operations run on its main thread."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gi.repository import Gtk, Gdk, GLib

MAX_BODY = 1024 * 1024
VERSION = '2.7.0'
INSTANCE = None


class DesktopBridge:
    def __init__(self, dbstate, uistate):
        self.dbstate, self.uistate = dbstate, uistate
        self.session = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(48)
        # GTK owns native objects, but PyGObject wrappers may disappear between
        # requests. Keep wrappers alive until GTK emits destroy.
        self.widgets = {}
        self.sequence = 0
        self.jobs = {}
        self.lock = threading.RLock()
        self.started = time.time()
        from importlib.util import spec_from_file_location, module_from_spec
        paths_spec = spec_from_file_location('gramps_desktop_paths', Path(__file__).with_name('platform_paths.py'))
        paths = module_from_spec(paths_spec)
        paths_spec.loader.exec_module(paths)
        self.runtime = paths.runtime_dir()
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            self.runtime.chmod(0o700)
        self.discovery = self.runtime / 'connection.json'
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != '/command':
                    self.send_error(404)
                    return
                if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + bridge.token):
                    self.send_error(401)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= MAX_BODY:
                        raise ValueError('Request size must be between 1 byte and 1 MiB')
                    request = json.loads(self.rfile.read(size))
                    result = bridge.submit(request)
                    encoded = json.dumps(result, ensure_ascii=False, default=str).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except Exception as exc:
                    self.send_error(400, str(exc))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        info = {'url': 'http://127.0.0.1:%d/command' % self.server.server_port,
                'token': self.token, 'pid': os.getpid(), 'session': self.session,
                'version': VERSION, 'started': self.started}
        temp = self.discovery.with_suffix('.tmp')
        temp.write_text(json.dumps(info), encoding='utf-8')
        if os.name != 'nt':
            temp.chmod(0o600)
        os.replace(temp, self.discovery)
        threading.Thread(target=self.server.serve_forever, daemon=True, name='GrampsDesktopMCP').start()

    def submit(self, request):
        method = request.get('method', '')
        args = request.get('arguments', {})
        if method == 'job':
            with self.lock:
                job = self.jobs.get(args.get('operation_id'))
                if job is None:
                    raise ValueError('Unknown operation ID for this Gramps session')
                return self.job_result(job)
        with self.lock:
            # Bound retained results; never evict running operations.
            if len(self.jobs) >= 200:
                for key, previous in list(self.jobs.items()):
                    if previous['state'] in ('done', 'cancelled'):
                        del self.jobs[key]
                        if len(self.jobs) < 150:
                            break
            job = {'id': uuid.uuid4().hex, 'state': 'queued', 'event': threading.Event()}
            self.jobs[job['id']] = job

        def run():
            with self.lock:
                if job['state'] == 'cancelled':
                    return False
                job['state'] = 'running'
            try:
                result = self.dispatch(method, args)
                with self.lock:
                    job['result'] = result
            except Exception as exc:
                with self.lock:
                    job['error'] = '%s: %s' % (type(exc).__name__, exc)
            finally:
                with self.lock:
                    job['state'] = 'done'
                    job['event'].set()
            return False

        GLib.idle_add(run)
        if not job['event'].wait(15):
            with self.lock:
                if job['state'] == 'queued':
                    job['state'] = 'cancelled'
                    job['error'] = 'GTK did not start this operation; it was cancelled.'
        with self.lock:
            return self.job_result(job)

    def job_result(self, job):
        result = {'session': self.session, 'operation_id': job['id'], 'state': job['state']}
        for key in ('result', 'error'):
            if key in job:
                result[key] = job[key]
        if job['state'] == 'running':
            result['message'] = 'Operation started and has not returned. Query gramps_job; do not repeat a write.'
        return result

    def identify(self, widget):
        key = getattr(widget, '_desktop_mcp_id', None)
        if key is None:
            self.sequence += 1
            key = '%s:%d' % (self.session, self.sequence)
            widget._desktop_mcp_id = key
        self.widgets[key] = widget
        if not getattr(widget, '_desktop_mcp_watch', False):
            def destroyed(w):
                w._desktop_mcp_destroyed = True
                self.widgets.pop(key, None)
            widget.connect('destroy', destroyed)
            widget._desktop_mcp_watch = True
        return key

    def resolve(self, key):
        widget = self.widgets.get(key)
        if widget is None:
            raise ValueError('Stale or unknown widget ID. Inspect the current windows/widgets again.')
        if getattr(widget, '_desktop_mcp_destroyed', False):
            raise ValueError('Widget has been destroyed. Inspect the current windows/widgets again.')
        return widget

    def describe(self, widget):
        self.identify(widget)
        result = {'id': self.identify(widget), 'type': type(widget).__name__,
                  'name': widget.get_name(), 'visible': widget.get_visible(),
                  'sensitive': widget.is_sensitive()}
        for method, key in (('get_title', 'title'), ('get_label', 'label'), ('get_text', 'text'),
                            ('get_active', 'active'), ('get_value', 'value'),
                            ('get_tooltip_text', 'tooltip'), ('get_buildable_id', 'buildable_id')):
            try:
                value = getattr(widget, method)()
                if value is not None and isinstance(value, (str, int, float, bool)):
                    result[key] = value[:2000] if isinstance(value, str) else value
            except (AttributeError, TypeError):
                pass
        try:
            result['builder_name'] = Gtk.Buildable.get_name(widget)
        except (TypeError, AttributeError):
            pass
        if isinstance(widget, Gtk.TextView):
            buf = widget.get_buffer()
            # Gramps's StyledTextBuffer overrides get_text with StyledText.
            # Use GTK's plain-text reader for a UI snapshot.
            result['text'] = Gtk.TextBuffer.get_text(buf, buf.get_start_iter(), buf.get_end_iter(), True)[:2000]
        if isinstance(widget, Gtk.Notebook):
            result['page'] = widget.get_current_page()
            result['pages'] = [widget.get_tab_label_text(widget.get_nth_page(i)) for i in range(widget.get_n_pages())]
        if isinstance(widget, Gtk.Actionable):
            result['action_name'] = widget.get_action_name()
            target = widget.get_action_target_value()
            result['action_target'] = target.print_(True) if target is not None else None
        if isinstance(widget, Gtk.ColorChooser):
            result['colour'] = widget.get_rgba().to_string()
        if isinstance(widget, Gtk.FontChooser):
            result['font'] = widget.get_font()
        if isinstance(widget, Gtk.Calendar):
            year, month, day = widget.get_date()
            result['date'] = {'year': year, 'month': month + 1, 'day': day}
        if isinstance(widget, Gtk.FileChooser):
            result.update(folder=widget.get_current_folder(), filenames=widget.get_filenames(),
                          multiple=widget.get_select_multiple(), chooser_action=int(widget.get_action()))
            if hasattr(widget, '_desktop_file_selection'):
                result['file_selection'] = dict(widget._desktop_file_selection)
        alloc = widget.get_allocation()
        result['allocation'] = [alloc.x, alloc.y, alloc.width, alloc.height]
        return result

    def windows(self):
        return [self.describe(w) for w in Gtk.Window.list_toplevels() if w.get_visible()]

    def dispatch(self, method, a):
        from importlib.util import spec_from_file_location, module_from_spec
        if method in ('menus', 'menu', 'cells') or (method == 'widget' and a.get('operation') in
                ('edit_cell', 'choose_cell', 'toggle_cell', 'choose_files', 'set_folder', 'set_filename', 'set_color', 'set_font', 'set_calendar')):
            if not hasattr(self, 'native_ui'):
                spec = spec_from_file_location('gramps_desktop_ui', Path(__file__).with_name('ui_support.py'))
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.native_ui = module.NativeUI(self)
            if method == 'widget':
                widget = self.resolve(a['widget_id'])
                if not widget.is_sensitive():
                    raise ValueError('Widget is disabled by Gramps')
                return self.native_ui.widget(widget, a['operation'], a.get('value'))
            return getattr(self.native_ui, method)(a)
        if method in ('capabilities', 'schema', 'object', 'find', 'relatives', 'links', 'mutate',
                      'family_member', 'attach', 'compare', 'merge', 'media_info', 'research',
                      'history', 'workflow', 'plugins', 'settings', 'rows', 'date',
                      'filter', 'report', 'export', 'graph', 'audit', 'tree', 'secondary', 'navigation', 'gramplets', 'batch', 'batch_attach', 'batch_file', 'media_manage', 'sync_apply', 'sync_refs'):
            if not hasattr(self, 'support'):
                spec = spec_from_file_location('gramps_desktop_support', Path(__file__).with_name('support.py'))
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.support = module.GrampsSupport(self)
            return self.support.dispatch(method, a)
        if method == 'status':
            db = self.dbstate.db
            opened = db.is_open()
            return {'pid': os.getpid(), 'session': self.session, 'bridge_version': VERSION,
                    'gtk': '%d.%d.%d' %
                    (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()),
                    'database_open': opened, 'database_name': db.get_dbname() if opened else None,
                    'database_readonly': db.readonly if opened else None,
                    'ui_initialised': bool(getattr(self.uistate.viewmanager, 'active_page', None)),
                    'windows': self.windows()}
        if method == 'windows':
            return self.windows()
        if method == 'selection':
            w = self.resolve(a['widget_id'])
            if isinstance(w, Gtk.TreeView):
                model, paths = w.get_selection().get_selected_rows()
                return {'paths': [p.to_string() for p in paths],
                        'mode': w.get_selection().get_mode().value_nick}
            if isinstance(w, Gtk.ComboBox):
                return {'active': w.get_active(), 'active_id': w.get_active_id()}
            if isinstance(w, Gtk.Notebook):
                return {'page': w.get_current_page()}
            raise ValueError('selection requires a TreeView, ComboBox or Notebook')
        if method == 'widgets':
            root = self.resolve(a['root_id'])
            limit = max(1, min(int(a.get('limit', 250)), 2000))
            depth_limit = max(0, min(int(a.get('depth', 12)), 40))
            offset = max(0, int(a.get('offset', 0)))
            needle = a.get('query', '').casefold()
            items = []
            pending = [(root, None, 0)]
            scanned = 0
            while pending and scanned < 10000:
                w, parent, depth = pending.pop()
                scanned += 1
                d = self.describe(w)
                d.update(parent=parent, depth=depth)
                if not needle or needle in json.dumps(d, ensure_ascii=False).casefold():
                    items.append(d)
                if depth < depth_limit and isinstance(w, Gtk.Container):
                    children = list(w.get_children())
                    for getter in ('get_submenu', 'get_popup', 'get_popover'):
                        if hasattr(w, getter):
                            child = getattr(w, getter)()
                            if child is not None and child not in children:
                                children.append(child)
                    pending.extend((c, d['id'], depth + 1) for c in reversed(children))
            return {'items': items[offset:offset + limit], 'total_matches': len(items),
                    'scan_truncated': bool(pending), 'next_offset': offset + limit if offset + limit < len(items) else None}
        if method == 'widget':
            w = self.resolve(a['widget_id'])
            op, value = a['operation'], a.get('value')
            if not w.is_sensitive() and op not in ('show', 'hide', 'close'):
                raise ValueError('Widget is disabled by Gramps')
            if op == 'activate':
                if isinstance(w, Gtk.Button):
                    w.clicked()
                elif isinstance(w, Gtk.MenuItem):
                    w.activate()
                elif not w.activate():
                    raise ValueError('Widget did not accept activation; inspect actions or use gramps_python')
            elif op == 'set_text':
                if not isinstance(value, str):
                    raise ValueError('set_text requires a string')
                if isinstance(w, Gtk.TextView):
                    if not w.get_editable():
                        raise ValueError('Text view is not editable')
                    buf = w.get_buffer()
                    from gramps.gui.widgets.styledtextbuffer import StyledTextBuffer
                    if isinstance(buf, StyledTextBuffer):
                        from gramps.gen.lib import StyledText
                        buf.set_text(StyledText(value))
                    else:
                        buf.set_text(value)
                elif isinstance(w, Gtk.Entry):
                    if not w.get_editable():
                        raise ValueError('Entry is not editable')
                    w.set_text(value)
                else:
                    raise ValueError('set_text requires an Entry or TextView')
            elif op == 'set_value':
                import math
                if not isinstance(w, (Gtk.Range, Gtk.SpinButton)) or type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('set_value requires a numeric Range or SpinButton value')
                adjustment = w.get_adjustment()
                upper = adjustment.get_upper() - adjustment.get_page_size()
                if not adjustment.get_lower() <= value <= upper:
                    raise ValueError('Value is outside the control bounds')
                w.set_value(value)
            elif op == 'set_active':
                if isinstance(w, (Gtk.ToggleButton, Gtk.Switch, Gtk.CheckMenuItem)):
                    if type(value) is not bool:
                        raise ValueError('Toggle state must be a boolean')
                elif isinstance(w, Gtk.ComboBox):
                    model = w.get_model()
                    count = model.iter_n_children(None) if model is not None else 0
                    if type(value) is not int or not -1 <= value < count:
                        raise ValueError('Combo index is outside its current choices')
                else:
                    raise ValueError('set_active requires a toggle, switch, menu check or ComboBox')
                w.set_active(value)
            elif op == 'set_active_id':
                if not isinstance(w, Gtk.ComboBox) or not isinstance(value, str):
                    raise ValueError('set_active_id requires a ComboBox and a string ID')
                if not w.set_active_id(value):
                    raise ValueError('No combo choice with that ID')
            elif op == 'popup_menu':
                if not w.emit('popup-menu'):
                    raise ValueError('Control did not open a context menu')
            elif op == 'select_rows':
                if not isinstance(w, Gtk.TreeView) or not isinstance(value, list):
                    raise ValueError('select_rows requires a TreeView and a list of row paths')
                selection = w.get_selection()
                if len(value) > 1 and selection.get_mode() != Gtk.SelectionMode.MULTIPLE:
                    raise ValueError('This control does not allow multiple selection')
                paths = [Gtk.TreePath.new_from_string(str(p)) for p in value]
                for path in paths:
                    if w.get_model() is None or w.get_model().get_iter(path) is None:
                        raise ValueError('No row at this path')
                selection.unselect_all()
                for path in paths:
                    selection.select_path(path)
            elif op == 'select_tab':
                index = int(value)
                if not 0 <= index < w.get_n_pages():
                    raise ValueError('Tab index is out of bounds')
                w.set_current_page(index)
            elif op in ('select_row', 'activate_row'):
                path = Gtk.TreePath.new_from_string(str(value))
                model = w.get_model()
                if model.get_iter(path) is None:
                    raise ValueError('No row at this path')
                w.get_selection().select_path(path)
                w.set_cursor(path, None, False)
                w.scroll_to_cell(path, None, False, 0, 0)
                if op == 'activate_row':
                    w.row_activated(path, w.get_columns()[0])
            elif op == 'focus':
                w.grab_focus()
            elif op == 'close':
                if not isinstance(w, Gtk.Window):
                    raise ValueError('close requires a window')
                w.close()
                return {'close_requested': True}
            elif op == 'show':
                w.show()
                if isinstance(w, Gtk.Window):
                    w.present()
            elif op == 'hide':
                w.hide()
            elif op == 'resize':
                w.resize(int(value[0]), int(value[1]))
            elif op == 'move':
                w.move(int(value[0]), int(value[1]))
            elif op == 'choose_file':
                if not w.set_filename(str(value)):
                    raise ValueError('File chooser rejected the path')
            elif op == 'response':
                w.response(int(value))
                return {'response_sent': value}
            else:
                raise ValueError('Unknown widget operation: ' + op)
            if getattr(w, '_desktop_mcp_destroyed', False):
                return {'operation': op, 'widget_destroyed': True}
            return self.describe(w)
        if method in ('actions', 'action'):
            w = self.resolve(a['window_id']) if a.get('window_id') else self.uistate.window
            scope = a.get('scope', 'win')
            group = w.get_application() if scope == 'app' else w
            if method == 'actions':
                items = []
                for name in sorted(group.list_actions()):
                    action = group.lookup_action(name)
                    typ = action.get_parameter_type()
                    state = action.get_state()
                    items.append({'name': name, 'enabled': action.get_enabled(),
                                  'parameter_type': typ.dup_string() if typ else None,
                                  'state': state.print_(True) if state else None})
                return items
            name = a['name'].removeprefix(scope + '.')
            action = group.lookup_action(name)
            if action is None or not action.get_enabled():
                raise ValueError('Unknown or disabled action: ' + name)
            typ = action.get_parameter_type()
            value = None
            if typ is not None:
                if 'parameter' not in a:
                    raise ValueError('This action requires GVariant parameter text')
                value = GLib.Variant.parse(typ, a['parameter'], None, None)
            elif a.get('parameter') is not None:
                raise ValueError('This action takes no parameter')
            group.activate_action(name, value)
            return {'activated': name, 'scope': scope}
        if method in ('views', 'view'):
            vm = self.uistate.viewmanager
            if method == 'views':
                return [{'category': c, 'view': v, 'name': p[0].name,
                         'category_name': p[0].category[1], 'plugin_id': p[0].id}
                        for c, cat in enumerate(vm.get_views()) for v, p in enumerate(cat)]
            page = vm.goto_page(int(a['category']), a.get('view'))
            return {'title': page.get_title()}
        if method in ('records', 'record', 'editor'):
            db = self.dbstate.db
            if not db.is_open():
                raise ValueError('Open a tree in Gramps first; the bridge never opens a second database')
            kind = a.get('kind', 'person').lower()
            classes = {'person': 'Person', 'family': 'Family', 'event': 'Event', 'place': 'Place',
                       'source': 'Source', 'citation': 'Citation', 'repository': 'Repository',
                       'media': 'Media', 'note': 'Note', 'tag': 'Tag'}
            if kind not in classes:
                raise ValueError('Unsupported record type')
            obj = None
            if a.get('handle'):
                obj = getattr(db, 'get_%s_from_handle' % kind)(a['handle'])
            elif a.get('gramps_id'):
                obj = getattr(db, 'get_%s_from_gramps_id' % kind)(a['gramps_id'])
            if method == 'record':
                if obj is None:
                    raise ValueError('Record not found; supply its handle or Gramps ID')
                return {'kind': kind, 'handle': obj.handle, 'gramps_id': getattr(obj, 'gramps_id', None),
                        'serialized': obj.serialize()}
            if method == 'editor':
                from gramps.gui.editors import EDITORS, CLASSES
                name = classes[kind]
                if not a.get('new', False) and obj is None:
                    raise ValueError('Existing record not found')
                if db.readonly:
                    raise ValueError('Gramps opened this tree read-only')
                if kind == 'tag':
                    from gramps.gui.views.tags import EditTag
                    from gramps.gen.lib import Tag
                    EditTag(db, self.uistate, [], Tag() if a.get('new') else obj)
                else:
                    EDITORS[name](self.dbstate, self.uistate, [], CLASSES[name]() if a.get('new') else obj)
                return self.windows()
            from gramps.gen.display.name import displayer
            needle = a.get('query', '').casefold()
            offset, limit = max(0, int(a.get('offset', 0))), max(1, min(int(a.get('limit', 50)), 200))
            items, matched, more = [], 0, False
            for handle in getattr(db, 'get_%s_handles' % kind)():
                item = getattr(db, 'get_%s_from_handle' % kind)(handle)
                label = displayer.display(item) if kind == 'person' else ''
                for accessor in ('get_title', 'get_description', 'get_name', 'get'):
                    if not label and hasattr(item, accessor):
                        try:
                            label = str(getattr(item, accessor)())[:500]
                        except TypeError:
                            pass
                ident = getattr(item, 'gramps_id', None)
                if needle and needle not in ('%s %s %s' % (ident, label, handle)).casefold():
                    continue
                if matched >= offset:
                    if len(items) >= limit:
                        more = True
                        break
                    items.append({'handle': handle, 'gramps_id': ident, 'label': label})
                matched += 1
            return {'items': items, 'next_offset': offset + len(items) if more else None}
        if method == 'python':
            # Deliberately privileged: user requested full application control.
            # Not a sandbox. Never use untrusted source text as executable code.
            env = {'bridge': self, 'dbstate': self.dbstate, 'db': self.dbstate.db,
                   'uistate': self.uistate, 'viewmanager': self.uistate.viewmanager,
                   'Gtk': Gtk, 'Gdk': Gdk, 'GLib': GLib, 'result': None}
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(a['code'], '<gramps-desktop-plugin>', 'exec'), env, env)
            return {'result': env['result'], 'output': output.getvalue()[:20000]}
        if method == 'screenshot':
            w = self.resolve(a['window_id'])
            gdk_window = w.get_window()
            if gdk_window is None:
                raise ValueError('Window is not realised')
            width, height = w.get_size()
            pix = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, width, height)
            if pix is None:
                raise ValueError('GTK could not capture this window')
            max_width = max(200, min(int(a.get('max_width', 1400)), 3000))
            if width > max_width:
                from gi.repository import GdkPixbuf
                pix = pix.scale_simple(max_width, max(1, int(height * max_width / width)), GdkPixbuf.InterpType.BILINEAR)
            ok, data = pix.save_to_bufferv('png', [], [])
            if not ok:
                raise ValueError('Screenshot encoding failed')
            return {'mimeType': 'image/png', 'data': base64.b64encode(data).decode('ascii')}
        raise ValueError('Unknown command: ' + method)


def start(dbstate, uistate):
    global INSTANCE
    if uistate is not None and INSTANCE is None:
        INSTANCE = DesktopBridge(dbstate, uistate)
    return INSTANCE
