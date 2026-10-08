#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2026  Gramps Codex Desktop Plugin contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
"""Predict native record lifecycle operations in an isolated SQLite database."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import copy
import importlib
import time
from types import SimpleNamespace
from typing import Any
import uuid

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.gen import lib
from gramps.gen.db import DbTxn
from gramps.gen.lib.json_utils import object_to_dict
from gramps.plugins.db.dbapi.sqlite import SQLite

KINDS = {
    kind: kind.title()
    for kind in (
        "person",
        "family",
        "event",
        "place",
        "source",
        "citation",
        "repository",
        "media",
        "note",
        "tag",
    )
}


def records(db: Any, maximum: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Read a bounded detached snapshot of every native primary record."""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for kind in KINDS:
        for handle in getattr(db, "get_%s_handles" % kind)():
            if len(result) >= maximum:
                raise ValueError(
                    "Detached lifecycle preview exceeds max_records; raise the explicit bound"
                )
            result[kind, handle] = object_to_dict(
                getattr(db, "get_%s_from_handle" % kind)(handle)
            )
    return result


def content(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compare record contents independently of native commit timestamps."""
    return (
        {key: value for key, value in data.items() if key != "change"} if data else None
    )


# -------------------------------------------------------------------------
# NativeBatch
# -------------------------------------------------------------------------
class NativeBatch:
    """Retain reviewed predictions and apply their exact delta in one transaction."""

    def __init__(
        self, support: Any, decode: Any, merge_patch: Any, revision: Any
    ) -> None:
        """Share guarded support without exposing the live database to mergers."""
        self.support, self.decode = support, decode
        self.merge_patch, self.revision = merge_patch, revision
        self.plans: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}

    def context(self) -> dict[str, Any]:
        """Use the existing canonical tree binding."""
        self.support.dispatch("batch", {"operation": "receipts"})
        return self.support.batches.context()

    def fingerprint(self, data: dict[tuple[str, str], Any], home: str | None) -> str:
        """Bind every inspected record and the detached home-person metadata."""
        return self.revision(
            {
                "records": [[*key, value] for key, value in sorted(data.items())],
                "home": home,
            }
        )

    def resolve_new(self, value: Any, created: dict[str, str]) -> Any:
        """Resolve explicit references to an earlier creation's client_id."""
        if isinstance(value, str) and value.startswith("$new:"):
            if value[5:] not in created:
                raise ValueError("New reference must name an earlier client_id")
            return created[value[5:]]
        if isinstance(value, list):
            return [self.resolve_new(item, created) for item in value]
        if isinstance(value, dict):
            return {key: self.resolve_new(item, created) for key, item in value.items()}
        return value

    def choose(self, kind: str, left: Any, right: Any, choices: dict[str, str]) -> None:
        """Apply independent native person name, gender and ID selections."""
        if kind != "person" and choices:
            raise ValueError("Independent choices currently support person merges")
        if set(choices) - {"primary_name", "gender", "gramps_id"} or any(
            value not in ("keep", "remove") for value in choices.values()
        ):
            raise ValueError(
                "Merge choices use primary_name/gender/gramps_id: keep or remove"
            )
        if choices.get("primary_name") == "remove":
            name = left.get_primary_name()
            left.set_primary_name(right.get_primary_name())
            right.set_primary_name(name)
        if choices.get("gender") == "remove":
            left.set_gender(right.get_gender())
        if choices.get("gramps_id") == "remove":
            ident = left.gramps_id
            left.gramps_id, right.gramps_id = right.gramps_id, ident

    def preview(self, a: dict[str, Any]) -> dict[str, Any]:
        """Run ordered operations only on a fully detached native database."""
        operations = a.get("changes", [])
        maximum = a.get("max_records", 50000)
        if type(maximum) is not int or not 1 <= maximum <= 200000:
            raise ValueError("max_records must be between 1 and 200000")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
            raise ValueError("Supply 1–200 ordered lifecycle operations")
        s, live = self.support, self.support.db
        before = records(live, maximum)
        home = live.get_default_handle()
        detached = SQLite()
        detached.load(":memory:")
        created: dict[str, str] = {}
        mapping, outcomes = {}, []
        merge_mappings: dict[str, dict[str, str | None]] = {kind: {} for kind in KINDS}
        try:
            with DbTxn("Detached preview seed", detached, batch=True) as txn:
                for (kind, _), data in before.items():
                    getattr(detached, "commit_" + kind)(
                        self.decode(data), txn, change_time=data.get("change")
                    )
            detached.set_default_person_handle(home)
            for op in operations:
                if not isinstance(op, dict):
                    raise ValueError("Each lifecycle operation must be an object")
                kind = s.kind(op.get("kind"))
                operation = op.get("operation")
                allowed = {
                    "operation",
                    "kind",
                    "handle",
                    "expected_revision",
                    "patch",
                    "client_id",
                    "keep_handle",
                    "remove_handle",
                    "keep_revision",
                    "remove_revision",
                    "choices",
                }
                if set(op) - allowed:
                    raise ValueError("Unknown lifecycle operation fields")
                if operation == "create":
                    if set(op) - {"operation", "kind", "patch", "client_id"}:
                        raise ValueError(
                            "Creation accepts only kind, patch and optional client_id"
                        )
                    ident = op.get("client_id")
                    if ident is not None and (
                        not isinstance(ident, str) or not ident or ident in created
                    ):
                        raise ValueError(
                            "Creation client_id must be a unique nonempty string"
                        )
                    patch = self.resolve_new(op.get("patch", {}), created)
                    if any(key in patch for key in ("handle", "change", "_class")):
                        raise ValueError(
                            "Native creation allocates handles and timestamps"
                        )
                    relationships = {
                        "person": ("family_list", "parent_family_list"),
                        "family": ("father_handle", "mother_handle", "child_ref_list"),
                    }
                    if any(key in patch for key in relationships.get(kind, ())):
                        raise ValueError(
                            "Create records first, then use family_member for reciprocal memberships"
                        )
                    obj = self.decode(
                        self.merge_patch(
                            object_to_dict(getattr(lib, KINDS[kind])()), patch
                        )
                    )
                    if (
                        kind != "tag"
                        and obj.gramps_id
                        and getattr(detached, "get_%s_from_gramps_id" % kind)(
                            obj.gramps_id
                        )
                    ):
                        raise ValueError("Creation Gramps ID already exists")
                    with DbTxn("Detached creation", detached) as txn:
                        add = getattr(detached, "add_" + kind)
                        (
                            add(obj, txn)
                            if kind == "tag"
                            else add(obj, txn, set_gid=not bool(obj.gramps_id))
                        )
                    if ident is not None:
                        created[ident] = obj.handle
                    outcomes.append(
                        {
                            "operation": operation,
                            "kind": kind,
                            "handle": obj.handle,
                            "client_id": ident,
                        }
                    )
                elif operation in ("delete", "merge"):
                    selected = (
                        (("handle", "expected_revision"),)
                        if operation == "delete"
                        else (
                            ("keep_handle", "keep_revision"),
                            ("remove_handle", "remove_revision"),
                        )
                    )
                    objects = []
                    for key, rev in selected:
                        handle = self.resolve_new(op.get(key), created)
                        original = before.get((kind, handle))
                        if original is not None and self.revision(original) != op.get(
                            rev
                        ):
                            raise ValueError(
                                "Every existing selection needs its original current revision"
                            )
                        obj = getattr(detached, "get_%s_from_handle" % kind)(handle)
                        if obj is None:
                            raise ValueError(
                                "Ordered operation refers to a missing or already removed record"
                            )
                        objects.append(obj)
                    if operation == "delete":
                        obj = objects[0]
                        with DbTxn("Detached deletion", detached) as txn:
                            for cls, handle in list(
                                detached.find_backlink_handles(obj.handle)
                            ):
                                owner = detached.method("get_%s_from_handle", cls)(
                                    handle
                                )
                                owner.remove_handle_references(
                                    KINDS[kind], [obj.handle]
                                )
                                detached.method("commit_%s", cls)(owner, txn)
                            getattr(detached, "remove_" + kind)(obj.handle, txn)
                        if detached.get_default_handle() == obj.handle:
                            detached.set_default_person_handle(None)
                        outcomes.append(
                            {"operation": operation, "kind": kind, "handle": obj.handle}
                        )
                    else:
                        if kind == "tag" or objects[0].handle == objects[1].handle:
                            raise ValueError(
                                "Merge two distinct records of one of the nine native kinds"
                            )
                        self.choose(kind, objects[0], objects[1], op.get("choices", {}))
                        module = importlib.import_module(
                            "gramps.gen.merge.merge%squery" % kind
                        )
                        query = getattr(module, "Merge%sQuery" % KINDS[kind])
                        arg = (
                            detached
                            if kind in ("person", "family")
                            else SimpleNamespace(db=detached)
                        )
                        merger = query(arg, *objects)
                        if kind == "person":
                            native_families = merger.merge_families

                            def cb_merge_families(
                                keep: str, removed: Any, txn: Any
                            ) -> Any:
                                """Record native implicit duplicate-family mergers."""
                                result = native_families(keep, removed, txn)
                                merge_mappings["family"][removed.handle] = keep
                                return result

                            merger.merge_families = cb_merge_families
                        elif kind == "family":
                            native_parents = merger.merge_person

                            def cb_merge_parent(
                                keep: Any, removed: Any, parent: str, txn: Any
                            ) -> Any:
                                """Record native implicit parent mergers without inventing identities."""
                                result = native_parents(keep, removed, parent, txn)
                                if (
                                    keep is not None
                                    and removed is not None
                                    and keep.handle != removed.handle
                                ):
                                    merge_mappings["person"][
                                        removed.handle
                                    ] = keep.handle
                                return result

                            merger.merge_person = cb_merge_parent
                        outcome = merger.execute()
                        merge_mappings[kind][objects[1].handle] = objects[0].handle
                        mapping[objects[1].handle] = objects[0].handle
                        outcomes.append(
                            {
                                "operation": operation,
                                "kind": kind,
                                "keep_handle": objects[0].handle,
                                "remove_handle": objects[1].handle,
                                "native_result": outcome,
                            }
                        )
                else:
                    raise ValueError(
                        "Lifecycle operation must be create, delete or merge"
                    )
            after = records(detached, maximum + len(operations))
            for merged_kind, merges in merge_mappings.items():
                for removed, retained in list(merges.items()):
                    seen = {removed}
                    while retained in merges:
                        if retained in seen:
                            raise ValueError("Native merge mapping contains a cycle")
                        seen.add(retained)
                        retained = merges[retained]
                    merges[removed] = (
                        retained if (merged_kind, retained) in after else None
                    )
            mapping = {
                removed: retained
                for merges in merge_mappings.values()
                for removed, retained in merges.items()
            }
            proposed = []
            for record_key in sorted(before.keys() | after.keys()):
                old, new = before.get(record_key), after.get(record_key)
                if content(old) != content(new):
                    if new:
                        obj = self.decode(new)
                        for cls, handle in obj.get_referenced_handles_recursively():
                            if (cls.lower(), handle) not in after:
                                raise ValueError(
                                    "Predicted lifecycle result contains a dangling reference"
                                )
                    proposed.append(
                        {
                            "kind": record_key[0],
                            "handle": record_key[1],
                            "before": old,
                            "proposed": new,
                        }
                    )
            plan = {
                "tree": self.context(),
                "base_revision": self.fingerprint(before, home),
                "home_before": home,
                "home_proposed": detached.get_default_handle(),
                "changes": copy.deepcopy(operations),
                "proposed": proposed,
                "created_handles": created,
                "mapping": mapping,
                "merge_mappings": {
                    kind: merges for kind, merges in merge_mappings.items() if merges
                },
                "outcomes": outcomes,
                "max_records": maximum,
                "undo_scope": "Records use native undo; home metadata requires receipt rollback",
            }
            revision = self.revision(plan)
            self.plans[revision] = copy.deepcopy(plan)
            while len(self.plans) > 10:
                del self.plans[next(iter(self.plans))]
            return {**plan, "plan_revision": revision, "applied": False}
        finally:
            detached.close(update=False)

    def apply(self, plan: dict[str, Any], revision: str, label: str) -> dict[str, Any]:
        """Commit a reviewed delta only while its inspected database is unchanged."""
        s, db = self.support, self.support.db
        s.writable()
        if (
            plan["tree"] != self.context()
            or self.fingerprint(
                records(db, plan["max_records"]), db.get_default_handle()
            )
            != plan["base_revision"]
        ):
            raise ValueError("Lifecycle preview is stale or belongs to another tree")
        home = db.get_default_handle()
        try:
            with DbTxn(label, db) as txn:
                for item in plan["proposed"]:
                    if item["proposed"] is not None:
                        getattr(db, "commit_" + item["kind"])(
                            self.decode(item["proposed"]), txn
                        )
                for item in plan["proposed"]:
                    if item["proposed"] is None:
                        getattr(db, "remove_" + item["kind"])(item["handle"], txn)
                if plan["home_proposed"] != home:
                    db.set_default_person_handle(plan["home_proposed"])
        except Exception:
            if db.get_default_handle() != home:
                db.set_default_person_handle(home)
            raise
        saved = []
        diagnostics: list[str] = []
        for item in plan["proposed"]:
            try:
                obj = (
                    getattr(db, "get_%s_from_handle" % item["kind"])(item["handle"])
                    if getattr(db, "has_%s_handle" % item["kind"])(item["handle"])
                    else None
                )
                data = object_to_dict(obj) if obj else None
                matches = content(data) == content(item["proposed"])
                saved.append({**item, "after": data, "verified": matches})
                if not matches:
                    diagnostics.append(
                        "Lifecycle readback differs for "
                        + item["kind"]
                        + ":"
                        + item["handle"]
                    )
            except Exception as exc:
                saved.append({**item, "after": None, "verified": False})
                diagnostics.append(
                    "Lifecycle readback: " + type(exc).__name__ + ": " + str(exc)
                )
        try:
            after_home = db.get_default_handle()
        except Exception as exc:
            after_home = None
            diagnostics.append("Home readback: " + type(exc).__name__ + ": " + str(exc))
        receipt = {
            "receipt_id": uuid.uuid4().hex,
            "tree": plan["tree"],
            "created_at": time.time(),
            "plan_revision": revision,
            "records": saved,
            "home_before": home,
            "home_after": after_home,
            "mapping": plan.get("mapping", {}),
            "merge_mappings": plan.get("merge_mappings", {}),
            "created_handles": plan.get("created_handles", {}),
            "outcomes": plan.get("outcomes", []),
            "applied": True,
            "outcome": "indeterminate" if diagnostics else "completed",
            "diagnostics": diagnostics,
            "undo_label": label,
            "rollback_scope": "Same tree/session; affected records, references and home must be unchanged",
        }
        self.receipts[receipt["receipt_id"]] = copy.deepcopy(receipt)
        while len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return receipt

    def dispatch(self, a: dict[str, Any]) -> dict[str, Any]:
        """Preview/apply lifecycle batches or inspect/roll back session receipts."""
        operation = a.get("operation", "run")
        if operation == "receipts":
            return {
                "receipts": [
                    {
                        key: item[key]
                        for key in ("receipt_id", "tree", "created_at", "undo_label")
                    }
                    for item in self.receipts.values()
                ]
            }
        if operation in ("receipt", "rollback"):
            if a.get("receipt_id") not in self.receipts:
                raise ValueError("Unknown lifecycle receipt in this session")
            receipt = copy.deepcopy(self.receipts[a["receipt_id"]])
            if operation == "receipt":
                return receipt
            if receipt.get("outcome") != "completed":
                raise ValueError(
                    "Inspect the indeterminate lifecycle result before recovery; automatic receipt rollback is unavailable"
                )
            db = self.support.db
            if (
                receipt["tree"] != self.context()
                or db.get_default_handle() != receipt["home_after"]
            ):
                raise ValueError("Rollback tree/home metadata changed")
            before = records(db, a.get("max_records", 50000))
            for item in receipt["records"]:
                key = item["kind"], item["handle"]
                if before.get(key) != item["after"]:
                    raise ValueError("Rollback would overwrite later edits")
            after = copy.deepcopy(before)
            for item in receipt["records"]:
                key = item["kind"], item["handle"]
                if item["before"] is None:
                    after.pop(key, None)
                else:
                    after[key] = item["before"]
            for data in after.values():
                for cls, handle in self.decode(
                    data
                ).get_referenced_handles_recursively():
                    if (cls.lower(), handle) not in after:
                        raise ValueError("Rollback would break a later reference")
            plan = {
                "tree": receipt["tree"],
                "base_revision": self.fingerprint(before, db.get_default_handle()),
                "home_before": receipt["home_after"],
                "home_proposed": receipt["home_before"],
                "max_records": a.get("max_records", 50000),
                "proposed": [
                    {
                        "kind": item["kind"],
                        "handle": item["handle"],
                        "before": item["after"],
                        "proposed": item["before"],
                    }
                    for item in receipt["records"]
                ],
            }
            revision = self.revision(plan)
            if not a.get("apply", False):
                return {**plan, "plan_revision": revision, "applied": False}
            if a.get("expected_plan") != revision:
                raise ValueError("Rollback preview missing or changed")
            return self.apply(
                plan, revision, a.get("label", "Plugin lifecycle rollback")
            )
        if operation != "run":
            raise ValueError("Select run, receipt, receipts or rollback")
        if not a.get("apply", False):
            return self.preview(a)
        plan_revision = a.get("expected_plan")
        if not isinstance(plan_revision, str):
            raise ValueError("Use an unchanged lifecycle preview from this session")
        retained_plan = self.plans.get(plan_revision)
        if retained_plan is None or retained_plan["changes"] != a.get("changes"):
            raise ValueError("Use an unchanged lifecycle preview from this session")
        result = self.apply(
            retained_plan,
            plan_revision,
            a.get("label", "Plugin record lifecycle batch"),
        )
        del self.plans[plan_revision]
        return result
