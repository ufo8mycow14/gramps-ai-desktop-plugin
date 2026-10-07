"""Native menu models and dialog controls; runs only on the Gramps GTK thread."""
import hashlib
import json

from gi.repository import Gtk, Gdk

WIDGET_OPERATIONS = {'edit_cell', 'choose_cell', 'toggle_cell', 'choose_files', 'set_folder',
                     'set_filename', 'set_color', 'set_font', 'set_calendar'}


class NativeUI:
    def __init__(self, bridge):
        self.bridge = bridge

    def action(self, owner, detailed, root=None):
        if not detailed or '.' not in detailed:
            return None, None, None
        scope, name = detailed.split('.', 1)
        pending, seen = [root, owner], set()
        while pending:
            widget = pending.pop(0)
            if widget is None or hash(widget) in seen:
                continue
            seen.add(hash(widget))
            group = widget.get_action_group(scope)
            if group is None and scope == 'win' and isinstance(widget, Gtk.ApplicationWindow):
                group = widget
            if group is None and scope == 'app' and isinstance(widget, Gtk.Window):
                group = widget.get_application()
            action = group.lookup_action(name) if group else None
            if action is not None:
                return group, action, name
            for getter in ('get_parent', 'get_attach_widget', 'get_relative_to'):
                if hasattr(widget, getter):
                    pending.insert(0, getattr(widget, getter)())
        return None, None, name

    def menu_snapshot(self, args):
        owner = self.bridge.resolve(args['window_id']) if args.get('window_id') else self.bridge.uistate.window
        root = self.bridge.resolve(args['root_id']) if args.get('root_id') else None
        action_root = root
        model = None
        if root is None:
            app = owner.get_application() if isinstance(owner, Gtk.Window) else None
            model = app.get_menubar() if app else None
        elif hasattr(root, 'get_menu_model'):
            model = root.get_menu_model()
        if root is not None and model is None:
            for method in ('get_submenu', 'get_popup', 'get_popover'):
                if hasattr(root, method) and getattr(root, method)() is not None:
                    root = getattr(root, method)()
                    break
        items = []

        def add(path, labels, label, detailed=None, target=None, widget=None):
            group, action, name = self.action(owner, detailed, widget or action_root)
            typ = action.get_parameter_type() if action else None
            state = action.get_state() if action else None
            item = {'path': path, 'label': label, 'labels': labels + ([label] if label else []),
                    'action': detailed, 'target': target.print_(True) if target is not None else None,
                    'parameter_type': typ.dup_string() if typ else None,
                    'state': state.print_(True) if state else None,
                    'action_available': action is not None, 'kind': 'item',
                    'actionable': action is not None or bool(widget and not detailed),
                    'enabled': (action.get_enabled() if action else bool(widget and not detailed))
                               and (widget.is_sensitive() if widget else True)
                               and (action_root.is_sensitive() if action_root else True),
                    'widget_id': self.bridge.identify(widget) if widget else None}
            items.append(item)
            if len(items) > 10000:
                raise ValueError('Menu exceeds discovery limit')
            return item

        def walk_model(current, prefix, labels, ancestors):
            if hash(current) in ancestors or len(ancestors) >= 40:
                raise ValueError('Cyclic or excessively nested menu model')
            ancestors = ancestors | {hash(current)}
            for index in range(current.get_n_items()):
                path = prefix + str(index)
                def attr(name):
                    return current.get_item_attribute_value(index, name, None)
                label = attr('label')
                action = attr('action')
                item = add(path, labels, label.unpack() if label else '', action.unpack() if action else None, attr('target'))
                for link in ('section', 'submenu'):
                    child = current.get_item_link(index, link)
                    if child is not None:
                        item['kind'] = link
                        item['actionable'] = False
                        walk_model(child, path + '/' + link + '/', item['labels'], ancestors)

        def walk_widgets(current, prefix, labels, depth):
            if depth >= 40:
                raise ValueError('Excessively nested GTK menu')
            for index, widget in enumerate(current.get_children() if isinstance(current, Gtk.Container) else []):
                path = prefix + str(index)
                next_labels = labels
                if isinstance(widget, (Gtk.MenuItem, Gtk.Button)):
                    label = widget.get_label() or ''
                    detailed = widget.get_action_name() if isinstance(widget, Gtk.Actionable) else None
                    target = widget.get_action_target_value() if isinstance(widget, Gtk.Actionable) else None
                    item = add(path, labels, label, detailed, target, widget)
                    next_labels = item['labels']
                walk_widgets(widget, path + '/', next_labels, depth + 1)
                if isinstance(widget, Gtk.MenuItem) and widget.get_submenu() is not None:
                    item['kind'] = 'submenu'
                    item['actionable'] = False
                    walk_widgets(widget.get_submenu(), path + '/submenu/', next_labels, depth + 1)

        if model is not None:
            walk_model(model, '', [], set())
        elif root is not None:
            walk_widgets(root, '', [], 0)
        else:
            raise ValueError('No native menu model is available')
        context = {'session': self.bridge.session, 'window_id': self.bridge.identify(owner),
                   'root_id': args.get('root_id'), 'items': items}
        revision = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return owner, items, revision

    def menus(self, args):
        owner, items, revision = self.menu_snapshot(args)
        query = args.get('query', '').casefold()
        matches = [i for i in items if not query or query in json.dumps(i, ensure_ascii=False).casefold()]
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(500, int(args.get('limit', 100))))
        return {'session': self.bridge.session, 'revision': revision, 'items': matches[offset:offset + limit],
                'total': len(items), 'matched': len(matches),
                'next_offset': offset + limit if offset + limit < len(matches) else None}

    def menu(self, args):
        owner, items, revision = self.menu_snapshot(args)
        if args['expected_revision'] != revision:
            raise ValueError('Menu changed; discover its current paths and revision again')
        item = next((i for i in items if i['path'] == args['path']), None)
        if item is not None and item['kind'] != 'item':
            raise ValueError('This menu entry is a heading; select one of its actionable children')
        if item is None or not item['enabled'] or not item['actionable']:
            raise ValueError('Unknown or disabled menu item')
        if item['action']:
            scope, name = item['action'].split('.', 1)
            root = self.bridge.resolve(args['root_id']) if args.get('root_id') else None
            widget = self.bridge.resolve(item['widget_id']) if item['widget_id'] else root
            group, action, name = self.action(owner, item['action'], widget)
            from gi.repository import GLib
            typ = action.get_parameter_type()
            if typ is not None and item['target'] is None:
                raise ValueError('Menu action requires a target; use action with an explicit parameter')
            target = GLib.Variant.parse(typ, item['target'], None, None) if typ else None
            group.activate_action(name, target)
            return {'activated': name, 'scope': scope}
        if item['widget_id']:
            return self.bridge.dispatch('widget', {'widget_id': item['widget_id'], 'operation': 'activate'})
        raise ValueError('This menu entry is a heading; select one of its actionable children')

    def cell_context(self, tree, path, column, renderer):
        if not isinstance(tree, Gtk.TreeView):
            raise ValueError('Cell operations require a TreeView')
        model = tree.get_model()
        parsed = Gtk.TreePath.new_from_string(str(path))
        iterator = model.get_iter(parsed) if model is not None else None
        if iterator is None:
            raise ValueError('No row at this path')
        columns = tree.get_columns()
        if not 0 <= column < len(columns):
            raise ValueError('Column index is out of bounds')
        col = columns[column]
        col.cell_set_cell_data(model, iterator, False, False)
        renderers = col.get_cells()
        if not 0 <= renderer < len(renderers):
            raise ValueError('Renderer index is out of bounds')
        return col, renderers[renderer], parsed

    def cells(self, args):
        tree = self.bridge.resolve(args['widget_id'])
        if not isinstance(tree, Gtk.TreeView):
            raise ValueError('cells requires a TreeView')
        items = []
        for index, column in enumerate(tree.get_columns()):
            for rindex, renderer in enumerate(column.get_cells()):
                if args.get('path') is not None:
                    self.cell_context(tree, args['path'], index, rindex)
                properties = {p.name for p in renderer.list_properties()}
                data = {'column': index, 'renderer': rindex, 'title': column.get_title(),
                        'column_visible': column.get_visible(), 'type': type(renderer).__name__}
                for name in ('editable', 'activatable', 'text', 'active', 'sensitive', 'visible', 'has-entry', 'text-column'):
                    if name in properties:
                        data[name.replace('-', '_')] = renderer.get_property(name)
                if isinstance(renderer, Gtk.CellRendererCombo):
                    choices = renderer.get_property('model')
                    text_column = renderer.get_property('text-column')
                    data['choices'] = [str(row[text_column]) for row in choices][:500] if choices is not None else []
                    data['choice_paths'] = [choices.get_path(row.iter).to_string() for row in choices][:500] if choices is not None else []
                items.append(data)
        return {'cells': items}

    def widget(self, widget, operation, value):
        if not widget.is_sensitive():
            raise ValueError('Widget is disabled by Gramps')
        if operation in ('edit_cell', 'choose_cell', 'toggle_cell'):
            if not isinstance(value, dict):
                raise ValueError('Cell value must include path, column and optional renderer')
            col, cell, path = self.cell_context(widget, value['path'], int(value['column']), int(value.get('renderer', 0)))
            if not col.get_visible() or not cell.get_property('visible') or not cell.get_property('sensitive'):
                raise ValueError('Cell is hidden or disabled')
            if operation in ('edit_cell', 'choose_cell'):
                if not isinstance(cell, Gtk.CellRendererText) or not cell.get_property('editable'):
                    raise ValueError('Cell is not editable text')
                choice = None
                if operation == 'choose_cell':
                    if not isinstance(cell, Gtk.CellRendererCombo):
                        raise ValueError('choose_cell requires a combo cell')
                    choices, column = cell.get_property('model'), cell.get_property('text-column')
                    choice = choices.get_iter(Gtk.TreePath.new_from_string(str(value['choice_path']))) if choices is not None else None
                    if choice is None:
                        raise ValueError('No combo choice at this path')
                    text = str(choices.get_value(choice, column))
                else:
                    text = value['text']
                if not isinstance(text, str):
                    raise ValueError('Cell text must be a string')
                if isinstance(cell, Gtk.CellRendererCombo) and not cell.get_property('has-entry'):
                    choices, column = cell.get_property('model'), cell.get_property('text-column')
                    if choices is None or text not in [str(row[column]) for row in choices]:
                        raise ValueError('Select a current combo cell choice')
                    if choice is None:
                        matches = [row.iter for row in choices if str(row[column]) == text]
                        if len(matches) != 1:
                            raise ValueError('Ambiguous combo text; use choose_cell with a choice_path')
                        choice = matches[0]
                # Native editors may initialise callback state in editing-started
                # (e.g. the surname tab). Emitting edited alone silently skips it.
                if not widget.get_realized():
                    raise ValueError('Show the table and select its notebook page before editing cells')
                editors = []
                handler = cell.connect('editing-started', lambda c, editor, p: editors.append(editor))
                try:
                    widget.set_cursor_on_cell(path, col, cell, True)
                finally:
                    cell.disconnect(handler)
                if not editors:
                    raise ValueError('Cell did not start native editing')
                editor = editors[0]
                try:
                    if isinstance(editor, Gtk.ComboBox) and choice is not None:
                        editor.set_active_iter(choice)
                    else:
                        entry = editor.get_child() if isinstance(editor, Gtk.ComboBox) else editor
                        if not isinstance(entry, Gtk.Entry):
                            raise ValueError('Unsupported cell editor; inspect its native controls')
                        entry.set_text(text)
                    editor.editing_done()
                    editor.remove_widget()
                except Exception:
                    cell.stop_editing(True)
                    editor.remove_widget()
                    raise
            else:
                if not isinstance(cell, Gtk.CellRendererToggle) or not cell.get_property('activatable'):
                    raise ValueError('Cell is not an activatable toggle')
                cell.emit('toggled', path.to_string())
        elif operation in ('choose_files', 'set_folder', 'set_filename'):
            from pathlib import Path
            if not isinstance(widget, Gtk.FileChooser):
                raise ValueError('File selection requires a native FileChooser')
            if operation == 'set_folder':
                if not isinstance(value, str) or not Path(value).is_dir():
                    raise ValueError('set_folder requires an existing directory')
                if hasattr(widget, '_desktop_file_selection'):
                    widget._desktop_file_selection['state'] = 'cancelled'
                    widget._desktop_file_selection = dict(widget._desktop_file_selection)
                if not widget.set_current_folder(str(value)):
                    raise ValueError('File chooser rejected the folder')
            elif operation == 'set_filename':
                if widget.get_action() not in (Gtk.FileChooserAction.SAVE, Gtk.FileChooserAction.CREATE_FOLDER):
                    raise ValueError('set_filename requires a save/create-folder chooser')
                if not isinstance(value, str) or not value or any(c in value for c in ('/', '\\', '\x00')):
                    raise ValueError('set_filename requires a file name without directory separators')
                if hasattr(widget, '_desktop_file_selection'):
                    widget._desktop_file_selection['state'] = 'cancelled'
                    widget._desktop_file_selection = dict(widget._desktop_file_selection)
                widget.set_current_name(value)
            else:
                folders = widget.get_action() == Gtk.FileChooserAction.SELECT_FOLDER
                if not isinstance(value, list) or not all(isinstance(p, str) and
                        (Path(p).is_dir() if folders else Path(p).is_file()) for p in value):
                    raise ValueError('choose_files requires existing paths matching the chooser type')
                if len(value) > 1 and not widget.get_select_multiple():
                    raise ValueError('File chooser does not permit multiple files')
                parents = {str(Path(p).resolve().parent) for p in value}
                if len(parents) > 1:
                    raise ValueError('Select files from one directory in this native chooser')
                previous = widget.get_filenames()
                previous_folder = widget.get_current_folder()
                request = {'state': 'pending', 'requested': value, 'attempts': 0}
                widget._desktop_file_selection = request
                widget.unselect_all()
                if folders and len(value) == 1:
                    widget.set_current_folder(value[0])
                for filename in value:
                    if folders and len(value) == 1:
                        break
                    if not widget.select_filename(filename):
                        request.update(state='failed', error='Native chooser rejected selection')
                        widget.unselect_all()
                        for old in previous:
                            widget.select_filename(old)
                        raise ValueError('File chooser rejected a selection; previous selection requested')
                # GTK loads directory models asynchronously. Its initial calls
                # can retain only the final file until that model is ready.
                from gi.repository import GLib
                import os
                normal = lambda paths: {os.path.normcase(os.path.abspath(p)) for p in paths}
                def finish_selection():
                    if getattr(widget, '_desktop_mcp_destroyed', False) or widget._desktop_file_selection is not request:
                        request['state'] = 'cancelled'
                        return False
                    if normal(widget.get_filenames()) == normal(value):
                        request['state'] = 'done'
                        return False
                    request['attempts'] += 1
                    if request['attempts'] >= 30:
                        request.update(state='failed', error='Native chooser did not confirm all selections; previous selection requested')
                        if previous_folder:
                            widget.set_current_folder(previous_folder)
                        widget.unselect_all()
                        for old in previous:
                            widget.select_filename(old)
                        return False
                    if folders and len(value) == 1:
                        pass  # GTK confirms the browsed directory after model loading.
                    elif len(value) > 1 and normal([widget.get_current_folder()] if widget.get_current_folder() else []) == normal(parents):
                        widget.select_all()
                        wanted = normal(value)
                        for filename in widget.get_filenames():
                            if normal([filename]).isdisjoint(wanted):
                                widget.unselect_filename(filename)
                    else:
                        for filename in value:
                            widget.select_filename(filename)
                    return True
                if finish_selection():
                    GLib.timeout_add(100, finish_selection)
        elif operation == 'set_color':
            if not isinstance(widget, Gtk.ColorChooser) or not isinstance(value, str):
                raise ValueError('set_color requires a ColorChooser and colour string')
            colour = Gdk.RGBA()
            if not colour.parse(value):
                raise ValueError('Invalid colour')
            widget.set_rgba(colour)
        elif operation == 'set_font':
            if not isinstance(widget, Gtk.FontChooser) or not isinstance(value, str) or not value.strip():
                raise ValueError('set_font requires a FontChooser and font description')
            widget.set_font(value)
        elif operation == 'set_calendar':
            import datetime
            if not isinstance(widget, Gtk.Calendar) or not isinstance(value, dict):
                raise ValueError('set_calendar requires a Calendar and year/month/day')
            if any(type(value.get(k)) is not int for k in ('year', 'month', 'day')):
                raise ValueError('Calendar year/month/day must be integers')
            date = datetime.date(value['year'], value['month'], value['day'])
            widget.select_month(date.month - 1, date.year)
            widget.select_day(date.day)
        else:
            raise ValueError('Unsupported native UI operation')
        return self.bridge.describe(widget)
