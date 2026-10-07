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
"""Check exact native tree lifecycle operations against disposable tree roots."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.gen.config import config
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import database_support
import support


# -------------------------------------------------------------------------
# NativeLifecycle
# -------------------------------------------------------------------------
class NativeLifecycle(unittest.TestCase):
    """Use native file creation with an isolated application state substitute."""

    def setUp(self) -> None:
        """Redirect only database-root reads to a disposable directory."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "trees"
        self.root.mkdir()
        original_get = config.get
        mock = patch.object(
            config,
            "get",
            side_effect=lambda key: (
                str(self.root) if key == "database.path" else original_get(key)
            ),
        )
        mock.start()
        self.addCleanup(mock.stop)
        self.state = SimpleNamespace(db=SQLite())
        self.state.is_open = lambda: self.state.db.is_open()
        self.addCleanup(self.close)
        manager = SimpleNamespace(open_activate=self.open, close_database=self.close)
        bridge = SimpleNamespace(
            dbstate=self.state,
            windows=lambda: [],
            uistate=SimpleNamespace(viewmanager=manager),
        )
        self.service = support.GrampsSupport(bridge, self.state)
        self.lifecycle = database_support.DatabaseSupport(
            self.service, support.revision
        )

    def open(self, path: str) -> None:
        """Load only the selected disposable tree through native SQLite."""
        self.close()
        self.state.db = SQLite()
        self.state.db.load(path)
        self.state.db.db_name = (Path(path) / "name.txt").read_text()

    def close(self) -> None:
        """Close only this test's active SQLite object."""
        if self.state.db.is_open():
            self.state.db.close(update=False)

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply only the matching reviewed tree lifecycle plan."""
        preview = self.lifecycle.dispatch(args)
        return self.lifecycle.dispatch(
            {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        )

    def create(self) -> Path:
        """Create a native tree without opening the application's state."""
        receipt = self.execute({"operation": "create", "name": "Synthetic lifecycle"})
        self.assertEqual(receipt["outcome"], "completed", receipt)
        self.assertFalse(self.state.is_open())
        return Path(receipt["created"]["path"])

    def test_create_open_reuse_close_rename_preserved_removal(self) -> None:
        """Complete the native file lifecycle and retain removed database bytes."""
        tree = self.create()
        listing = self.lifecycle.dispatch({"operation": "list"})
        self.assertEqual(listing["trees"][0]["path"], str(tree))
        receipt = self.execute({"operation": "open", "tree_path": str(tree)})
        self.assertEqual(receipt["outcome"], "completed", receipt)
        active = self.state.db
        self.execute({"operation": "open", "tree_path": str(tree)})
        self.assertIs(self.state.db, active)
        self.assertEqual(self.execute({"operation": "close"})["outcome"], "completed")
        self.assertEqual(
            self.execute(
                {
                    "operation": "rename",
                    "tree_path": str(tree),
                    "name": "Synthetic renamed",
                }
            )["outcome"],
            "completed",
        )
        before = (tree / "sqlite.db").read_bytes()
        preserve = self.folder / "preserved"
        receipt = self.execute(
            {
                "operation": "delete",
                "tree_path": str(tree),
                "preservation_path": str(preserve),
            }
        )
        self.assertEqual(receipt["outcome"], "completed", receipt)
        self.assertFalse(tree.exists())
        self.assertEqual((preserve / "sqlite.db").read_bytes(), before)
        self.assertEqual((preserve / "name.txt").read_text(), "Synthetic renamed")

    def test_changed_name_lock_and_outside_root_rejected(self) -> None:
        """Reject stale plans, locked tree removal and unregistered paths."""
        tree = self.create()
        args = {"operation": "rename", "tree_path": str(tree), "name": "Proposed"}
        preview = self.lifecycle.dispatch(args)
        (tree / "name.txt").write_text("Concurrent edit")
        with self.assertRaises(ValueError):
            self.lifecycle.dispatch(
                {**args, "apply": True, "expected_plan": preview["plan_revision"]}
            )
        (tree / "lock").write_text("synthetic@SYNTHETIC")
        with self.assertRaises(ValueError):
            self.lifecycle.dispatch(args)
        with self.assertRaises(ValueError):
            self.lifecycle.dispatch(
                {"operation": "open", "tree_path": str(self.folder)}
            )
        self.assertEqual((tree / "name.txt").read_text(), "Concurrent edit")

    def test_created_tree_retains_indeterminate_readback_receipt(self) -> None:
        """A successful creation cannot lose its receipt to a state-inspection error."""
        args = {"operation": "create", "name": "Synthetic uncertain"}
        preview = self.lifecycle.dispatch(args)
        before = self.lifecycle.state()
        with patch.object(
            self.lifecycle,
            "state",
            side_effect=[before, ValueError("synthetic readback failure")],
        ):
            receipt = self.lifecycle.dispatch(
                {**args, "apply": True, "expected_plan": preview["plan_revision"]}
            )
        self.assertEqual(receipt["outcome"], "indeterminate", receipt)
        self.assertTrue(receipt["created_state"]["database_file_exists"])
        self.assertEqual(self.lifecycle.receipts[receipt["receipt_id"]], receipt)

    def test_failed_creation_exposes_partial_tree_and_marker(self) -> None:
        """Native load failure reports preserved files and its residual lock."""

        def cb_load(path: str) -> None:
            """Leave a synthetic native marker before failing initialisation."""
            (Path(path) / "lock").write_text("synthetic@SYNTHETIC")
            raise OSError("synthetic load failure")

        database = SimpleNamespace(load=cb_load, is_open=lambda: False)
        with patch.object(database_support, "make_database", return_value=database):
            receipt = self.execute({"operation": "create", "name": "Synthetic partial"})
        self.assertEqual(receipt["outcome"], "failed")
        self.assertTrue(receipt["partial_changes"])
        self.assertTrue(receipt["created_state"]["exists"])
        self.assertTrue(receipt["created_state"]["locked"])
        self.assertTrue(Path(receipt["created"]["path"]).is_dir())

    def test_failed_creation_releases_owned_native_connection(self) -> None:
        """An early schema error closes the connection created by this operation."""
        database = SQLite()
        with patch.object(database, "_schema_exists", side_effect=OSError("synthetic early schema failure")), patch.object(database_support, "make_database", return_value=database):
            receipt = self.execute({"operation": "create", "name": "Synthetic early failure"})
        self.assertEqual(receipt["outcome"], "failed")
        tree = Path(receipt["created"]["path"])
        (tree / "sqlite.db").read_bytes()
        self.assertTrue(receipt["creation_cleanup"]["recovered"], receipt)
        self.assertFalse((tree / "lock").exists())
