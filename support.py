"""Structured Gramps 6.1 support, using the open application database only."""

import copy
import hashlib
import importlib
import json
from pathlib import Path

from gramps.gen import lib
from gramps.gen.db import DbTxn
from gramps.gen.lib.json_utils import object_to_dict, data_to_object

VERSION = "2.9.0"

KINDS = {
    "person": "Person",
    "family": "Family",
    "event": "Event",
    "place": "Place",
    "source": "Source",
    "citation": "Citation",
    "repository": "Repository",
    "media": "Media",
    "note": "Note",
    "tag": "Tag",
}
METHODS = {
    "capabilities",
    "schema",
    "object",
    "find",
    "relatives",
    "links",
    "mutate",
    "family_member",
    "attach",
    "compare",
    "merge",
    "media_info",
    "research",
    "history",
    "workflow",
    "plugins",
    "settings",
    "rows",
    "date",
    "secondary",
    "navigation",
    "gramplets",
    "filter",
    "report",
    "export",
    "graph",
    "audit",
    "tree",
    "batch",
    "batch_attach",
    "batch_file",
    "batch_records",
    "details",
    "import",
    "database",
    "media_manage",
    "sync_apply",
    "sync_refs",
}


def revision(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def merge_patch(target, patch):
    """Recursive named-field replacement; lists replace explicitly, never merge by position."""
    if not isinstance(patch, dict):
        raise ValueError("A record patch must be an object")
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if key not in target:
            raise ValueError("Unknown record field: " + key)
        if key == "_class" and value != target[key]:
            raise ValueError("Object class cannot change")
        if isinstance(value, dict) and isinstance(target[key], dict):
            result[key] = merge_patch(target[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def decode(data):
    def check(value):
        if isinstance(value, dict):
            cls = getattr(lib, value.get("_class", ""), None)
            if (
                cls is None
                or not isinstance(cls, type)
                or not hasattr(cls, "get_object_state")
            ):
                raise ValueError("Expected a supported Gramps object with _class")
            template = object_to_dict(cls())
            extra = set(value) - set(template)
            if extra:
                raise ValueError(
                    "Unknown %s fields: %s" % (cls.__name__, sorted(extra))
                )
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(data)
    obj = data_to_object(copy.deepcopy(data))
    obj.serialize()  # Validate native field shapes before opening a transaction.
    return obj


class GrampsSupport:
    def __init__(self, bridge, dbstate=None):
        self.bridge = bridge
        self.dbstate = dbstate or bridge.dbstate

    @property
    def db(self):
        db = self.dbstate.db
        if not db.is_open():
            raise ValueError("Open the intended tree in Gramps first")
        return db

    def kind(self, value):
        if value not in KINDS:
            raise ValueError("Unsupported record kind")
        return value

    def get(self, kind, handle=None, gramps_id=None):
        self.kind(kind)
        if handle:
            obj = getattr(self.db, "get_%s_from_handle" % kind)(handle)
        elif gramps_id and kind != "tag":
            obj = getattr(self.db, "get_%s_from_gramps_id" % kind)(gramps_id)
        else:
            raise ValueError("Supply handle or Gramps ID (tags use handles)")
        if obj is None:
            raise ValueError("Record not found: %s %s" % (kind, handle or gramps_id))
        return obj

    def exists(self, kind, handle):
        return bool(handle) and getattr(self.db, "has_%s_handle" % kind)(handle)

    def snapshot(self, kind, obj):
        data = object_to_dict(obj)
        return {
            "kind": kind,
            "handle": obj.handle,
            "gramps_id": getattr(obj, "gramps_id", None),
            "revision": revision(data),
            "data": data,
        }

    def summary(self, kind, obj):
        from gramps.gen.display.name import displayer

        if kind == "person":
            label = displayer.display(obj)
        else:
            label = ""
            for name in ("get_title", "get_description", "get_name", "get"):
                if hasattr(obj, name):
                    try:
                        label = str(getattr(obj, name)())[:500]
                        break
                    except TypeError:
                        pass
        return {
            "kind": kind,
            "handle": obj.handle,
            "gramps_id": getattr(obj, "gramps_id", None),
            "label": label,
        }

    def backlinks(self, obj):
        return [
            {"kind": k.lower(), "handle": h}
            for k, h in self.db.find_backlink_handles(obj.handle)
        ]

    def ensure_revision(self, obj, expected):
        if not expected or revision(object_to_dict(obj)) != expected:
            raise ValueError(
                "Record changed or revision missing; read gramps_object again before editing"
            )

    def writable(self):
        if self.db.readonly:
            raise ValueError("Gramps opened this tree read-only")
        # Avoid bypassing edits still pending in native dialogs.
        if self.dbstate is self.bridge.dbstate:
            extra = [
                w
                for w in self.bridge.windows()
                if w["type"] not in ("ApplicationWindow", "GtkTooltipWindow")
            ]
            if extra:
                raise ValueError(
                    "Finish or cancel open Gramps dialogs before a structured database write"
                )

    def validate_refs(self, obj, overlay=None):
        overlay = overlay or {}
        for cls, handle in obj.get_referenced_handles_recursively():
            kind = cls.lower()
            if not handle or kind not in KINDS:
                raise ValueError(
                    "Invalid referenced Gramps object: %s %s" % (cls, handle)
                )
            other = overlay.get((kind, handle), "not-in-overlay")
            if other == "not-in-overlay":
                other = getattr(self.db, "get_%s_from_handle" % kind)(handle)
            if other is None:
                raise ValueError("Dangling reference: %s %s" % (cls, handle))

    def changed(self, objects, label, apply=False):
        before, after = [], []
        overlay = {(kind, obj.handle): obj for kind, obj in objects}
        for kind, obj in objects:
            self.validate_refs(obj, overlay)
            existing = (
                self.get(kind, handle=obj.handle)
                if self.exists(kind, obj.handle)
                else None
            )
            before.append(self.snapshot(kind, existing) if existing else None)
            after.append(self.snapshot(kind, obj))
        if not apply:
            return {"applied": False, "before": before, "proposed": after}
        self.writable()
        with DbTxn(label, self.db) as txn:
            for kind, obj in objects:
                if not self.exists(kind, obj.handle):
                    getattr(self.db, "add_" + kind)(obj, txn)
                else:
                    getattr(self.db, "commit_" + kind)(obj, txn)
        saved = [self.snapshot(k, self.get(k, handle=o.handle)) for k, o in objects]
        return {"applied": True, "before": before, "after": saved, "undo_label": label}

    def dispatch(self, method, a):
        if method == "database":
            if not hasattr(self, "databases"):
                from importlib.util import spec_from_file_location, module_from_spec

                spec = spec_from_file_location(
                    "gramps_native_databases",
                    Path(__file__).with_name("database_support.py"),
                )
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.databases = module.DatabaseSupport(self, revision)
            return self.databases.dispatch(a)
        if method == "import":
            if not hasattr(self, "imports"):
                from importlib.util import spec_from_file_location, module_from_spec

                spec = spec_from_file_location(
                    "gramps_native_imports",
                    Path(__file__).with_name("native_imports.py"),
                )
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.imports = module.NativeImports(self, revision)
            return self.imports.dispatch(a)
        if method == "details":
            from importlib.util import spec_from_file_location, module_from_spec

            spec = spec_from_file_location(
                "gramps_special_details", Path(__file__).with_name("special_details.py")
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.dispatch(self, a, decode, merge_patch, revision)
        if method == "batch_records":
            if not hasattr(self, "record_batches"):
                from importlib.util import spec_from_file_location, module_from_spec

                spec = spec_from_file_location(
                    "gramps_native_batch", Path(__file__).with_name("native_batch.py")
                )
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.record_batches = module.NativeBatch(
                    self, decode, merge_patch, revision
                )
            return self.record_batches.dispatch(a)
        if method in (
            "secondary",
            "schema",
            "date",
            "settings",
            "navigation",
            "gramplets",
        ):
            from importlib.util import spec_from_file_location, module_from_spec

            filename = {
                "secondary": "secondary_support.py",
                "schema": "secondary_support.py",
                "date": "date_support.py",
                "settings": "preference_support.py",
                "navigation": "navigation_support.py",
                "gramplets": "navigation_support.py",
            }[method]
            spec = spec_from_file_location(
                "gramps_extended_" + method, Path(__file__).with_name(filename)
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            if method == "date":
                return module.dispatch(a)
            if method == "schema":
                return module.schema(self, a)
            if method == "settings":
                if not hasattr(self, "preferences"):
                    self.preferences = module.Preferences(self, revision)
                return self.preferences.dispatch(a)
            if method == "secondary":
                return module.dispatch(self, a, decode, merge_patch, revision)
            return (
                module.navigation(self, a)
                if method == "navigation"
                else module.gramplets(self, a, revision)
            )
        if method in ("graph", "audit", "tree"):
            from importlib.util import spec_from_file_location, module_from_spec

            filename = "tree_support.py" if method == "tree" else "analysis_support.py"
            spec = spec_from_file_location(
                "gramps_desktop_" + method, Path(__file__).with_name(filename)
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            if method == "tree":
                if not hasattr(self, "tree_metadata"):
                    self.tree_metadata = module.TreeSupport(self, revision)
                return self.tree_metadata.dispatch(a)
            return getattr(module.Analysis(self), method)(a)
        if method in ("batch", "batch_attach", "batch_file"):
            if not hasattr(self, "batches"):
                from importlib.util import spec_from_file_location, module_from_spec

                spec = spec_from_file_location(
                    "gramps_desktop_batches",
                    Path(__file__).with_name("batch_support.py"),
                )
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.batches = module.BatchSupport(self, decode, merge_patch, revision)
            return getattr(
                self.batches,
                {
                    "batch": "batch",
                    "batch_attach": "attachments",
                    "batch_file": "files",
                }[method],
            )(a)
        if method in (
            "filter",
            "report",
            "export",
            "media_manage",
            "sync_apply",
            "sync_refs",
        ):
            if not hasattr(self, "workflows"):
                from importlib.util import spec_from_file_location, module_from_spec

                spec = spec_from_file_location(
                    "gramps_desktop_workflows",
                    Path(__file__).with_name("workflow_support.py"),
                )
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self.workflows = module.WorkflowSupport(
                    self, decode, merge_patch, revision
                )
            return self.workflows.dispatch(method, a)
        if method == "capabilities":
            from gramps.version import VERSION as gramps_version
            import sys

            return {
                "version": VERSION,
                "gramps_version": gramps_version,
                "platform": sys.platform,
                "kinds": KINDS,
                "structured_methods": sorted(METHODS),
                "integrations": [
                    "automatic_abandoned_lock_recovery",
                    "native_batch_record_lifecycle",
                    "reviewed_native_import_restore",
                    "reviewed_database_lifecycle",
                    "specialised_reference_details",
                    "native_filters",
                    "native_reports",
                    "atomic_batch_updates",
                    "reviewed_native_lifecycle_batches",
                    "atomic_batch_attachments",
                    "saved_batch_plans_receipts",
                    "native_exports_backups",
                    "filtered_private_living_exports",
                    "recorded_relationship_graph",
                    "scoped_reference_duplicate_inspection",
                    "reviewed_tree_metadata",
                    "typed_schema_choices",
                    "embedded_record_edits",
                    "typed_detail_lifecycle",
                    "native_date_operations",
                    "civil_calendar_offsets",
                    "native_dashboard_gramplets",
                    "reviewed_profile_preferences",
                    "record_navigation",
                    "native_gramplet_bars",
                    "media_inspection_relink",
                    "configured_gramps_web",
                    "scoped_web_sync",
                ],
                "native_editors": list(KINDS),
                "merge_kinds": [k for k in KINDS if k != "tag"],
                "writes": "Native DbTxn with record revisions; preview unless apply=true",
                "fallback": "Native GTK controls/actions and privileged gramps_python",
                "native_menu_methods": ["menus", "menu", "cells"],
                "limits": [
                    "Native runtime tested on Windows Gramps 6.1; portable installers target 6.0/6.1",
                    "Web sync requires equal Gramps release lines and an explicit target tree",
                    "Add-on dependencies and external service access still apply",
                    "No automatic sync to the project master GEDCOM",
                    "No genealogical decision inferred from a match",
                ],
            }
        if method in ("object", "links", "media_info", "research"):
            kind = a.get("kind", "media" if method == "media_info" else "person")
            obj = self.get(kind, a.get("handle"), a.get("gramps_id"))
            if method == "object":
                return self.snapshot(kind, obj)
            result = {
                "record": self.summary(kind, obj),
                "references": [
                    {"kind": k.lower(), "handle": h}
                    for k, h in obj.get_referenced_handles_recursively()
                ],
                "backlinks": self.backlinks(obj),
            }
            if method == "links":
                return result
            if method == "media_info":
                if kind != "media":
                    raise ValueError("media_info requires a media record")
                from gramps.gen.utils.file import media_path_full

                path = media_path_full(self.db, obj.get_path())
                result.update(
                    path=obj.get_path(),
                    resolved_path=path,
                    exists=Path(path).is_file(),
                    mime_type=obj.get_mime_type(),
                    description=obj.get_description(),
                )
                return result
            # Claim-neutral context, no whole-dataset audit or inferred relationships.
            refs = []
            for ref in result["references"]:
                related = getattr(self.db, "get_%s_from_handle" % ref["kind"])(
                    ref["handle"]
                )
                if related:
                    refs.append(self.snapshot(ref["kind"], related))
            result.update(data=object_to_dict(obj), related_records=refs)
            return result
        if method == "find":
            kind = self.kind(a.get("kind", "person"))
            query = a.get("query", "")
            filters = a.get("filters", [])
            limit, offset = max(1, min(int(a.get("limit", 50)), 200)), max(
                0, int(a.get("offset", 0))
            )

            def values(data, path):
                if not path:
                    return [data]
                segment, *rest = path.split(".")
                if isinstance(data, list):
                    return [v for item in data for v in values(item, path)]
                if isinstance(data, dict) and segment in data:
                    return values(data[segment], ".".join(rest))
                return []

            def matches(data, filt):
                field, op, expected = (
                    filt["field"],
                    filt.get("op", "eq"),
                    filt.get("value"),
                )
                found = values(data, field)
                if op == "exists":
                    return bool(found) == bool(expected)
                predicates = {
                    "eq": lambda v: v == expected,
                    "contains": lambda v: str(expected).casefold() in str(v).casefold(),
                    "gt": lambda v: v > expected,
                    "lt": lambda v: v < expected,
                }
                if op == "ne":
                    return all(v != expected for v in found)
                if op not in predicates:
                    raise ValueError("Unsupported filter operator")
                return any(predicates[op](v) for v in found)

            items, skipped, more = [], 0, False
            for handle in getattr(self.db, "get_%s_handles" % kind)():
                obj = self.get(kind, handle=handle)
                if query and not obj.matches_string(query):
                    continue
                data = object_to_dict(obj)
                if not all(matches(data, filt) for filt in filters):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                if len(items) == limit:
                    more = True
                    break
                items.append(
                    self.snapshot(kind, obj)
                    if a.get("include_data")
                    else self.summary(kind, obj)
                )
            return {
                "items": items,
                "next_offset": offset + len(items) if more else None,
            }
        if method == "relatives":
            person = self.get("person", a.get("handle"), a.get("gramps_id"))
            parent_families, spouse_families = [], []
            for family_handle in (
                person.get_parent_family_handle_list() + person.get_family_handle_list()
            ):
                family = self.get("family", handle=family_handle)
                item = self.snapshot("family", family)
                handles = [family.get_father_handle(), family.get_mother_handle()] + [
                    c.ref for c in family.get_child_ref_list()
                ]
                item["people"] = [
                    self.summary("person", self.get("person", handle=h))
                    for h in handles
                    if h
                ]
                (
                    parent_families
                    if family_handle in person.get_parent_family_handle_list()
                    else spouse_families
                ).append(item)
            return {
                "person": self.summary("person", person),
                "parent_families": parent_families,
                "partner_families": spouse_families,
            }
        if method == "mutate":
            kind, operation = self.kind(a["kind"]), a["operation"]
            obj = (
                None
                if operation == "create"
                else self.get(kind, a.get("handle"), a.get("gramps_id"))
            )
            if obj:
                self.ensure_revision(obj, a.get("expected_revision"))
            if operation == "delete":
                backlinks = self.backlinks(obj)
                if backlinks:
                    raise ValueError(
                        "Referenced record; unlink it or use the native deletion dialog first"
                    )
                before = self.snapshot(kind, obj)
                if a.get("apply", False):
                    self.writable()
                    with DbTxn(a.get("label", "MCP delete " + kind), self.db) as txn:
                        getattr(self.db, "remove_" + kind)(obj.handle, txn)
                    if self.exists(kind, obj.handle):
                        raise RuntimeError("Deletion readback failed")
                return {
                    "applied": a.get("apply", False),
                    "before": before,
                    "after": None,
                }
            if operation not in ("create", "update"):
                raise ValueError("Unsupported mutation operation")
            template = object_to_dict(obj or getattr(lib, KINDS[kind])())
            patch = a.get("patch", {})
            if operation == "update" and any(
                k in patch for k in ("handle", "gramps_id", "change")
            ):
                raise ValueError(
                    "Existing identifiers and change timestamp must be preserved"
                )
            family_fields = {
                "person": ("family_list", "parent_family_list"),
                "family": ("father_handle", "mother_handle", "child_ref_list"),
            }
            if any(k in patch for k in family_fields.get(kind, ())):
                raise ValueError(
                    "Use gramps_family_member for reciprocal family relationships"
                )
            data = merge_patch(template, patch)
            updated = decode(data)
            if operation == "create":
                if updated.handle:
                    raise ValueError("New record handles are allocated by Gramps")
                ident = getattr(updated, "gramps_id", None)
                if ident and getattr(self.db, "get_%s_from_gramps_id" % kind)(ident):
                    raise ValueError("Gramps ID already exists")
            return self.changed(
                [(kind, updated)],
                a.get("label", "MCP " + operation + " " + kind),
                a.get("apply", False),
            )
        if method == "family_member":
            return self.family_member(a)
        if method == "attach":
            return self.attach(a)
        if method in ("compare", "merge"):
            kind = self.kind(a["kind"])
            left = self.get(kind, handle=a["keep_handle"])
            right = self.get(kind, handle=a["remove_handle"])
            if left.handle == right.handle:
                raise ValueError("Select two distinct records")
            comparison = {
                "keep": self.snapshot(kind, left),
                "remove": self.snapshot(kind, right),
                "keep_backlinks": self.backlinks(left),
                "remove_backlinks": self.backlinks(right),
            }
            if method == "compare" or not a.get("apply", False):
                comparison["applied"] = False
                return comparison
            if kind == "tag":
                raise ValueError("Use native tag editing for tag merges")
            self.ensure_revision(left, a.get("keep_revision"))
            self.ensure_revision(right, a.get("remove_revision"))
            self.writable()
            module = importlib.import_module("gramps.gen.merge.merge%squery" % kind)
            cls = getattr(module, "Merge%sQuery" % KINDS[kind])
            argument = self.db if kind in ("person", "family") else self.dbstate
            merger = cls(argument, left, right)
            merger.execute()
            retained = self.get(kind, handle=left.handle)
            if self.exists(kind, right.handle):
                raise RuntimeError(
                    "Native merge did not remove the selected duplicate; inspect before retrying"
                )
            return {
                "applied": True,
                "before": comparison,
                "after": self.snapshot(kind, retained),
                "mapping": {right.handle: left.handle},
            }
        if method == "history":
            undo = self.db.get_undodb()
            if a.get("operation", "list") != "list":
                op = a["operation"]
                if op not in ("undo", "redo"):
                    raise ValueError("Use list, undo or redo")
                self.writable()
                return {"applied": bool(getattr(self.db, op)())}
            return {
                "undo": [
                    {"description": x.get_description(), "timestamp": x.timestamp}
                    for x in list(undo.undoq)[-50:]
                ],
                "redo": [
                    {"description": x.get_description(), "timestamp": x.timestamp}
                    for x in list(undo.redoq)[-50:]
                ],
            }
        if method == "rows":
            from gi.repository import Gtk

            widget = self.bridge.resolve(a["widget_id"])
            if not isinstance(widget, (Gtk.TreeView, Gtk.ComboBox)):
                raise ValueError("rows requires a GTK TreeView or ComboBox")
            model = widget.get_model()
            columns = (
                [c.get_title() for c in widget.get_columns()]
                if isinstance(widget, Gtk.TreeView)
                else []
            )
            if model is None:
                return {"columns": columns, "rows": [], "total": 0, "next_offset": None}
            rows, limit = [], max(1, min(int(a.get("limit", 100)), 500))
            offset = max(0, int(a.get("offset", 0)))
            parent = (
                model.get_iter(Gtk.TreePath.new_from_string(a["parent_path"]))
                if a.get("parent_path")
                else None
            )
            count = model.iter_n_children(parent)
            for n in range(offset, min(offset + limit, count)):
                it = model.iter_nth_child(parent, n)
                values = []
                for c in range(model.get_n_columns()):
                    v = model.get_value(it, c)
                    values.append(
                        v
                        if isinstance(v, (str, int, float, bool, type(None)))
                        else str(v)
                    )
                rows.append(
                    {
                        "path": model.get_path(it).to_string(),
                        "values": values,
                        "children": model.iter_n_children(it),
                    }
                )
            return {
                "columns": columns,
                "rows": rows,
                "total": count,
                "next_offset": offset + limit if offset + limit < count else None,
            }
        if method == "workflow":
            names = {
                "import": "Import",
                "export": "Export",
                "backup": "Backup",
                "trees": "Open",
                "history": "UndoHistory",
                "addons": "AddonManager",
                "reports": "Reports",
                "tools": "Tools",
            }
            op = a["operation"]
            if op == "preferences":
                return self.bridge.dispatch(
                    "action", {"scope": "app", "name": "preferences"}
                )
            if op in names:
                return self.bridge.dispatch("action", {"name": names[op]})
            if op in ("report", "tool"):
                from gramps.gui.uimanager import valid_action_name

                return self.bridge.dispatch(
                    "action", {"name": valid_action_name(a["action_name"])}
                )
            raise ValueError("Unknown workflow")
        if method == "plugins":
            from gramps.gen.plug import PluginRegister

            register = PluginRegister.get_instance()
            from importlib.util import spec_from_file_location, module_from_spec

            spec = spec_from_file_location(
                "gramps_plugin_details",
                Path(__file__).with_name("navigation_support.py"),
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            if a.get("operation", "list") != "list":
                return module.visibility(self, a, revision)
            group = a.get("kind", "report")
            functions = {
                "report": "report_plugins",
                "tool": "tool_plugins",
                "import": "import_plugins",
                "export": "export_plugins",
                "view": "view_plugins",
                "gramplet": "gramplet_plugins",
                "database": "database_plugins",
            }
            from gramps.gen.plug._pluginreg import PTYPE, PTYPE_STR

            if group == "types":
                return [{"code": code, "label": PTYPE_STR[code]} for code in PTYPE]
            if group == "all" or a.get("plugin_type") is not None:
                codes = PTYPE if group == "all" else [a["plugin_type"]]
                if any(type(code) is not int or code not in PTYPE for code in codes):
                    raise ValueError("Select an observed native plugin type")
                items = [p for code in codes for p in register.type_plugins(code)]
            elif group not in functions:
                raise ValueError("Unsupported plugin group")
            else:
                items = getattr(register, functions[group])()
            window = self.bridge.uistate.window
            result = []
            for p in items:
                result.append(module.plugin_details(p, window))
            return result
        raise ValueError("Unknown structured Gramps command")

    def family_member(self, a):
        family = self.get("family", handle=a["family_handle"])
        person = self.get("person", handle=a["person_handle"])
        self.ensure_revision(family, a.get("family_revision"))
        self.ensure_revision(person, a.get("person_revision"))
        role, op = a["role"], a.get("operation", "add")
        if role not in ("father", "mother", "child") or op not in ("add", "remove"):
            raise ValueError("Unsupported family role or operation")
        if op == "add":
            parents = (
                [person.handle]
                if role != "child"
                else [
                    h
                    for h in (family.get_father_handle(), family.get_mother_handle())
                    if h
                ]
            )
            children = (
                [person.handle]
                if role == "child"
                else [c.ref for c in family.get_child_ref_list()]
            )
            for parent in parents:
                pending, seen = [parent], set()
                while pending:
                    handle = pending.pop()
                    if handle in children:
                        raise ValueError(
                            "Family membership would create an ancestry cycle"
                        )
                    if handle in seen:
                        continue
                    seen.add(handle)
                    ancestor = self.get("person", handle=handle)
                    for fh in ancestor.get_parent_family_handle_list():
                        pf = self.get("family", handle=fh)
                        pending.extend(
                            h
                            for h in (pf.get_father_handle(), pf.get_mother_handle())
                            if h
                        )
        objects = [("family", family), ("person", person)]
        if role == "child":
            refs = family.get_child_ref_list()
            existing = next((r for r in refs if r.ref == person.handle), None)
            if op == "add":
                if person.handle in (
                    family.get_father_handle(),
                    family.get_mother_handle(),
                ):
                    raise ValueError("A family parent cannot also be its child")
                ref = existing or lib.ChildRef()
                ref.ref = person.handle
                ref.frel.set_from_xml_str(a.get("father_relation", "Unknown"))
                ref.mrel.set_from_xml_str(a.get("mother_relation", "Unknown"))
                if not existing:
                    family.add_child_ref(ref)
                if family.handle not in person.get_parent_family_handle_list():
                    person.add_parent_family_handle(family.handle)
            elif existing:
                family.remove_child_ref(existing)
                person.remove_parent_family_handle(family.handle)
        else:
            current = getattr(family, "get_%s_handle" % role)()
            if op == "add":
                if any(c.ref == person.handle for c in family.get_child_ref_list()):
                    raise ValueError("A family child cannot also be its parent")
                if current and current != person.handle:
                    raise ValueError(
                        "Remove the existing parent explicitly before replacing it"
                    )
                other = (
                    family.get_mother_handle()
                    if role == "father"
                    else family.get_father_handle()
                )
                if other == person.handle:
                    raise ValueError(
                        "The same person cannot occupy both parent positions"
                    )
                getattr(family, "set_%s_handle" % role)(person.handle)
                if family.handle not in person.get_family_handle_list():
                    person.add_family_handle(family.handle)
            elif current == person.handle:
                getattr(family, "set_%s_handle" % role)(None)
                person.remove_family_handle(family.handle)
        return self.changed(
            objects, a.get("label", "MCP family relationship"), a.get("apply", False)
        )

    def attach(self, a):
        kind = self.kind(a["kind"])
        owner = self.get(kind, handle=a["handle"])
        self.ensure_revision(owner, a.get("expected_revision"))
        target_kind = self.kind(a["target_kind"])
        target = self.get(target_kind, handle=a["target_handle"])
        self.prepare_attachment(
            kind,
            owner,
            target_kind,
            target,
            a.get("operation", "add"),
            a.get("reference_patch"),
        )
        return self.changed(
            [(kind, owner)],
            a.get("label", "MCP " + target_kind + " attachment"),
            a.get("apply", False),
        )

    def prepare_attachment(
        self,
        kind,
        owner,
        target_kind,
        target,
        op,
        patch=None,
        strict=False,
        all_references=False,
    ):
        """Stage one native attachment without committing or touching child notes."""
        if op not in ("add", "remove"):
            raise ValueError("Use add or remove")
        direct = {
            "citation": ("get_citation_list", "add_citation", "remove_citation"),
            "note": ("get_note_list", "add_note", "remove_note"),
            "tag": ("get_tag_list", "add_tag", "remove_tag"),
        }
        refs = {
            "event": (
                "get_event_ref_list",
                "add_event_ref",
                "_remove_event_references",
                lib.EventRef,
            ),
            "media": (
                "get_media_list",
                "add_media_reference",
                "remove_media_references",
                lib.MediaRef,
            ),
            "repository": (
                "get_reporef_list",
                "add_repo_reference",
                "remove_repo_references",
                lib.RepoRef,
            ),
        }
        if target_kind in direct:
            if strict and (patch or all_references):
                raise ValueError("Direct attachments do not accept reference patches")
            if strict and target_kind == "citation" and op == "add":
                if not target.get_reference_handle():
                    raise ValueError("Attach only citations with an existing source")
                self.get("source", handle=target.get_reference_handle())
            get, add, remove = direct[target_kind]
            if not hasattr(owner, get):
                raise ValueError("This record type does not support that attachment")
            exists = target.handle in getattr(owner, get)()
            if op == "add" and not exists:
                getattr(owner, add)(target.handle)
            elif op == "remove" and exists:
                if target_kind in ("citation", "note"):
                    setter = (
                        owner.set_citation_list
                        if target_kind == "citation"
                        else owner.set_note_list
                    )
                    setter([h for h in getattr(owner, get)() if h != target.handle])
                else:
                    getattr(owner, remove)(target.handle)
        elif target_kind in refs:
            get, add, remove, cls = refs[target_kind]
            if not hasattr(owner, get):
                raise ValueError("This record type does not support that reference")
            matches = [r for r in getattr(owner, get)() if r.ref == target.handle]
            existing = matches[0] if matches else None
            if strict and all_references and op != "remove":
                raise ValueError(
                    "all_references is only valid for removing native references"
                )
            if op == "add" and (strict or not existing):
                ref = cls()
                ref.ref = target.handle
                if target_kind == "event":
                    ref.set_role(
                        lib.EventRoleType.FAMILY
                        if kind == "family"
                        else lib.EventRoleType.PRIMARY
                    )
                if patch:
                    ref = decode(merge_patch(object_to_dict(ref), patch))
                    if ref.ref != target.handle:
                        raise ValueError("Reference patch cannot redirect the target")
                duplicates = (
                    [old for old in matches if old.is_equal(ref)] if strict else []
                )
                if duplicates:
                    if not any(
                        object_to_dict(old) == object_to_dict(ref) for old in duplicates
                    ):
                        raise ValueError(
                            "Matching native reference has different metadata; edit it explicitly"
                        )
                else:
                    getattr(owner, add)(ref)
            elif op == "remove" and existing:
                selected = matches
                if strict and patch:
                    selected = []
                    for old in matches:
                        data = object_to_dict(old)
                        candidate = decode(merge_patch(data, patch))
                        if candidate.ref != target.handle:
                            raise ValueError(
                                "Reference selector cannot redirect the target"
                            )
                        if object_to_dict(candidate) == data:
                            selected.append(old)
                if strict and len(selected) > 1 and not all_references:
                    raise ValueError(
                        "Ambiguous references; supply a matching selector or all_references=true"
                    )
                if strict or target_kind == "event":
                    setters = (
                        {"event": owner.set_event_ref_list}
                        if target_kind == "event"
                        else {
                            "media": getattr(owner, "set_media_list", None),
                            "repository": getattr(owner, "set_reporef_list", None),
                        }
                    )
                    setters[target_kind](
                        [
                            ref
                            for ref in getattr(owner, get)()
                            if all(ref is not old for old in selected)
                        ]
                    )
                else:
                    getattr(owner, remove)([target.handle])
            if target_kind == "event" and kind == "person":
                self.db.set_birth_death_index(owner)
        elif kind == "citation" and target_kind == "source":
            if strict and (patch or all_references):
                raise ValueError(
                    "Citation source replacement does not accept a reference patch"
                )
            if op == "remove":
                raise ValueError(
                    "A citation requires a source; replace the source explicitly"
                )
            owner.set_reference_handle(target.handle)
        else:
            raise ValueError(
                "Unsupported attachment; use named record fields or the native editor"
            )
        return owner
