#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2026  Gramps Desktop plugin contributors
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
"""Verify record lifecycle predictions against synthetic native databases."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.gen import lib
from gramps.gen.db import DbTxn
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import support


# -------------------------------------------------------------------------
# LifecycleBatches
# -------------------------------------------------------------------------
class LifecycleBatches(unittest.TestCase):
    """Use isolated memory trees; never access the application's database."""

    def setUp(self) -> None:
        """Create a synthetic service with no native windows."""
        self.db = SQLite()
        self.db.load(":memory:")
        state = SimpleNamespace(db=self.db)
        bridge = SimpleNamespace(dbstate=state, windows=lambda: [])
        self.service = support.GrampsSupport(bridge, state)

    def tearDown(self) -> None:
        """Release only the disposable database."""
        self.db.close(update=False)

    def create(self, kind: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Seed one native record using the existing service."""
        return self.service.dispatch(
            "mutate",
            {"operation": "create", "kind": kind, "patch": patch or {}, "apply": True},
        )["after"][0]

    def run_batch(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Preview before committing the exact retained plan."""
        preview = self.service.dispatch("batch_records", {"changes": changes})
        return self.service.dispatch(
            "batch_records",
            {
                "changes": changes,
                "apply": True,
                "expected_plan": preview["plan_revision"],
            },
        )

    def test_create_preview_allocates_only_detached_records(self) -> None:
        """Bind native IDs/handles and references to earlier creations."""
        changes = [
            {
                "operation": "create",
                "kind": "source",
                "client_id": "source",
                "patch": {"title": "Synthetic"},
            },
            {
                "operation": "create",
                "kind": "citation",
                "client_id": "citation",
                "patch": {"source_handle": "$new:source"},
            },
        ]
        preview = self.service.dispatch("batch_records", {"changes": changes})
        self.assertEqual(list(self.db.get_source_handles()), [])
        receipt = self.service.dispatch(
            "batch_records",
            {
                "changes": changes,
                "apply": True,
                "expected_plan": preview["plan_revision"],
            },
        )
        self.assertEqual(receipt["created_handles"], preview["created_handles"])
        citation = self.db.get_citation_from_handle(
            receipt["created_handles"]["citation"]
        )
        self.assertEqual(citation.source_handle, receipt["created_handles"]["source"])
        rollback = self.service.dispatch(
            "batch_records",
            {"operation": "rollback", "receipt_id": receipt["receipt_id"]},
        )
        self.service.dispatch(
            "batch_records",
            {
                "operation": "rollback",
                "receipt_id": receipt["receipt_id"],
                "apply": True,
                "expected_plan": rollback["plan_revision"],
            },
        )
        self.assertEqual(list(self.db.get_source_handles()), [])
        self.assertEqual(list(self.db.get_citation_handles()), [])

    def test_delete_predicts_backlink_cleanup_and_rollback(self) -> None:
        """Restore deleted records and exact surviving reference owners."""
        note = self.create("note", {"text": {"string": "Synthetic"}})
        person = self.create("person", {"note_list": [note["handle"]]})
        changes = [
            {
                "operation": "delete",
                "kind": "note",
                "handle": note["handle"],
                "expected_revision": note["revision"],
            }
        ]
        preview = self.service.dispatch("batch_records", {"changes": changes})
        self.assertEqual(len(preview["proposed"]), 2)
        self.assertEqual(
            self.db.get_person_from_handle(person["handle"]).note_list, [note["handle"]]
        )
        receipt = self.run_batch(changes)
        self.assertEqual(self.db.get_person_from_handle(person["handle"]).note_list, [])
        args = {"operation": "rollback", "receipt_id": receipt["receipt_id"]}
        rollback = self.service.dispatch("batch_records", args)
        self.service.dispatch(
            "batch_records",
            {**args, "apply": True, "expected_plan": rollback["plan_revision"]},
        )
        self.assertEqual(
            self.db.get_person_from_handle(person["handle"]).note_list, [note["handle"]]
        )

    def test_person_merge_choices_home_and_undo(self) -> None:
        """Preview the native merged result with independent field choices."""
        keep = self.create(
            "person", {"gender": 1, "primary_name": {"first_name": "Keep"}}
        )
        remove = self.create(
            "person", {"gender": 0, "primary_name": {"first_name": "Remove"}}
        )
        self.db.set_default_person_handle(remove["handle"])
        changes = [
            {
                "operation": "merge",
                "kind": "person",
                "keep_handle": keep["handle"],
                "remove_handle": remove["handle"],
                "keep_revision": keep["revision"],
                "remove_revision": remove["revision"],
                "choices": {
                    "primary_name": "remove",
                    "gender": "remove",
                    "gramps_id": "remove",
                },
            }
        ]
        receipt = self.run_batch(changes)
        merged = self.db.get_person_from_handle(keep["handle"])
        self.assertEqual(merged.primary_name.first_name, "Remove")
        self.assertEqual(merged.gender, 0)
        self.assertEqual(merged.gramps_id, remove["gramps_id"])
        self.assertIn("Keep", [name.first_name for name in merged.alternate_names])
        self.assertEqual(self.db.get_default_handle(), keep["handle"])
        args = {"operation": "rollback", "receipt_id": receipt["receipt_id"]}
        rollback = self.service.dispatch("batch_records", args)
        self.service.dispatch(
            "batch_records",
            {**args, "apply": True, "expected_plan": rollback["plan_revision"]},
        )
        self.assertEqual(self.db.get_default_handle(), remove["handle"])
        self.assertEqual(
            self.db.get_person_from_handle(keep["handle"]).gramps_id, keep["gramps_id"]
        )

    def test_stale_preview_later_edits_and_bounds_rejected(self) -> None:
        """Fail closed without overwriting edits or reusing an applied plan."""
        note = self.create("note")
        changes = [
            {
                "operation": "delete",
                "kind": "note",
                "handle": note["handle"],
                "expected_revision": note["revision"],
            }
        ]
        preview = self.service.dispatch("batch_records", {"changes": changes})
        obj = self.db.get_note_from_handle(note["handle"])
        obj.set("Later edit")
        with DbTxn("Synthetic later edit", self.db) as txn:
            self.db.commit_note(obj, txn)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service.dispatch(
                "batch_records",
                {
                    "changes": changes,
                    "apply": True,
                    "expected_plan": preview["plan_revision"],
                },
            )
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "batch_records", {"changes": changes, "max_records": True}
            )
        self.assertEqual(
            self.db.get_note_from_handle(note["handle"]).get(), "Later edit"
        )

    def test_native_merge_kinds(self) -> None:
        """Predict and apply each supported native pairwise merger."""
        for kind in (
            "family",
            "event",
            "place",
            "source",
            "citation",
            "repository",
            "media",
            "note",
        ):
            with self.subTest(kind=kind):
                patch = {}
                if kind == "citation":
                    source = self.create("source")
                    patch = {"source_handle": source["handle"]}
                left, right = self.create(kind, patch), self.create(kind, patch)
                receipt = self.run_batch(
                    [
                        {
                            "operation": "merge",
                            "kind": kind,
                            "keep_handle": left["handle"],
                            "remove_handle": right["handle"],
                            "keep_revision": left["revision"],
                            "remove_revision": right["revision"],
                        }
                    ]
                )
                self.assertEqual(receipt["mapping"][right["handle"]], left["handle"])
                self.assertFalse(
                    getattr(self.db, "has_%s_handle" % kind)(right["handle"])
                )

    def test_transitive_and_implicit_native_merge_mappings(self) -> None:
        """Receipts resolve final survivors and include native related mergers."""
        notes = [self.create("note") for _ in range(3)]
        changes = [
            {
                "operation": "merge",
                "kind": "note",
                "keep_handle": notes[b]["handle"],
                "remove_handle": notes[a]["handle"],
                "keep_revision": notes[b]["revision"],
                "remove_revision": notes[a]["revision"],
            }
            for a, b in ((0, 1), (1, 2))
        ]
        receipt = self.run_batch(changes)
        self.assertEqual(receipt["mapping"][notes[0]["handle"]], notes[2]["handle"])
        for merge_kind in ("person", "family"):
            with self.subTest(kind=merge_kind):
                people = [lib.Person() for _ in range(3)]
                families = [lib.Family(), lib.Family()]
                with DbTxn("Synthetic implicit merge", self.db) as txn:
                    for person in people:
                        self.db.add_person(person, txn)
                    for index, family in enumerate(families):
                        family.father_handle, family.mother_handle = (
                            people[index].handle,
                            people[2].handle,
                        )
                        self.db.add_family(family, txn)
                        people[index].add_family_handle(family.handle)
                        people[2].add_family_handle(family.handle)
                    for person in people:
                        self.db.commit_person(person, txn)
                pair = people[:2] if merge_kind == "person" else families
                snapshots = [self.service.snapshot(merge_kind, obj) for obj in pair]
                receipt = self.run_batch(
                    [
                        {
                            "operation": "merge",
                            "kind": merge_kind,
                            "keep_handle": snapshots[0]["handle"],
                            "remove_handle": snapshots[1]["handle"],
                            "keep_revision": snapshots[0]["revision"],
                            "remove_revision": snapshots[1]["revision"],
                        }
                    ]
                )
                implicit = "family" if merge_kind == "person" else "person"
                self.assertTrue(receipt["merge_mappings"][implicit])
                for removed, retained in receipt["merge_mappings"][implicit].items():
                    self.assertFalse(
                        getattr(self.db, "has_%s_handle" % implicit)(removed)
                    )
                    self.assertTrue(
                        getattr(self.db, "has_%s_handle" % implicit)(retained)
                    )

    def test_atomic_failure_and_postcommit_receipt(self) -> None:
        """Rollback native commit failure and retain unknown readback outcomes."""
        args = {
            "changes": [{"operation": "create", "kind": "note", "client_id": "new"}]
        }
        preview = self.service.dispatch("batch_records", args)
        with patch.object(
            self.db, "commit_note", side_effect=OSError("synthetic commit failure")
        ):
            with self.assertRaises(OSError):
                self.service.dispatch(
                    "batch_records",
                    {**args, "apply": True, "expected_plan": preview["plan_revision"]},
                )
        self.assertEqual(self.db.get_number_of_notes(), 0)
        native_has = self.db.has_note_handle
        calls = 0

        def cb_has(handle: str) -> bool:
            """Reject only the final record inspection."""
            nonlocal calls
            calls += 1
            raise OSError("synthetic readback failure")

        with patch.object(self.db, "has_note_handle", side_effect=cb_has):
            receipt = self.service.dispatch(
                "batch_records",
                {**args, "apply": True, "expected_plan": preview["plan_revision"]},
            )
        self.assertEqual(receipt["outcome"], "indeterminate")
        self.assertTrue(native_has(preview["created_handles"]["new"]))
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "batch_records",
                {"operation": "rollback", "receipt_id": receipt["receipt_id"]},
            )


if __name__ == "__main__":
    unittest.main()
