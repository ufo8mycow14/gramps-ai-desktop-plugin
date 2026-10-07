"""Native record navigation and explicit sidebar/bottombar Gramplet control."""
from gramps.gen.plug import PluginRegister, BasePluginManager

NAV_TYPES = {'person': 'Person', 'family': 'Family', 'event': 'Event', 'place': 'Place',
             'source': 'Source', 'citation': 'Citation', 'repository': 'Repository', 'media': 'Media', 'note': 'Note'}


def navigation(support, a):
    kind = a.get('kind', 'person')
    if kind not in NAV_TYPES:
        raise ValueError('Select one of the nine native navigation kinds')
    group = a.get('nav_group', 0)
    if type(group) is not int or not 0 <= group <= 100:
        raise ValueError('nav_group must be an observed group number from 0–100')
    ui = support.bridge.uistate
    history = ui.get_history(NAV_TYPES[kind], group)
    if history is None:
        raise ValueError('This navigation kind/group is not registered by the current view')
    operation = a.get('operation', 'get')
    if operation == 'activate':
        record = support.get(kind, a.get('handle'), a.get('gramps_id'))
        ui.set_active(record.handle, NAV_TYPES[kind], group)
    elif operation in ('back', 'forward'):
        index = history.index + (-1 if operation == 'back' else 1)
        if not 0 <= index < len(history.history):
            raise ValueError('No native history entry in that direction')
        # Reject stale/deleted history entries before native callbacks run.
        support.get(kind, handle=history.history[index])
        getattr(history, operation)()
    elif operation != 'get':
        raise ValueError('Select get, activate, back or forward')
    return {'kind': kind, 'nav_group': group, 'active_handle': ui.get_active(NAV_TYPES[kind], group),
            'history': list(history.history)[-200:], 'history_total': len(history.history),
            'index': history.index, 'can_back': history.index > 0,
            'can_forward': history.index + 1 < len(history.history), 'records_changed': False}


def gramplets(support, a, revision):
    from gramps.gui.widgets.grampletbar import GrampletBar
    from gramps.gui.widgets.grampletpane import GET_GRAMPLET_LIST
    page = support.bridge.uistate.viewmanager.active_page
    location = a.get('location', 'sidebar')
    if location not in ('sidebar', 'bottombar'):
        raise ValueError('Select sidebar or bottombar on the active view')
    bar = getattr(page, location, None)
    if not isinstance(bar, GrampletBar):
        raise ValueError('The active view has no supported GrampletBar at this location')
    current = list(bar.all_gramplets())
    names = [name if isinstance(name, str) else name.gname for name in current]
    available = [{'label': label, 'id': ident} for label, ident in GET_GRAMPLET_LIST(bar.pageview.navigation_type(), [])]
    tabs = [{'name': bar.get_nth_page(index).gname, 'page': index}
            for index in range(bar.get_n_pages()) if hasattr(bar.get_nth_page(index), 'gname')]
    plan = revision({'session': support.bridge.session, 'bar': support.bridge.identify(bar),
                     'location': location, 'names': names, 'tabs': tabs, 'current_page': bar.get_current_page()})
    operation = a.get('operation', 'list')
    if operation != 'list':
        if operation not in ('add', 'remove', 'select'):
            raise ValueError('Select list, add, remove or select')
        if a.get('expected_revision') != plan:
            raise ValueError('Gramplet layout changed; list again')
        name = a.get('name')
        if operation == 'add':
            if name in names:
                return {**gramplets(support, {'operation': 'list', 'location': location}, revision),
                        'layout_changed': False, 'operation': operation}
            registered = [p for p in PluginRegister.get_instance().gramplet_plugins() if p.id == name]
            if len(registered) != 1 or not getattr(registered[0], 'supported', True):
                raise ValueError('Select one supported registered Gramplet ID')
            if name not in {item['id'] for item in available}:
                raise ValueError('Gramplet is hidden or incompatible with this view')
            # Native filtering for the active bar/view must also allow this name.
            from gramps.gui.widgets.grampletbar import get_gramplet_options_by_name
            if not get_gramplet_options_by_name(name):
                raise ValueError('No native Gramplet options for this name')
            bar.add_gramplet(name)
            if not bar.has_gramplet(name):
                raise RuntimeError('Native Gramplet constructor failed; inspect plugin diagnostics')
            created = [bar.get_nth_page(index) for index in range(bar.get_n_pages())
                       if getattr(bar.get_nth_page(index), 'gname', None) == name]
            if len(created) != 1 or getattr(created[0], 'pui', None) is None:
                bar.remove_gramplet(name)
                raise RuntimeError('Native Gramplet content failed to load; the new tab was removed')
        elif operation == 'remove':
            if names.count(name) != 1:
                raise ValueError('Select one unambiguous present Gramplet ID')
            bar.remove_gramplet(name)
        else:
            matches = [item['page'] for item in tabs if item['name'] == name]
            if len(matches) != 1:
                raise ValueError('Select one attached Gramplet tab; detached windows use native GTK controls')
            bar.set_current_page(matches[0])
        return {**gramplets(support, {'operation': 'list', 'location': location}, revision),
                'layout_changed': True, 'operation': operation}
    return {'location': location, 'names': names, 'tabs': tabs, 'available': available, 'current_page': bar.get_current_page(),
            'revision': plan, 'records_changed': False,
            'constructor_notice': 'Adding loads installed code and runs its native lifecycle',
            'persistence': 'Native per-view layout lifecycle'}


def plugin_details(p, window):
    from gramps.gui.uimanager import valid_action_name
    from gramps.gui.pluginmanager import GuiPluginManager
    action_name = valid_action_name(p.id)
    action = window.lookup_action(action_name)
    manager = BasePluginManager.get_instance()
    failures = []
    for _filename, info, pdata in manager.get_fail_list():
        if pdata and pdata.id == p.id:
            failures.append({'error_type': info[0].__name__ if info[0] else None, 'message': str(info[1])[:1000]})
    return {'id': p.id, 'name': p.name, 'version': p.version, 'description': p.description,
            'plugin_type': getattr(p, 'ptype', None), 'registered': True, 'loaded': manager.is_loaded(p.id),
            'hidden': p.id in GuiPluginManager.get_instance().get_hidden_plugin_ids(),
            'declared_dependencies': {key: getattr(p, key, None) for key in ('requires_mod', 'requires_gi', 'requires_exe')},
            'load_failures': failures, 'action_name': action_name, 'action_available': action is not None,
            'action_enabled': action.get_enabled() if action else False, 'supported': bool(getattr(p, 'supported', True))}


def visibility(support, a, revision):
    from gramps.gui.pluginmanager import GuiPluginManager
    operation, ident = a.get('operation'), a.get('plugin_id')
    if operation not in ('hide', 'unhide'):
        raise ValueError('Select list, hide or unhide')
    if ident == 'Desktop MCP Control':
        raise ValueError('The controlling desktop bridge cannot be hidden through itself')
    plugin = PluginRegister.get_instance().get_plugin(ident)
    if plugin is None:
        raise ValueError('Select an observed registered plugin ID')
    manager = GuiPluginManager.get_instance()
    hidden = sorted(manager.get_hidden_plugin_ids())
    plan = revision({'session': support.bridge.session, 'id': ident, 'version': plugin.version,
                     'operation': operation, 'hidden_ids': hidden})
    preview = {'plugin_id': ident, 'operation': operation, 'before_hidden': ident in hidden,
               'proposed_hidden': operation == 'hide', 'plan_revision': plan, 'applied': False,
               'scope': 'Gramps profile; native plugin-visibility callbacks', 'records_changed': False}
    if not a.get('apply', False):
        return preview
    if a.get('expected_plan') != plan:
        raise ValueError('Plugin visibility changed; preview again')
    if preview['before_hidden'] != preview['proposed_hidden']:
        getattr(manager, 'hide_plugin' if operation == 'hide' else 'unhide_plugin')(ident)
    return {**preview, 'applied': True, 'hidden': ident in manager.get_hidden_plugin_ids(),
            'save_requested': True}
