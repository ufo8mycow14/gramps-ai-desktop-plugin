"""Native record navigation and explicit sidebar/bottombar Gramplet control."""

# ------------------------
# Python modules
# ------------------------
from typing import Any, Callable
import logging

# ------------------------
# Gramps modules
# ------------------------
from gramps.gen.plug import PluginRegister, BasePluginManager
from gramps.gen.const import GRAMPS_LOCALE as glocale

_ = glocale.translation.gettext
LOG = logging.getLogger(__name__)

NAV_TYPES = {
    "person": "Person",
    "family": "Family",
    "event": "Event",
    "place": "Place",
    "source": "Source",
    "citation": "Citation",
    "repository": "Repository",
    "media": "Media",
    "note": "Note",
}


def navigation(support: Any, a: dict[str, Any]) -> dict[str, Any]:
    """Use verified native record history.

    :param support: Current desktop service.
    :param a: History operation arguments.
    :returns: Native history state.
    """
    kind = a.get("kind", "person")
    if kind not in NAV_TYPES:
        raise ValueError("Select one of the nine native navigation kinds")
    group = a.get("nav_group", 0)
    if type(group) is not int or not 0 <= group <= 100:
        raise ValueError("nav_group must be an observed group number from 0–100")
    ui = support.bridge.uistate
    history = ui.get_history(NAV_TYPES[kind], group)
    if history is None:
        raise ValueError(
            "This navigation kind/group is not registered by the current view"
        )
    operation = a.get("operation", "get")
    if operation == "activate":
        record = support.get(kind, a.get("handle"), a.get("gramps_id"))
        ui.set_active(record.handle, NAV_TYPES[kind], group)
    elif operation in ("back", "forward"):
        index = history.index + (-1 if operation == "back" else 1)
        if not 0 <= index < len(history.history):
            raise ValueError("No native history entry in that direction")
        # Reject stale/deleted history entries before native callbacks run.
        support.get(kind, handle=history.history[index])
        getattr(history, operation)()
    elif operation != "get":
        raise ValueError("Select get, activate, back or forward")
    return {
        "kind": kind,
        "nav_group": group,
        "active_handle": ui.get_active(NAV_TYPES[kind], group),
        "history": list(history.history)[-200:],
        "history_total": len(history.history),
        "index": history.index,
        "can_back": history.index > 0,
        "can_forward": history.index + 1 < len(history.history),
        "records_changed": False,
    }


def gramplets(support: Any, a: dict[str, Any], revision: Callable) -> dict[str, Any]:
    """Control the observed native Gramplet location.

    :param support: Current desktop service.
    :param a: Layout arguments.
    :param revision: Revision digest helper.
    :returns: Current native layout.
    """
    from gramps.gui.widgets.grampletbar import GrampletBar
    from gramps.gui.widgets.grampletpane import GET_GRAMPLET_LIST

    page = support.bridge.uistate.viewmanager.active_page
    location = a.get("location", "sidebar")
    if location == "dashboard":
        return dashboard(support, a, revision)
    if location not in ("sidebar", "bottombar"):
        raise ValueError("Select sidebar or bottombar on the active view")
    bar = getattr(page, location, None)
    if not isinstance(bar, GrampletBar):
        raise ValueError(
            "The active view has no supported GrampletBar at this location"
        )
    current = list(bar.all_gramplets())
    names = [name if isinstance(name, str) else name.gname for name in current]
    available = [
        {"label": label, "id": ident}
        for label, ident in GET_GRAMPLET_LIST(bar.pageview.navigation_type(), [])
    ]
    tabs = [
        {"name": bar.get_nth_page(index).gname, "page": index}
        for index in range(bar.get_n_pages())
        if hasattr(bar.get_nth_page(index), "gname")
    ]
    plan = revision(
        {
            "session": support.bridge.session,
            "bar": support.bridge.identify(bar),
            "location": location,
            "names": names,
            "tabs": tabs,
            "current_page": bar.get_current_page(),
        }
    )
    operation = a.get("operation", "list")
    if operation != "list":
        if operation not in ("add", "remove", "select"):
            raise ValueError("Select list, add, remove or select")
        if a.get("expected_revision") != plan:
            raise ValueError("Gramplet layout changed; list again")
        name = a.get("name")
        if operation == "add":
            if name in names:
                return {
                    **gramplets(
                        support, {"operation": "list", "location": location}, revision
                    ),
                    "layout_changed": False,
                    "operation": operation,
                }
            registered = [
                p
                for p in PluginRegister.get_instance().gramplet_plugins()
                if p.id == name
            ]
            if len(registered) != 1 or not getattr(registered[0], "supported", True):
                raise ValueError("Select one supported registered Gramplet ID")
            if name not in {item["id"] for item in available}:
                raise ValueError("Gramplet is hidden or incompatible with this view")
            # Native filtering for the active bar/view must also allow this name.
            from gramps.gui.widgets.grampletbar import get_gramplet_options_by_name

            if not get_gramplet_options_by_name(name):
                raise ValueError("No native Gramplet options for this name")
            bar.add_gramplet(name)
            if not bar.has_gramplet(name):
                raise RuntimeError(
                    "Native Gramplet constructor failed; inspect plugin diagnostics"
                )
            created = [
                bar.get_nth_page(index)
                for index in range(bar.get_n_pages())
                if getattr(bar.get_nth_page(index), "gname", None) == name
            ]
            if len(created) != 1 or getattr(created[0], "pui", None) is None:
                bar.remove_gramplet(name)
                raise RuntimeError(
                    "Native Gramplet content failed to load; the new tab was removed"
                )
        elif operation == "remove":
            if names.count(name) != 1:
                raise ValueError("Select one unambiguous present Gramplet ID")
            bar.remove_gramplet(name)
        else:
            matches = [item["page"] for item in tabs if item["name"] == name]
            if len(matches) != 1:
                raise ValueError(
                    "Select one attached Gramplet tab; detached windows use native GTK controls"
                )
            bar.set_current_page(matches[0])
        return {
            **gramplets(support, {"operation": "list", "location": location}, revision),
            "layout_changed": True,
            "operation": operation,
        }
    return {
        "location": location,
        "names": names,
        "tabs": tabs,
        "available": available,
        "current_page": bar.get_current_page(),
        "revision": plan,
        "records_changed": False,
        "constructor_notice": "Adding loads installed code and runs its native lifecycle",
        "persistence": "Native per-view layout lifecycle",
    }


def dashboard(support: Any, a: dict[str, Any], revision: Callable) -> dict[str, Any]:
    """Control observed Dashboard instances through their native layout lifecycle.

    :param support: The current desktop service.
    :param a: Layout operation and observed instance arguments.
    :param revision: Revision digest helper.
    :returns: Current layout and any applied operation result.
    """
    from gramps.gui.widgets.grampletpane import (
        GrampletPane,
        GridGramplet,
        GET_GRAMPLET_LIST,
        get_gramplet_options_by_name,
        PLUGMAN,
    )

    page = support.bridge.uistate.viewmanager.active_page
    pane = getattr(page, "widget", None)
    if not isinstance(pane, GrampletPane) or page.navigation_type() != "Dashboard":
        raise ValueError(_("Activate the native Dashboard view first"))
    instances = [g for g in pane.gramplet_map.values() if g is not None]
    positions = {
        id(frame): (col, row)
        for col, box in enumerate(pane.columns)
        for row, frame in enumerate(box.get_children())
    }
    items = [
        {
            "instance_id": support.bridge.identify(g.mainframe),
            "name": g.gname,
            "title": g.title,
            "state": g.gstate,
            "column": positions.get(id(g.mainframe), (g.column, g.row))[0],
            "row": positions.get(id(g.mainframe), (g.column, g.row))[1],
            "content_loaded": g.pui is not None,
        }
        for g in instances
    ]
    available = [
        {"label": label, "id": ident}
        for label, ident in GET_GRAMPLET_LIST("Dashboard", [])
    ]
    pending = [opts["title"] for opts in pane.closed_opts]
    layout_revision = revision(
        {
            "session": support.bridge.session,
            "pane": support.bridge.identify(pane),
            "columns": pane.column_count,
            "items": items,
            "pending_closed": pending,
        }
    )
    operation = a.get("operation", "list")
    if operation == "list":
        return {
            "location": "dashboard",
            "items": items,
            "pending_closed": pending,
            "available": available,
            "columns": pane.column_count,
            "revision": layout_revision,
            "records_changed": False,
            "persistence": "Native Dashboard profile lifecycle",
            "constructor_notice": "Adding/restoring loads installed code and runs its native lifecycle",
        }
    if operation not in ("add", "remove", "restore", "move", "state", "columns"):
        raise ValueError(
            _(
                "Select list, add, remove, restore, move, state or columns for the Dashboard"
            )
        )
    if a.get("expected_revision") != layout_revision:
        raise ValueError(_("The Dashboard layout changed; list it again"))
    before = layout_revision
    if operation == "add":
        ident = a.get("name")
        plugins = [
            p for p in PluginRegister.get_instance().gramplet_plugins() if p.id == ident
        ]
        if (
            len(plugins) != 1
            or not getattr(plugins[0], "supported", True)
            or ident not in {item["id"] for item in available}
        ):
            raise ValueError(_("Select one available supported Dashboard Gramplet ID"))
        opts = get_gramplet_options_by_name(ident)
        if not opts:
            raise ValueError(_("Native Gramplet options are unavailable"))
        title = opts["title"]
        suffix = 1
        while title in pane.gramplet_map:
            title = opts["title"] + "-" + str(suffix)
            suffix += 1
        opts["title"] = title
        # Retain the GUI before user content runs so failures can be cleaned up.
        created = GridGramplet(pane, pane.dbstate, pane.uistate, **opts)
        try:
            module = PLUGMAN.load_plugin(PLUGMAN.get_plugin(ident))
            if module is None or not opts.get("content"):
                raise RuntimeError(_("Dashboard Gramplet content failed to load"))
            getattr(module, opts["content"])(created)
            if created.pui is None:
                raise RuntimeError(_("Dashboard Gramplet content failed to initialise"))
            pane.gramplet_map[title] = created
            pane.frame_map[str(created.mainframe)] = created
            pane.columns[0].pack_start(created.mainframe, created.expand, True, 0)
            created.column, created.row = 0, len(pane.columns[0].get_children()) - 1
            created.scrolledwindow.set_size_request(-1, created.height)
            created.set_state("maximized")
            created.mainframe.show_all()
            created.pui.active = True
            created.pui.update()
        except Exception:
            discard_dashboard(pane, created, title)
            raise
    elif operation == "columns":
        columns = a.get("columns")
        if type(columns) is not int or not 1 <= columns <= 10:
            raise ValueError(_("Dashboard columns must be an integer from 1–10"))
        if columns != pane.column_count:
            pane.set_columns(columns)
    else:
        matches = [
            g
            for g in instances
            if support.bridge.identify(g.mainframe) == a.get("instance_id")
        ]
        if len(matches) != 1:
            raise ValueError(_("Select one observed Dashboard instance ID"))
        selected = matches[0]
        if operation == "restore":
            if selected.gstate != "closed" or selected not in pane.closed_gramplets:
                raise ValueError(_("Select a Gramplet closed in this session"))
            pane.restore_gramplet(selected.title)
        else:
            if selected.gstate not in ("minimized", "maximized"):
                raise ValueError(
                    _(
                        "Detached or closed Gramplets need their native window or restore control"
                    )
                )
            if operation == "remove":
                selected.close()
            elif operation == "state":
                state = a.get("state")
                if state not in ("minimized", "maximized"):
                    raise ValueError(_("Select minimized or maximized"))
                if selected.gstate != state:
                    selected.set_state(state)
            else:
                column, row = a.get("column"), a.get("row")
                if type(column) is not int or not 0 <= column < pane.column_count:
                    raise ValueError(_("Select an observed Dashboard column"))
                target = pane.columns[column]
                others = [
                    frame
                    for frame in target.get_children()
                    if frame is not selected.mainframe
                ]
                if type(row) is not int or not 0 <= row <= len(others):
                    raise ValueError(
                        _("Select an insertion row within the target column")
                    )
                if positions.get(id(selected.mainframe)) != (column, row):
                    selected.mainframe.get_parent().remove(selected.mainframe)
                    target.pack_start(selected.mainframe, selected.expand, True, 0)
                    target.reorder_child(selected.mainframe, row)
                    for col, box in enumerate(pane.columns):
                        for index, frame in enumerate(box.get_children()):
                            g = pane.frame_map[str(frame)]
                            g.column, g.row = col, index
                    selected.set_state(selected.gstate)
    for col, box in enumerate(pane.columns):
        for index, frame in enumerate(box.get_children()):
            gramplet = pane.frame_map[str(frame)]
            gramplet.column, gramplet.row = col, index
    result = dashboard(support, {"operation": "list"}, revision)
    return {
        **result,
        "operation": operation,
        "layout_changed": result["revision"] != before,
    }


def discard_dashboard(pane: Any, created: Any, title: str) -> None:
    """Remove failed native content and its owned callback/idle resources.

    :param pane: Dashboard containing the failed instance.
    :param created: GUI retained before the plugin constructor runs.
    :param title: Unique instance title.
    """
    content = created.pui
    if content is not None:
        for method in ("interrupt", "disconnect_all"):
            try:
                getattr(content, method)()
            except Exception as error:
                LOG.warning("Failed Gramplet %s cleanup: %s", method, error)
        # Gramps' base constructor connects these directly, outside _signal.
        for state in (pane.dbstate, pane.uistate):
            callbacks = getattr(state, "_Callback__callback_map", {})
            keys = [
                key
                for values in callbacks.values()
                for key, callback in values
                if getattr(callback, "__self__", None) is content
            ]
            for key in keys:
                state.disconnect(key)
    parent = created.mainframe.get_parent()
    if parent is not None:
        parent.remove(created.mainframe)
    pane.gramplet_map.pop(title, None)
    pane.frame_map.pop(str(created.mainframe), None)
    created.mainframe.destroy()


def plugin_details(p: Any, window: Any) -> dict[str, Any]:
    """Describe plugin registration and recorded failures.

    :param p: Registered native plugin.
    :param window: Native application window.
    :returns: Registry diagnostics.
    """
    from gramps.gui.uimanager import valid_action_name
    from gramps.gui.pluginmanager import GuiPluginManager

    action_name = valid_action_name(p.id)
    action = window.lookup_action(action_name)
    manager = BasePluginManager.get_instance()
    failures = []
    for _filename, info, pdata in manager.get_fail_list():
        if pdata and pdata.id == p.id:
            failures.append(
                {
                    "error_type": info[0].__name__ if info[0] else None,
                    "message": str(info[1])[:1000],
                }
            )
    return {
        "id": p.id,
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "plugin_type": getattr(p, "ptype", None),
        "registered": True,
        "loaded": manager.is_loaded(p.id),
        "hidden": p.id in GuiPluginManager.get_instance().get_hidden_plugin_ids(),
        "declared_dependencies": {
            key: getattr(p, key, None)
            for key in ("requires_mod", "requires_gi", "requires_exe")
        },
        "load_failures": failures,
        "action_name": action_name,
        "action_available": action is not None,
        "action_enabled": action.get_enabled() if action else False,
        "supported": bool(getattr(p, "supported", True)),
    }


def visibility(support: Any, a: dict[str, Any], revision: Callable) -> dict[str, Any]:
    """Preview/apply one revision-bound native visibility change.

    :param support: Current desktop service.
    :param a: Visibility arguments.
    :param revision: Revision digest helper.
    :returns: Reviewed visibility state.
    """
    from gramps.gui.pluginmanager import GuiPluginManager

    operation, ident = a.get("operation"), a.get("plugin_id")
    if operation not in ("hide", "unhide"):
        raise ValueError("Select list, hide or unhide")
    if ident == "Desktop MCP Control":
        raise ValueError(
            "The controlling desktop bridge cannot be hidden through itself"
        )
    plugin = PluginRegister.get_instance().get_plugin(ident)
    if plugin is None:
        raise ValueError("Select an observed registered plugin ID")
    manager = GuiPluginManager.get_instance()
    hidden = sorted(manager.get_hidden_plugin_ids())
    plan = revision(
        {
            "session": support.bridge.session,
            "id": ident,
            "version": plugin.version,
            "operation": operation,
            "hidden_ids": hidden,
        }
    )
    preview = {
        "plugin_id": ident,
        "operation": operation,
        "before_hidden": ident in hidden,
        "proposed_hidden": operation == "hide",
        "plan_revision": plan,
        "applied": False,
        "scope": "Gramps profile; native plugin-visibility callbacks",
        "records_changed": False,
    }
    if not a.get("apply", False):
        return preview
    if a.get("expected_plan") != plan:
        raise ValueError("Plugin visibility changed; preview again")
    if preview["before_hidden"] != preview["proposed_hidden"]:
        getattr(manager, "hide_plugin" if operation == "hide" else "unhide_plugin")(
            ident
        )
    return {
        **preview,
        "applied": True,
        "hidden": ident in manager.get_hidden_plugin_ids(),
        "save_requested": True,
    }
