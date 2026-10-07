"""Menu/control regressions on disposable GTK objects, plus read-only live inventory."""
import importlib.util
import json
import uuid
from pathlib import Path


def run_inside(bridge, live_inventory=True):
    from types import SimpleNamespace
    from gi.repository import Gtk, Gio, GLib
    spec = importlib.util.spec_from_file_location('ui_test', Path(__file__).with_name('ui_support.py'))
    ui_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui_module)
    app = Gtk.Application(application_id='org.example.GrampsDesktopMenuTests.t' + uuid.uuid4().hex,
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = Gtk.ApplicationWindow(application=app)
    test = bridge.__class__.__new__(bridge.__class__)
    test.session, test.widgets, test.sequence = 'synthetic-menu-test', {}, 0
    test.uistate = SimpleNamespace(window=window)
    ui = ui_module.NativeUI(test)
    checks = []
    activated = []
    disposable = []
    def check(name, condition):
        assert condition, name
        checks.append(name)
        import os
        if os.environ.get('GRAMPS_MENU_TEST_PROGRESS'):
            Path(os.environ['GRAMPS_MENU_TEST_PROGRESS']).write_text(json.dumps(checks), encoding='utf-8')
    def rejected(name, operation, unchanged):
        try:
            operation()
        except (ValueError, TypeError):
            check(name, unchanged())
        else:
            raise AssertionError(name + ': invalid operation accepted')
    try:
        action = Gio.SimpleAction.new('pick', GLib.VariantType.new('s'))
        action.connect('activate', lambda a, p: activated.append(p.unpack()))
        window.add_action(action)
        disabled = Gio.SimpleAction.new('disabled', None)
        disabled.set_enabled(False)
        window.add_action(disabled)
        menu, sub = Gio.Menu(), Gio.Menu()
        item = Gio.MenuItem.new('Synthetic choice', None)
        item.set_action_and_target_value('win.pick', GLib.Variant('s', 'observed-target'))
        sub.append_item(item)
        sub.append('Disabled', 'win.disabled')
        menu.append_submenu('Synthetic menu', sub)
        app.set_menubar(menu)
        listing = ui.menus({})
        choice = next(i for i in listing['items'] if i['action'] == 'win.pick')
        ui.menu({'path': choice['path'], 'expected_revision': listing['revision']})
        check('native_menu_path_and_target_activation', activated == ['observed-target'])
        sub.append('New item', 'win.pick')
        try:
            ui.menu({'path': choice['path'], 'expected_revision': listing['revision']})
        except ValueError:
            check('stale_menu_revision_rejected_without_activation', len(activated) == 1)
        else:
            raise AssertionError('Stale menu activated')
        current = ui.menus({})
        disabled_path = next(i['path'] for i in current['items'] if i['action'] == 'win.disabled')
        try:
            ui.menu({'path': disabled_path, 'expected_revision': current['revision']})
        except ValueError:
            check('disabled_menu_rejected', len(activated) == 1)
        else:
            raise AssertionError('Disabled menu activated')
        legacy = Gtk.Menu()
        legacy_item = Gtk.MenuItem(label='Legacy action')
        legacy.append(legacy_item)
        legacy_item.connect('activate', lambda item: activated.append('legacy'))
        listing = ui.menus({'root_id': test.identify(legacy)})
        ui.menu({'root_id': test.identify(legacy), 'path': listing['items'][0]['path'], 'expected_revision': listing['revision']})
        check('legacy_popup_menu_discovery_and_activation', activated[-1] == 'legacy')
        legacy.destroy()
        group = Gio.SimpleActionGroup()
        local_action = Gio.SimpleAction.new('local', None)
        local_action.connect('activate', lambda a, p: activated.append('local'))
        group.add_action(local_action)
        box, button = Gtk.Box(), Gtk.MenuButton()
        box.pack_start(button, False, False, 0)
        window.add(box)
        box.insert_action_group('menu', group)
        local_menu = Gio.Menu()
        local_menu.append('Widget scoped action', 'menu.local')
        button.set_menu_model(local_menu)
        local_id = test.identify(button)
        listing = ui.menus({'root_id': local_id})
        check('inherited_widget_local_menu_scope_discovered', listing['items'][0]['action_available'])
        ui.menu({'root_id': local_id, 'path': '0', 'expected_revision': listing['revision']})
        check('widget_local_menu_scope_activated', activated[-1] == 'local')
        button.set_sensitive(False)
        listing = ui.menus({'root_id': local_id})
        rejected('disabled_menu_root_preserves_action',
                 lambda: ui.menu({'root_id': local_id, 'path': '0', 'expected_revision': listing['revision']}),
                 lambda: activated.count('local') == 1)
        button.set_sensitive(True)
        model = Gtk.ListStore(str, bool)
        model.append(['Before', False])
        tree = Gtk.TreeView(model=model)
        text = Gtk.CellRendererText(editable=True)
        toggle = Gtk.CellRendererToggle(activatable=True)
        tree.append_column(Gtk.TreeViewColumn('Text', text, text=0))
        tree.append_column(Gtk.TreeViewColumn('Toggle', toggle, active=1))
        text.connect('edited', lambda c, p, v: model.set_value(model.get_iter(p), 0, v))
        toggle.connect('toggled', lambda c, p: model.set_value(model.get_iter(p), 1, not model[p][1]))
        box.pack_start(tree, True, True, 0)
        window.show_all()
        cells = ui.cells({'widget_id': test.identify(tree), 'path': '0'})['cells']
        check('native_cell_editability_discovery', cells[0]['editable'] and cells[1]['activatable'])
        ui.widget(tree, 'edit_cell', {'path': '0', 'column': 0, 'text': 'After'})
        ui.widget(tree, 'toggle_cell', {'path': '0', 'column': 1})
        check('native_edited_and_toggled_signals', model[0][:] == ['After', True])
        text.set_property('sensitive', False)
        rejected('disabled_cell_preserves_data',
                 lambda: ui.widget(tree, 'edit_cell', {'path': '0', 'column': 0, 'text': 'Rejected'}),
                 lambda: model[0][0] == 'After')
        text.set_property('sensitive', True)
        rejected('invalid_cell_path_preserves_data',
                 lambda: ui.widget(tree, 'edit_cell', {'path': '99', 'column': 0, 'text': 'Rejected'}),
                 lambda: model[0][0] == 'After')
        tree.get_columns()[0].set_visible(False)
        rejected('hidden_column_preserves_data',
                 lambda: ui.widget(tree, 'edit_cell', {'path': '0', 'column': 0, 'text': 'Rejected'}),
                 lambda: model[0][0] == 'After')
        tree.get_columns()[0].set_visible(True)
        text.set_property('editable', False)
        try:
            ui.widget(tree, 'edit_cell', {'path': '0', 'column': 0, 'text': 'Rejected'})
        except ValueError:
            check('noneditable_cell_preserves_data', model[0][0] == 'After')
        else:
            raise AssertionError('Noneditable cell changed')
        stateful_model = Gtk.ListStore(str)
        stateful_model.append(['Before'])
        stateful = Gtk.TreeView(model=stateful_model)
        state_cell = Gtk.CellRendererText(editable=True)
        stateful.append_column(Gtk.TreeViewColumn('Stateful surname-style text', state_cell, text=0))
        editing = {'path': None, 'events': []}
        def started(c, editor, path):
            editing['path'] = path
            editing['events'].append('started')
        def edited(c, path, value):
            if editing['path'] is not None:
                editing['events'].append('edited')
                stateful_model[path][0] = value
        state_cell.connect('editing-started', started)
        state_cell.connect('edited', edited)
        box.pack_start(stateful, True, True, 0)
        stateful.show_all()
        ui.widget(stateful, 'edit_cell', {'path': '0', 'column': 0, 'text': 'Surname'})
        check('stateful_native_editor_lifecycle', stateful_model[0][0] == 'Surname' and editing['events'] == ['started', 'edited'])
        choices = Gtk.ListStore(str)
        choices.append(['Alpha'])
        choices.append(['Beta'])
        combo_cell = Gtk.CellRendererCombo(editable=True, has_entry=False, model=choices, text_column=0)
        stateful.append_column(Gtk.TreeViewColumn('Combo', combo_cell, text=0))
        combo_events = []
        combo_cell.connect('editing-started', lambda c, e, p: combo_events.append('started'))
        combo_cell.connect('editing-started', lambda c, e, p: e.connect('changed', lambda cmb: combo_events.append('changed')))
        combo_cell.connect('edited', lambda c, p, v: stateful_model.set_value(stateful_model.get_iter(p), 0, v))
        ui.widget(stateful, 'choose_cell', {'path': '0', 'column': 1, 'choice_path': '1'})
        check('combo_cell_native_choice_and_callbacks', stateful_model[0][0] == 'Beta' and combo_events == ['started', 'changed'])
        rejected('combo_cell_invalid_choice_preserves_data',
                 lambda: ui.widget(stateful, 'edit_cell', {'path': '0', 'column': 1, 'text': 'Absent'}),
                 lambda: stateful_model[0][0] == 'Beta')
        disposable.append(stateful)
        calendar = Gtk.Calendar()
        ui.widget(calendar, 'set_calendar', {'year': 2024, 'month': 2, 'day': 29})
        check('calendar_selection_and_readback', test.describe(calendar)['date'] == {'year': 2024, 'month': 2, 'day': 29})
        for bad in ({'year': 2023, 'month': 2, 'day': 29}, {'year': 2024.5, 'month': 2, 'day': 1}):
            rejected('invalid_calendar_preserves_date', lambda: ui.widget(calendar, 'set_calendar', bad),
                     lambda: calendar.get_date() == (2024, 1, 29))
        colour = Gtk.ColorButton()
        ui.widget(colour, 'set_color', '#123456')
        check('colour_selection_and_readback', 'colour' in test.describe(colour))
        previous_colour = colour.get_rgba().to_string()
        rejected('invalid_colour_preserves_selection', lambda: ui.widget(colour, 'set_color', 'invalid colour'),
                 lambda: colour.get_rgba().to_string() == previous_colour)
        font = Gtk.FontButton()
        ui.widget(font, 'set_font', 'Sans 12')
        check('font_selection_and_readback', test.describe(font)['font'] == 'Sans 12')
        toggle_button = Gtk.CheckButton()
        combo = Gtk.ComboBoxText()
        combo.append_text('Alpha')
        combo.set_active(0)
        for control, bad in ((toggle_button, 'false'), (combo, 99)):
            rejected('invalid_active_preserves_selection',
                     lambda: test.dispatch('widget', {'widget_id': test.identify(control), 'operation': 'set_active', 'value': bad}),
                     lambda: control.get_active() == (False if control is toggle_button else 0))
        disposable.extend((toggle_button, combo))
        spin = Gtk.SpinButton.new_with_range(0, 10, 1)
        disposable.append(spin)
        test.dispatch('widget', {'widget_id': test.identify(spin), 'operation': 'set_value', 'value': 5})
        check('numeric_control_value_and_readback', spin.get_value() == 5)
        rejected('invalid_numeric_value_preserves_control',
                 lambda: test.dispatch('widget', {'widget_id': test.identify(spin), 'operation': 'set_value', 'value': 99}),
                 lambda: spin.get_value() == 5)
        plain = Gtk.TextView()
        disposable.append(plain)
        test.dispatch('widget', {'widget_id': test.identify(plain), 'operation': 'set_text', 'value': 'Plain café text'})
        check('plain_text_buffer_edit_and_read', test.describe(plain)['text'] == 'Plain café text')
        import tempfile, time
        def settle(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            return predicate()
        with tempfile.TemporaryDirectory() as temp:
            files = [Path(temp) / name for name in ('one.txt', 'two.txt')]
            for file in files:
                file.write_text('Synthetic selection fixture', encoding='utf-8')
            chooser = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.OPEN)
            chooser.set_select_multiple(True)
            disposable.append(chooser)
            chooser_window = Gtk.Window()
            chooser_window.add(chooser)
            chooser_window.show_all()
            disposable.append(chooser_window)
            ui.widget(chooser, 'set_folder', temp)
            settle(lambda: chooser.get_current_folder() == temp)
            ui.widget(chooser, 'choose_files', [str(f) for f in files])
            selected = settle(lambda: set(chooser.get_filenames()) == {str(f) for f in files})
            if not selected:
                raise AssertionError('Multiple file selection: ' + repr(chooser.get_filenames()) + ' requested ' + repr([str(f) for f in files]))
            check('multiple_file_selection_and_readback', selected)
            rejected('invalid_file_preserves_selection',
                     lambda: ui.widget(chooser, 'choose_files', [str(files[0]), str(Path(temp) / 'missing')]),
                     lambda: set(chooser.get_filenames()) == {str(f) for f in files})
            rejected('invalid_folder_preserves_selection',
                     lambda: ui.widget(chooser, 'set_folder', str(Path(temp) / 'missing')),
                     lambda: chooser.get_current_folder() == temp)
            save = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.SAVE)
            disposable.append(save)
            ui.widget(save, 'set_folder', temp)
            ui.widget(save, 'set_filename', 'synthetic.txt')
            check('save_chooser_name_without_file_write', save.get_current_name() == 'synthetic.txt' and not (Path(temp) / 'synthetic.txt').exists())
            rejected('invalid_save_name_preserves_selection', lambda: ui.widget(save, 'set_filename', '../invalid'),
                     lambda: save.get_current_name() == 'synthetic.txt')
            folder = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.SELECT_FOLDER)
            disposable.append(folder)
            folder_window = Gtk.Window()
            folder_window.add(folder)
            folder_window.show_all()
            disposable.append(folder_window)
            ui.widget(folder, 'choose_files', [temp])
            selected = settle(lambda: folder.get_filename() == temp)
            if not selected:
                raise AssertionError('Folder selection: ' + repr(test.describe(folder)))
            check('folder_chooser_selection', selected)
        for widget in (tree, calendar, colour, font):
            widget.destroy()
        if not live_inventory:
            return {'passed': len(checks), 'checks': checks, 'family_record_writes': 0}
        live = bridge.dispatch('menus', {'limit': 500})
        from gramps.gui.uimanager import valid_action_name
        groups = {g: bridge.dispatch('plugins', {'kind': g}) for g in ('tool', 'report')}
        for group, plugins in groups.items():
            for plugin in plugins:
                check_name = 'normalised_' + group + '_action_mapping'
                expected = valid_action_name(plugin['id'])
                assert plugin['action_name'] == expected
                assert plugin['action_available'] == (bridge.uistate.window.lookup_action(expected) is not None)
            checks.append('normalised_' + group + '_action_mapping')
        from gramps.gen.plug import PluginRegister
        registered = PluginRegister.get_instance()
        by_kind = {'tool': registered.tool_plugins(), 'report': registered.report_plugins()}
        plugin_metadata, gui_missing = {}, {}
        manager = bridge.uistate.viewmanager._pmgr
        for kind, plugins in by_kind.items():
            gui = manager.get_reg_tools() if kind == 'tool' else manager.get_reg_reports()
            gui_ids = {p.id for p in gui}
            plugin_metadata[kind] = [{'id': p.id, 'name': p.name, 'action': valid_action_name(p.id),
                                      'gui_eligible': p.id in gui_ids, 'supported': bool(p.supported),
                                      'gui_registered': bridge.uistate.window.lookup_action(valid_action_name(p.id)) is not None}
                                     for p in plugins]
            gui_missing[kind] = [p['id'] for p in plugin_metadata[kind] if p['gui_eligible'] and not p['gui_registered']]
            check('all_gui_' + kind + '_actions_resolve', not gui_missing[kind])
        return {'passed': len(checks), 'checks': checks,
                'menus': {'total': live['total'], 'actionable': sum(i['actionable'] for i in live['items']),
                          'unresolved_actions': [i['action'] for i in live['items'] if i['kind'] == 'item' and i['action'] and not i['action_available']],
                          'headings_with_legacy_action': [i['action'] for i in live['items'] if i['kind'] != 'item' and i['action']]},
                'plugins': plugin_metadata, 'gui_missing': gui_missing, 'family_record_writes': 0}
    finally:
        for widget in disposable:
            widget.destroy()
        window.destroy()
        app.quit()


if __name__ == '__main__':
    import server
    code = "import importlib.util\ns=importlib.util.spec_from_file_location('menu_checks', %r)\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\nresult=m.run_inside(bridge)" % str(Path(__file__).resolve())
    response = server.call_tool('gramps_python', {'code': code})
    if 'error' in response or response.get('state') != 'done':
        raise RuntimeError(response)
    receipt = response['result']['result']
    Path(__file__).with_name('menu_verification.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ('passed', 'menus', 'family_record_writes')}))
