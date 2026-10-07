"""Checks run only on the caller's isolated memory database and temporary files."""

import copy
from pathlib import Path
import tarfile
import xml.etree.ElementTree as ET


def run(service, folder):
    from gramps.gen.db import DbTxn
    from gramps.gen.lib import ChildRef, Name, Surname, PersonRef, EventRef, EventType

    db, checks = service.db, []

    def check(name, value):
        assert value, name
        checks.append(name)

    def reject(name, action):
        try:
            action()
        except (ValueError, RuntimeError):
            checks.append(name)
        else:
            raise AssertionError(name)

    def call(method, **args):
        return service.dispatch(method, args)

    def create(kind):
        return call("mutate", operation="create", kind=kind, apply=True)["after"][0][
            "handle"
        ]

    def revision(kind, handle):
        return call("object", kind=kind, handle=handle)["revision"]

    def join(family, person, role, relation="Birth"):
        return call(
            "family_member",
            family_handle=family,
            person_handle=person,
            role=role,
            father_relation=relation,
            mother_relation=relation,
            family_revision=revision("family", family),
            person_revision=revision("person", person),
            apply=True,
        )

    from gramps.gen.datehandler import displayer
    from gramps.gen.lib import Date

    dates = call("date", operation="options")
    check(
        "date_native_option_discovery",
        len(dates["calendars"]) == 7 and dates["formats"],
    )
    legacy = call("date", text="1900")
    check(
        "date_legacy_parse_contract",
        set(legacy) == {"data", "display", "valid"}
        and legacy["data"]["dateval"][0:2] == [0, 0],
    )
    original_format = displayer.format
    formatted = call("date", operation="format", text="1900", format_index=0)
    check(
        "date_format_retains_precision_and_global_format",
        formatted["precision"]["start"] == {"year": True, "month": False, "day": False}
        and displayer.format == original_format,
    )
    text_date = call("date", operation="format", text="undated manuscript")
    check(
        "date_text_preserved",
        text_date["text_only"] and text_date["data"]["text"] == "undated manuscript",
    )
    compared = call(
        "date",
        operation="compare",
        text="between 1900 and 1910",
        other_text="between 1900 and 1920",
        comparison="identity",
    )
    check("date_range_ends_not_conflated", not compared["matched"])
    converted = call(
        "date", operation="calendar", text="1 January 1900", calendar="Julian"
    )
    check(
        "date_julian_conversion_known_day",
        converted["result"]["data"]["dateval"][:3] == [20, 12, 1899],
    )
    roundtrip = call(
        "date",
        operation="calendar",
        text=converted["result"]["display"],
        calendar="Gregorian",
    )
    check(
        "date_calendar_roundtrip",
        roundtrip["result"]["data"]["dateval"][:3] == [1, 1, 1900],
    )
    offset = call("date", operation="offset", text="28 February 2000", days=1)
    check(
        "date_offset_crosses_leap_day",
        offset["result"]["data"]["dateval"][:3] == [29, 2, 2000],
    )
    for text in (
        "1900",
        "about 1 January 1900",
        "1 January 1700/1",
        "undated manuscript",
    ):
        reject(
            "date_rejects_precision_changing_transform_" + text,
            lambda text=text: call(
                "date", operation="calendar", text=text, calendar="Julian"
            ),
        )
    check(
        "date_native_interval_match",
        call("date", operation="compare", text="1900", other_text="1 June 1900")[
            "matched"
        ],
    )
    from types import SimpleNamespace
    from gramps.gen.utils.configmanager import ConfigManager
    import preference_support
    from support import revision as digest

    preference_manager = ConfigManager(str(folder / "synthetic-preferences.ini"))
    preference_manager.register("test.first", 1)
    preference_manager.register("test.second", False)
    prefs = preference_support.Preferences(
        SimpleNamespace(bridge=SimpleNamespace(session="synthetic-preferences")),
        digest,
        preference_manager,
    )
    args = {"operation": "update", "changes": {"test.first": 2, "test.second": True}}
    preview = prefs.dispatch(args)
    check(
        "preferences_preview_defaults_without_effect",
        preference_manager.get("test.first") == 1
        and prefs.dispatch({"operation": "get", "key": "test.first"})["default"] == 1
        and not preview["atomic"],
    )
    reject("preferences_plan_required", lambda: prefs.dispatch({**args, "apply": True}))
    saved = prefs.dispatch(
        {**args, "apply": True, "expected_plan": preview["plan_revision"]}
    )
    check(
        "preferences_native_save_and_receipt",
        preference_manager.get("test.first") == 2
        and (folder / "synthetic-preferences.ini").is_file()
        and saved["receipt_id"],
    )
    rollback = prefs.dispatch(
        {"operation": "rollback", "receipt_id": saved["receipt_id"]}
    )
    prefs.dispatch(
        {
            "operation": "rollback",
            "receipt_id": saved["receipt_id"],
            "apply": True,
            "expected_plan": rollback["plan_revision"],
        }
    )
    check(
        "preferences_guarded_restoration",
        preference_manager.get("test.first") == 1
        and not preference_manager.get("test.second"),
    )
    preview = prefs.dispatch(args)
    preference_manager.set("test.first", 3)
    reject(
        "preferences_stale_plan_preserves_manual_change",
        lambda: prefs.dispatch(
            {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        ),
    )
    preference_manager.set("test.first", 1)
    setter = preference_manager.set

    def failing_set(key, value):
        setter(key, value)
        if key == "test.second" and value is True:
            raise RuntimeError("Synthetic preference callback failure")

    preference_manager.set = failing_set
    preview = prefs.dispatch(args)
    reject(
        "preferences_callback_failure_reported",
        lambda: prefs.dispatch(
            {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        ),
    )
    check(
        "preferences_callback_compensates_applied_values",
        preference_manager.get("test.first") == 1
        and not preference_manager.get("test.second"),
    )
    preference_manager.set = setter
    reject(
        "preferences_type_mismatch_rejected",
        lambda: prefs.dispatch(
            {"operation": "update", "changes": {"test.first": True}}
        ),
    )

    def dependent_callback(*unused):
        if preference_manager.get("test.first") == 2:
            preference_manager.set("test.second", True)

    callback = preference_manager.connect("test.first", dependent_callback)
    preview = prefs.dispatch(args)
    reject(
        "preferences_callback_changes_unwritten_key_rejected",
        lambda: prefs.dispatch(
            {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        ),
    )
    check(
        "preferences_restores_full_planned_snapshot",
        preference_manager.get("test.first") == 1
        and not preference_manager.get("test.second"),
    )
    preference_manager.disconnect(callback)
    preference_manager.register("test.list", [1])

    def list_callback(*unused):
        if preference_manager.get("test.list") == [2]:
            preference_manager.get("test.list").append(3)

    callback = preference_manager.connect("test.list", list_callback)
    list_args = {"operation": "update", "changes": {"test.list": [2]}}
    preview = prefs.dispatch(list_args)
    reject(
        "preferences_callback_cannot_mutate_reviewed_proposal",
        lambda: prefs.dispatch(
            {**list_args, "apply": True, "expected_plan": preview["plan_revision"]}
        ),
    )
    check(
        "preferences_list_restoration_and_caller_preserved",
        preference_manager.get("test.list") == [1]
        and list_args["changes"]["test.list"] == [2],
    )
    preference_manager.disconnect(callback)
    schema = call("schema", kind="person")
    check(
        "schema_recursive_native_type_choices",
        "json_schema" in schema
        and "NameType" in schema["type_choices"]
        and schema["json_schema"]["properties"]["event_ref_list"]["items"][
            "properties"
        ]["role"],
    )
    schema = call("schema", class_name="ChildRef")
    check(
        "schema_embedded_class_template",
        schema["template"]["_class"] == "ChildRef"
        and "ChildRefType" in schema["type_choices"],
    )
    root, parent, grand, adopter, sibling, twin, outsider = [
        create("person") for _ in range(7)
    ]
    family, older, adoption = [create("family") for _ in range(3)]
    for fam, person, role, relation in (
        (family, parent, "father", "Birth"),
        (family, root, "child", "Birth"),
        (family, sibling, "child", "Birth"),
        (older, grand, "father", "Birth"),
        (older, parent, "child", "Birth"),
        (adoption, adopter, "father", "Birth"),
        (adoption, root, "child", "Adopted"),
    ):
        join(fam, person, role, relation)
    selected = call(
        "secondary",
        kind="family",
        handle=family,
        operation="get",
        path=["child_ref_list", 0],
    )
    edit_args = {
        "kind": "family",
        "handle": family,
        "operation": "update",
        "path": ["child_ref_list", 0],
        "expected_revision": selected["owner_revision"],
        "expected_secondary_revision": selected["revision"],
        "patch": {"private": True},
    }
    before = revision("family", family)
    call("secondary", **edit_args)
    check("secondary_preview_preserves_owner", revision("family", family) == before)
    call("secondary", **edit_args, apply=True)
    check(
        "secondary_changes_only_selected_child_ref",
        db.get_family_from_handle(family).get_child_ref_list()[0].get_privacy()
        and not db.get_family_from_handle(family).get_child_ref_list()[1].get_privacy(),
    )
    reject(
        "secondary_stale_owner_rejected",
        lambda: call("secondary", **edit_args, apply=True),
    )
    selected = call(
        "secondary",
        kind="family",
        handle=family,
        operation="get",
        path=["child_ref_list", 0],
    )
    edit_args.update(
        expected_revision=selected["owner_revision"],
        expected_secondary_revision=selected["revision"],
    )
    reject(
        "secondary_link_identity_preserved",
        lambda: call(
            "secondary", **{**edit_args, "patch": {"ref": sibling}}, apply=True
        ),
    )
    embedded_note = create("note")
    attachment = {
        **edit_args,
        "operation": "attach",
        "target_kind": "note",
        "target_handle": embedded_note,
        "target_revision": revision("note", embedded_note),
    }
    attachment.pop("patch")
    call("secondary", **attachment, apply=True)
    check(
        "secondary_native_nested_note_attachment",
        db.get_family_from_handle(family).get_child_ref_list()[0].get_note_list()
        == [embedded_note],
    )
    check(
        "secondary_list_returns_unambiguous_paths",
        any(
            item["path"] == ["child_ref_list", 1]
            for item in call(
                "secondary", kind="family", handle=family, operation="list"
            )["items"]
        ),
    )
    selected = call(
        "secondary", kind="person", handle=root, operation="get", path=["primary_name"]
    )
    args = {
        "kind": "person",
        "handle": root,
        "operation": "update",
        "path": ["primary_name"],
        "expected_revision": selected["owner_revision"],
        "expected_secondary_revision": selected["revision"],
        "patch": {"date": {"dateval": [1, 1, 1900, False]}},
    }
    call("secondary", **args, apply=True)
    check(
        "secondary_nested_date_recomputes_native_sort_value",
        db.get_person_from_handle(root)
        .get_primary_name()
        .get_date_object()
        .get_sort_value()
        == Date(1900, 1, 1).get_sort_value(),
    )
    selected = call(
        "secondary", kind="person", handle=root, operation="get", path=["primary_name"]
    )
    args.update(
        expected_revision=selected["owner_revision"],
        expected_secondary_revision=selected["revision"],
        patch={"date": {"sortval": 9}},
    )
    reject(
        "secondary_nested_derived_identity_rejected",
        lambda: call("secondary", **args, apply=True),
    )
    before = revision("person", root)
    for label, patch in (
        ("date_extra_components", {"date": {"dateval": [1, 1, 1900, False, 999]}}),
        ("wrong_nested_class", {"surname_list": [call("date", text="1900")["data"]]}),
        ("direct_citation_patch", {"citation_list": ["missing-citation"]}),
        ("invalid_field_type", {"first_name": 123}),
    ):
        reject(
            "secondary_rejects_" + label,
            lambda patch=patch: call(
                "secondary", **{**args, "patch": patch}, apply=True
            ),
        )
    check(
        "secondary_bad_shapes_preserve_readable_owner",
        revision("person", root) == before
        and db.get_person_from_handle(root).serialize(),
    )
    import navigation_support
    from gramps.gui.displaystate import History

    nav_state = SimpleNamespace(
        db=db, connect=lambda *args: None, is_open=lambda: False
    )
    history = History(nav_state, "Person")
    history.clear()
    nav_ui = SimpleNamespace(
        get_history=lambda kind, group: (
            history if kind == "Person" and group == 0 else None
        ),
        get_active=lambda kind, group: history.present(),
        set_active=lambda handle, kind, group: history.push(handle),
    )
    nav_support = SimpleNamespace(
        bridge=SimpleNamespace(uistate=nav_ui), get=service.get
    )
    navigation_support.navigation(
        nav_support, {"operation": "activate", "handle": root}
    )
    navigation_support.navigation(
        nav_support, {"operation": "activate", "handle": parent}
    )
    check("navigation_native_handle_activation", history.present() == parent)
    navigation_support.navigation(nav_support, {"operation": "back"})
    check(
        "navigation_native_back_forward",
        history.present() == root
        and navigation_support.navigation(nav_support, {"operation": "forward"})[
            "active_handle"
        ]
        == parent,
    )
    reject(
        "navigation_forward_boundary_preserves_index",
        lambda: navigation_support.navigation(nav_support, {"operation": "forward"}),
    )
    reject(
        "navigation_unregistered_group_rejected",
        lambda: navigation_support.navigation(nav_support, {"nav_group": 1}),
    )
    from gi.repository import Gtk
    from gramps.gen.plug import BasePluginManager
    from gramps.gui.widgets.grampletbar import GrampletBar

    fixture_code = "from gramps.gen.plug import Gramplet\nclass SyntheticGramplet(Gramplet):\n    def init(self):\n        self.append_text('Synthetic fixture')\n"
    (folder / "synthetic_gramplet.py").write_text(fixture_code, encoding="utf-8")
    (folder / "synthetic_gramplet.gpr.py").write_text(
        "register(GRAMPLET, id='synthetic-gramplet', name='Synthetic Gramplet', description='Synthetic only', version='1.0', gramps_target_version='6.1', status=STABLE, fname='synthetic_gramplet.py', gramplet='SyntheticGramplet', gramplet_title='Synthetic Gramplet', navtypes=['Person'])",
        encoding="utf-8",
    )
    g_ui = SimpleNamespace(
        connect=lambda *args: None,
        emit=lambda *args: None,
        get_active=lambda *args: None,
        register=lambda *args: None,
        get_history=lambda *args: history,
        window=Gtk.ApplicationWindow(),
    )
    g_page = SimpleNamespace(navigation_type=lambda: "Person", active=True)
    manager = BasePluginManager.get_instance()
    manager.reg_plugins(str(folder), nav_state, g_ui, load_on_reg=False)
    bar = GrampletBar(nav_state, g_ui, g_page, "synthetic-expansion-bar", [])
    g_page.sidebar = bar
    g_ui.viewmanager = SimpleNamespace(active_page=g_page)
    g_bridge = SimpleNamespace(
        uistate=g_ui,
        session="synthetic-gramplet",
        identify=lambda widget: "synthetic-bar",
    )
    g_support = SimpleNamespace(bridge=g_bridge)
    listing = navigation_support.gramplets(g_support, {}, digest)
    check(
        "gramplet_native_bar_inventory_and_eligible_ids",
        not listing["names"]
        and any(item["id"] == "synthetic-gramplet" for item in listing["available"]),
    )
    reject(
        "gramplet_layout_revision_required",
        lambda: navigation_support.gramplets(
            g_support, {"operation": "add", "name": "synthetic-gramplet"}, digest
        ),
    )
    listing = navigation_support.gramplets(
        g_support,
        {
            "operation": "add",
            "name": "synthetic-gramplet",
            "expected_revision": listing["revision"],
        },
        digest,
    )
    check(
        "gramplet_native_constructor_and_tab_readback",
        listing["names"] == ["synthetic-gramplet"]
        and bar.get_nth_page(0).pui.__class__.__name__ == "SyntheticGramplet",
    )
    selected_bar = navigation_support.gramplets(
        g_support,
        {
            "operation": "select",
            "name": "synthetic-gramplet",
            "expected_revision": listing["revision"],
        },
        digest,
    )
    check("gramplet_native_tab_selection", selected_bar["current_page"] == 0)
    listing = navigation_support.gramplets(
        g_support,
        {
            "operation": "remove",
            "name": "synthetic-gramplet",
            "expected_revision": selected_bar["revision"],
        },
        digest,
    )
    check("gramplet_native_remove", not listing["names"])
    from gramps.gui.widgets.grampletbar import TabGramplet

    detached = TabGramplet.__new__(TabGramplet)
    Gtk.ScrolledWindow.__init__(detached)
    detached.gname = "synthetic-gramplet"
    bar.detached_gramplets.append(detached)
    listing = navigation_support.gramplets(g_support, {}, digest)
    added = navigation_support.gramplets(
        g_support,
        {
            "operation": "add",
            "name": "synthetic-gramplet",
            "expected_revision": listing["revision"],
        },
        digest,
    )
    check(
        "gramplet_detached_only_duplicate_prevented",
        not added["layout_changed"] and added["names"] == ["synthetic-gramplet"],
    )
    bar.detached_gramplets.clear()
    detached.destroy()
    loader = manager.load_plugin
    manager.load_plugin = lambda plugin: (
        None if plugin.id == "synthetic-gramplet" else loader(plugin)
    )
    listing = navigation_support.gramplets(g_support, {}, digest)
    try:
        reject(
            "gramplet_failed_content_load_reported",
            lambda: navigation_support.gramplets(
                g_support,
                {
                    "operation": "add",
                    "name": "synthetic-gramplet",
                    "expected_revision": listing["revision"],
                },
                digest,
            ),
        )
        check(
            "gramplet_failed_new_tab_removed",
            not navigation_support.gramplets(g_support, {}, digest)["names"],
        )
    finally:
        manager.load_plugin = loader
    plugin = (
        __import__("gramps.gen.plug", fromlist=["PluginRegister"])
        .PluginRegister.get_instance()
        .get_plugin("synthetic-gramplet")
    )
    details = navigation_support.plugin_details(plugin, g_ui.window)
    check(
        "plugin_diagnostics_separate_loaded_from_action",
        details["registered"]
        and details["loaded"]
        and not details["action_available"]
        and "declared_dependencies" in details,
    )
    hide = navigation_support.visibility(
        g_support, {"operation": "hide", "plugin_id": plugin.id}, digest
    )
    reject(
        "plugin_visibility_requires_preview",
        lambda: navigation_support.visibility(
            g_support,
            {"operation": "hide", "plugin_id": plugin.id, "apply": True},
            digest,
        ),
    )
    hidden = navigation_support.visibility(
        g_support,
        {
            "operation": "hide",
            "plugin_id": plugin.id,
            "apply": True,
            "expected_plan": hide["plan_revision"],
        },
        digest,
    )
    check("plugin_visibility_native_hidden_readback", hidden["hidden"])
    unhide = navigation_support.visibility(
        g_support, {"operation": "unhide", "plugin_id": plugin.id}, digest
    )
    navigation_support.visibility(
        g_support,
        {
            "operation": "unhide",
            "plugin_id": plugin.id,
            "apply": True,
            "expected_plan": unhide["plan_revision"],
        },
        digest,
    )
    reject(
        "plugin_controlling_bridge_visibility_protected",
        lambda: navigation_support.visibility(
            g_support, {"operation": "hide", "plugin_id": "Desktop MCP Control"}, digest
        ),
    )
    bar.destroy()
    g_ui.window.destroy()
    graph = call("graph", operation="ancestors", handle=root)
    check(
        "graph_all_parent_families_and_adoption",
        {n["handle"] for n in graph["nodes"]} == {root, parent, grand, adopter}
        and {e["relation"]["code"] for e in graph["edges"]} == {1, 2}
        and not graph["truncated"],
    )
    check(
        "graph_explicit_relation_scope",
        adopter
        not in {
            n["handle"]
            for n in call(
                "graph", operation="ancestors", handle=root, relation_codes=[1]
            )["nodes"]
        },
    )
    check(
        "graph_recorded_descendants",
        {
            n["handle"]
            for n in call("graph", operation="descendants", handle=grand)["nodes"]
        }
        == {grand, parent, root, sibling},
    )
    common = call(
        "graph", operation="common_ancestors", handle=root, target_handle=sibling
    )
    check(
        "graph_common_ancestors_are_recorded_links",
        {n["handle"] for n in common["common_ancestors"]} == {parent, grand}
        and common["recorded_links_only"],
    )
    common = call(
        "graph",
        operation="common_ancestors",
        handle=root,
        target_handle=sibling,
        relation_codes=[1],
        max_edges=3,
    )
    check(
        "graph_shared_edges_deduplicated_at_budget",
        not common["truncated"]
        and len(common["edges"]) == 3
        and any(set(e["root_handles"]) == {root, sibling} for e in common["edges"]),
    )
    work = call("graph", operation="ancestors", handle=root, max_inspections=1)
    check(
        "graph_family_inspection_budget",
        work["truncated"]
        and "max_inspections" in work["truncation_reasons"]
        and work["inspections"] == 1,
    )
    path = call("graph", operation="path", handle=root, target_handle=sibling)
    check(
        "graph_shortest_recorded_path",
        path["found"]
        and len(path["path"]) == 2
        and path["path"][-1]["to_handle"] == sibling,
    )
    missing = call("graph", operation="path", handle=root, target_handle=outsider)
    check(
        "graph_no_path_search_status",
        not missing["found"] and missing["path_status"] == "no_recorded_path",
    )
    limited = call("graph", operation="ancestors", handle=root, max_nodes=1)
    check(
        "graph_discovery_bounds_are_explicit",
        limited["truncated"]
        and limited["truncation_reasons"] == ["max_nodes"]
        and len(limited["nodes"]) == 1,
    )
    reject(
        "graph_invalid_limit_rejected",
        lambda: call("graph", operation="ancestors", handle=root, max_depth=0),
    )
    check(
        "audit_valid_reciprocal_links",
        not call(
            "audit",
            operation="references",
            records=[
                {"kind": "family", "handle": h} for h in (family, older, adoption)
            ],
        )["issues"],
    )
    with DbTxn("Synthetic candidate names", db) as txn:
        for handle, first, surname in (
            (root, "Alex", "Candidate"),
            (twin, "  alex ", "CANDIDATE"),
            (outsider, "Other", "Name"),
        ):
            person = db.get_person_from_handle(handle)
            name, last = Name(), Surname()
            name.set_first_name(first)
            last.set_surname(surname)
            name.add_surname(last)
            person.set_primary_name(name)
            db.commit_person(person, txn)
    before = revision("person", root)
    duplicates = call(
        "audit",
        operation="duplicates",
        kind="person",
        handle=root,
        candidate_handles=[twin, outsider],
    )
    check(
        "duplicate_candidates_transparent_and_read_only",
        len(duplicates["candidates"]) == 1
        and duplicates["candidates"][0]["record"]["handle"] == twin
        and "name" in duplicates["candidates"][0]["matched_fields"]
        and "birth_date" in duplicates["candidates"][0]["missing_fields"]
        and not duplicates["merges_run"]
        and revision("person", root) == before,
    )
    matching_family = create("family")
    with DbTxn("Synthetic family candidate", db) as txn:
        obj = db.get_family_from_handle(matching_family)
        obj.set_father_handle(parent)
        db.commit_family(obj, txn)
    candidates = call(
        "audit",
        operation="duplicates",
        kind="family",
        handle=family,
        candidate_handles=[matching_family, older],
    )
    check(
        "duplicate_family_recorded_parent_anchor",
        len(candidates["candidates"]) == 1
        and "father_handle" in candidates["candidates"][0]["matched_fields"],
    )
    source, other_source = create("source"), create("source")
    citations = [create("citation") for _ in range(3)]
    with DbTxn("Synthetic citation candidates", db) as txn:
        for handle, target, page in zip(
            citations, (source, source, other_source), ("Page 4", " page 4 ", "Page 4")
        ):
            citation = db.get_citation_from_handle(handle)
            citation.set_reference_handle(target)
            citation.set_page(page)
            db.commit_citation(citation, txn)
    candidates = call(
        "audit",
        operation="duplicates",
        kind="citation",
        handle=citations[0],
        candidate_handles=citations[1:],
    )
    check(
        "duplicate_citation_source_and_page_anchor",
        len(candidates["candidates"]) == 1
        and candidates["candidates"][0]["record"]["handle"] == citations[1]
        and "source_page" in candidates["candidates"][0]["matched_fields"],
    )
    for kind in ("note", "place"):
        first, second = create(kind), create(kind)
        check(
            "duplicate_empty_" + kind + "_has_no_anchor",
            not call(
                "audit",
                operation="duplicates",
                kind=kind,
                handle=first,
                candidate_handles=[second],
            )["candidates"],
        )
    initial = list(db.bookmarks.get())
    tree_args = {
        "operation": "set",
        "field": "bookmarks",
        "kind": "person",
        "value": [root, parent],
    }
    preview = call("tree", **tree_args)
    check("tree_bookmark_preview_no_effect", list(db.bookmarks.get()) == initial)
    reject(
        "tree_current_plan_required",
        lambda: call("tree", **tree_args, apply=True, expected_plan="stale"),
    )
    saved = call(
        "tree", **tree_args, apply=True, expected_plan=preview["plan_revision"]
    )
    check(
        "tree_ordered_bookmarks_and_receipt",
        db.bookmarks.get() == [root, parent]
        and call("tree", operation="receipt", receipt_id=saved["receipt_id"])["before"]
        == initial,
    )
    rollback = call("tree", operation="rollback", receipt_id=saved["receipt_id"])
    call(
        "tree",
        operation="rollback",
        receipt_id=saved["receipt_id"],
        apply=True,
        expected_plan=rollback["plan_revision"],
    )
    check("tree_metadata_guarded_rollback", list(db.bookmarks.get()) == initial)
    home_args = {"operation": "set", "field": "home", "value": root}
    preview = call("tree", **home_args)
    db.set_default_person_handle(parent)
    reject(
        "tree_home_staleness_rejected",
        lambda: call(
            "tree", **home_args, apply=True, expected_plan=preview["plan_revision"]
        ),
    )
    check("tree_home_rejection_preserves_home", db.get_default_handle() == parent)
    preview = call("tree", **home_args)
    call("tree", **home_args, apply=True, expected_plan=preview["plan_revision"])
    check("tree_home_native_readback", db.get_default_handle() == root)
    owner_args = {
        "operation": "set",
        "field": "researcher",
        "value": {
            "name": "PRIVATE RESEARCHER SENTINEL",
            "email": "synthetic@example.invalid",
        },
    }
    preview = call("tree", **owner_args)
    call("tree", **owner_args, apply=True, expected_plan=preview["plan_revision"])
    check(
        "tree_researcher_native_fields",
        call("tree", operation="get", field="researcher")["value"]["email"]
        == "synthetic@example.invalid",
    )
    reject(
        "tree_unknown_researcher_field_rejected",
        lambda: call(
            "tree", operation="set", field="researcher", value={"unsupported": "value"}
        ),
    )
    stale = call("tree", **tree_args)
    call(
        "mutate",
        operation="update",
        kind="person",
        handle=parent,
        expected_revision=revision("person", parent),
        patch={"gender": 1},
        apply=True,
    )
    reject(
        "tree_bookmark_target_revision_guard",
        lambda: call(
            "tree", **tree_args, apply=True, expected_plan=stale["plan_revision"]
        ),
    )
    private_note = create("note")
    with DbTxn("Synthetic private records", db) as txn:
        person = db.get_person_from_handle(adopter)
        person.set_privacy(True)
        db.commit_person(person, txn)
        note = db.get_note_from_handle(private_note)
        note.set("PRIVATE NOTE SENTINEL")
        note.set_privacy(True)
        db.commit_note(note, txn)
    call(
        "attach",
        kind="person",
        handle=root,
        expected_revision=revision("person", root),
        target_kind="note",
        target_handle=private_note,
        apply=True,
    )
    media_path = folder / "selected-source.txt"
    media_path.write_text("Synthetic scoped media", encoding="utf-8")
    media = call(
        "mutate",
        kind="media",
        operation="create",
        patch={"path": str(media_path), "mime": "text/plain"},
        apply=True,
    )["after"][0]["handle"]
    call(
        "attach",
        kind="person",
        handle=root,
        expected_revision=revision("person", root),
        target_kind="media",
        target_handle=media,
        apply=True,
    )
    db.bookmarks.set([root, adopter])
    db.set_default_person_handle(adopter)
    db.set_name_group_mapping("PRIVATE NAMEMAP SENTINEL", "PRIVATE GROUP SENTINEL")
    original_note = revision("note", private_note)
    export_args = {
        "operation": "run",
        "format": "xml",
        "person_handles": [root],
        "exclude_private": True,
        "output_path": str(folder / "selected.xml"),
    }
    selected = call("export", **export_args)
    call("export", **export_args, apply=True, expected_plan=selected["plan_revision"])
    raw = Path(export_args["output_path"]).read_bytes()
    xml = ET.fromstring(raw)
    people = [node for node in xml.iter() if node.tag.endswith("person")]
    check(
        "export_selected_people_private_nested_data",
        len(people) == 1
        and people[0].get("handle") == "_" + root
        and b"PRIVATE NOTE SENTINEL" not in raw,
    )
    check(
        "export_scoped_metadata_isolated",
        b"PRIVATE RESEARCHER SENTINEL" not in raw
        and b"PRIVATE NAMEMAP SENTINEL" not in raw
        and all(
            n.get("hlink") != "_" + adopter
            for n in xml.iter()
            if n.tag.endswith("bookmark")
        ),
    )
    check(
        "export_privacy_preserves_originals",
        revision("note", private_note) == original_note
        and db.bookmarks.get() == [root, adopter]
        and db.get_default_handle() == adopter
        and db.get_researcher().get_name() == "PRIVATE RESEARCHER SENTINEL",
    )
    original_base = db.get_mediapath()
    with DbTxn("Synthetic relative media", db) as txn:
        obj = db.get_media_from_handle(media)
        obj.set_path(media_path.name)
        db.commit_media(obj, txn)
    db.set_mediapath(str(folder))
    original_media = revision("media", media)
    for fmt in ("xml", "gedcom"):
        args = {
            **export_args,
            "format": fmt,
            "output_path": str(
                folder / ("relative." + ("xml" if fmt == "xml" else "ged"))
            ),
        }
        preview = call("export", **args)
        call("export", **args, apply=True, expected_plan=preview["plan_revision"])
        check(
            "export_relative_media_resolves_" + fmt,
            str(media_path.resolve()).replace("\\", "/")
            in Path(args["output_path"]).read_text(encoding="utf-8").replace("\\", "/")
            and revision("media", media) == original_media,
        )
    db.set_mediapath(original_base)
    with DbTxn("Restore synthetic media path", db) as txn:
        obj = db.get_media_from_handle(media)
        obj.set_path(str(media_path))
        db.commit_media(obj, txn)
    package_args = {
        **export_args,
        "format": "gpkg",
        "output_path": str(folder / "selected.gpkg"),
    }
    preview = call("export", **package_args)
    packaged = call(
        "export", **package_args, apply=True, expected_plan=preview["plan_revision"]
    )
    with tarfile.open(package_args["output_path"]) as archive:
        check(
            "export_package_media_matches_selected_xml",
            len(packaged["media"]) == 1
            and packaged["media"][0]["handle"] == media
            and set(archive.getnames())
            == {"data.gramps", packaged["media"][0]["archive_path"]},
        )
    filtered_args = {
        **export_args,
        "person_filter": {
            "definition": {
                "name": "Selected ID",
                "rules": [
                    {
                        "class": "HasIdOf",
                        "values": [db.get_person_from_handle(root).get_gramps_id()],
                    }
                ],
            },
            "store_path": str(folder / "export-filter.xml"),
        },
        "output_path": str(folder / "native-filter.xml"),
    }
    filtered_args.pop("person_handles")
    preview = call("export", **filtered_args)
    call("export", **filtered_args, apply=True, expected_plan=preview["plan_revision"])
    check(
        "export_native_person_filter",
        preview["record_counts"]["person"] == 1
        and preview["scope"]["filter"]["handles"] == [root],
    )
    living_args = {
        **export_args,
        "living_mode": "exclude",
        "current_year": 2026,
        "output_path": str(folder / "exclude-living.xml"),
    }
    preview = call("export", **living_args)
    call("export", **living_args, apply=True, expected_plan=preview["plan_revision"])
    check(
        "export_living_unknown_dates_excluded", preview["record_counts"]["person"] == 0
    )
    redacted_args = {
        **export_args,
        "living_mode": "redact",
        "current_year": 2026,
        "output_path": str(folder / "redacted.xml"),
    }
    preview = call("export", **redacted_args)
    call("export", **redacted_args, apply=True, expected_plan=preview["plan_revision"])
    check(
        "export_living_name_redacted",
        b"Candidate" not in Path(redacted_args["output_path"]).read_bytes()
        and preview["record_counts"]["person"] == 1,
    )
    names = call("filter", operation="rules", kind="person")["rules"]
    name_rule = next(r for r in names if r["class"] == "HasNameOf")
    name_values = ["Alex"] + [""] * (len(name_rule["parameters"]) - 1)
    args = {
        **redacted_args,
        "person_filter": {
            "definition": {
                "name": "Original name after redaction",
                "rules": [{"class": "HasNameOf", "values": name_values}],
            }
        },
        "output_path": str(folder / "redacted-filter.xml"),
    }
    args.pop("person_handles")
    preview = call("export", **args)
    check(
        "export_name_filter_uses_redacted_view", preview["record_counts"]["person"] == 0
    )
    deceased, alive, death_event = create("person"), create("person"), create("event")
    with DbTxn("Synthetic living association", db) as txn:
        event = db.get_event_from_handle(death_event)
        event.set_type(EventType.DEATH)
        event.set_date_object(
            __import__("gramps.gen.lib", fromlist=["Date"]).Date(1900, 1, 1)
        )
        db.commit_event(event, txn)
        person = db.get_person_from_handle(deceased)
        event_ref = EventRef()
        event_ref.ref = death_event
        person.add_event_ref(event_ref)
        person.set_death_ref(event_ref)
        association = PersonRef()
        association.ref = alive
        association.set_relation("Synthetic associate")
        person.add_person_ref(association)
        db.commit_person(person, txn)
    original_person = revision("person", deceased)
    args = {
        "operation": "run",
        "format": "xml",
        "living_mode": "exclude",
        "current_year": 2026,
        "output_path": str(folder / "living-association.xml"),
    }
    preview = call("export", **args)
    call("export", **args, apply=True, expected_plan=preview["plan_revision"])
    raw = Path(args["output_path"]).read_bytes()
    check(
        "export_living_exclusion_trims_person_association",
        ("_" + deceased).encode() in raw
        and ("_" + alive).encode() not in raw
        and revision("person", deceased) == original_person,
    )
    from gramps.gen.config import config

    previous = config.get("preferences.private-surname-text")
    args = {**redacted_args, "output_path": str(folder / "changed-preference.xml")}
    preview = call("export", **args)
    try:
        config.set("preferences.private-surname-text", "Changed synthetic preference")
        reject(
            "export_privacy_preference_staleness",
            lambda: call(
                "export", **args, apply=True, expected_plan=preview["plan_revision"]
            ),
        )
    finally:
        config.set("preferences.private-surname-text", previous)
    reject(
        "backup_cannot_become_partial",
        lambda: call(
            "export",
            operation="backup",
            person_handles=[root],
            output_path=str(folder / "partial.gramps"),
        ),
    )
    empty_args = {
        **export_args,
        "person_handles": [],
        "output_path": str(folder / "empty-selection.xml"),
    }
    preview = call("export", **empty_args)
    call("export", **empty_args, apply=True, expected_plan=preview["plan_revision"])
    check("export_explicit_empty_selection", preview["record_counts"]["person"] == 0)
    with DbTxn("Synthetic damaged references", db) as txn:
        person = db.get_person_from_handle(root)
        person.add_note("missing-synthetic-note")
        db.commit_person(person, txn)
        broken = db.get_family_from_handle(family)
        broken.set_child_ref_list(
            broken.get_child_ref_list()
            + [copy.deepcopy(broken.get_child_ref_list()[0])]
        )
        db.commit_family(broken, txn)
    before = revision("person", root)
    damaged = call(
        "audit",
        operation="references",
        records=[
            {"kind": "person", "handle": root},
            {"kind": "family", "handle": family},
        ],
    )
    check(
        "audit_dangling_duplicate_links_without_repair",
        {"missing_target", "duplicate_child_ref"}
        <= {i["code"] for i in damaged["issues"]}
        and revision("person", root) == before
        and not damaged["repairs_run"],
    )
    cycle_family = create("family")
    with DbTxn("Synthetic parent cycle", db) as txn:
        cyclic = db.get_family_from_handle(cycle_family)
        cyclic.set_father_handle(root)
        ref = ChildRef()
        ref.ref = grand
        cyclic.add_child_ref(ref)
        db.commit_family(cyclic, txn)
        person = db.get_person_from_handle(root)
        person.add_family_handle(cycle_family)
        db.commit_person(person, txn)
        person = db.get_person_from_handle(grand)
        person.add_parent_family_handle(cycle_family)
        db.commit_person(person, txn)
    cycle = call(
        "audit",
        operation="references",
        records=[{"kind": "family", "handle": cycle_family}],
    )
    check(
        "audit_directed_parent_cycle_warning",
        any(i["code"] == "recorded_parent_cycle" for i in cycle["issues"]),
    )
    check(
        "graph_cycles_terminate",
        len(call("graph", operation="ancestors", handle=root)["nodes"]) == 4,
    )
    one, two = create("person"), create("person")
    f1, f2 = create("family"), create("family")
    with DbTxn("Synthetic depth boundary cycle", db) as txn:
        for fh, ph, ch in ((f1, one, two), (f2, two, one)):
            f = db.get_family_from_handle(fh)
            f.set_father_handle(ph)
            ref = ChildRef()
            ref.ref = ch
            f.add_child_ref(ref)
            db.commit_family(f, txn)
            p = db.get_person_from_handle(ph)
            p.add_family_handle(fh)
            db.commit_person(p, txn)
            p = db.get_person_from_handle(ch)
            p.add_parent_family_handle(fh)
            db.commit_person(p, txn)
    graph = call("graph", operation="ancestors", handle=one, max_depth=1)
    check(
        "graph_depth_boundary_preserves_known_cycle_edges",
        len(graph["edges"]) == 2 and not graph["truncated"],
    )
    with DbTxn("Synthetic warning overflow", db) as txn:
        p = db.get_person_from_handle(outsider)
        p.set_parent_family_handle_list(["missing-family-%s" % i for i in range(201)])
        db.commit_person(p, txn)
    graph = call("graph", operation="ancestors", handle=outsider)
    check(
        "graph_warning_overflow_explicit",
        len(graph["warnings"]) == 200 and graph["warnings_truncated"],
    )
    check(
        "capability_metadata_current_version",
        call("capabilities")["version"] == "2.8.0"
        and "native_exports_backups" in call("capabilities")["integrations"],
    )
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "gramps_detail_lifecycle_tests",
        Path(__file__).with_name("test") / "detail_lifecycle_test.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    checks.extend(module.run_checks())
    return checks
