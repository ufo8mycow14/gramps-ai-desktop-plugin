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
"""Check native imports and honest partial outcomes using disposable data."""

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
from gramps.gen.config import config
from gramps.gen.db import DbTxn
from gramps.gen.plug import BasePluginManager
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import native_imports
import support
import test.native_exports_test as exporter_fixtures


# -------------------------------------------------------------------------
# ReviewedImports
# -------------------------------------------------------------------------
class ReviewedImports(unittest.TestCase):
    """Use real import/export modules with synthetic family fixtures."""

    def setUp(self) -> None:
        """Reuse the existing synthetic exporter fixture and a separate destination."""
        exporter_fixtures.AdditionalExports.setUp(self)
        self.destination = SQLite()
        self.destination.load(":memory:")
        self.addCleanup(self.destination.close, update=False)
        state = SimpleNamespace(db=self.destination)
        self.target = support.GrampsSupport(
            SimpleNamespace(dbstate=state, windows=lambda: []), state
        )
        self.imports = native_imports.NativeImports(self.target, support.revision)

    def tearDown(self) -> None:
        """Close the source and remove disposable exported input."""
        exporter_fixtures.AdditionalExports.tearDown(self)

    def input_file(self, format_name: str, suffix: str) -> str:
        """Generate a real native import input from the synthetic source."""
        path = str(self.folder / (format_name + suffix))
        args = {"operation": "run", "format": format_name, "output_path": path}
        preview = self.service.dispatch("export", args)
        self.service.dispatch(
            "export", {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        )
        return path

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply only the just-reviewed native execution plan."""
        preview = self.imports.dispatch(args)
        return self.imports.dispatch(
            {**args, "apply": True, "expected_plan": preview["plan_revision"]}
        )

    def test_native_xml_gedcom_csv_geneweb(self) -> None:
        """Exercise each installed dedicated importer and inspect its result."""
        for format_name, suffix, ident in (
            ("xml", ".xml", "im_gramps"),
            ("gedcom", ".ged", "im_ged"),
            ("csv", ".csv", "im_csv"),
            ("geneweb", ".gw", "im_geneweb"),
        ):
            with self.subTest(importer=ident):
                destination = SQLite()
                destination.load(":memory:")
                state = SimpleNamespace(db=destination)
                target = support.GrampsSupport(
                    SimpleNamespace(dbstate=state, windows=lambda: []), state
                )
                service = native_imports.NativeImports(target, support.revision)
                try:
                    args = {
                        "operation": "run",
                        "importer_id": ident,
                        "file_path": self.input_file(format_name, suffix),
                        "prompt_policy": "accept",
                    }
                    preview = service.dispatch(args)
                    receipt = service.dispatch(
                        {
                            **args,
                            "apply": True,
                            "expected_plan": preview["plan_revision"],
                        }
                    )
                    self.assertIn(
                        receipt["outcome"], ("completed", "indeterminate"), receipt
                    )
                    self.assertEqual(receipt["after"]["counts"]["person"], 3)
                    self.assertFalse(destination.readonly)
                    self.assertTrue(receipt["database_changed"])
                finally:
                    destination.close(update=False)

    def test_restore_avoids_import_tags_and_preserves_native_ids(self) -> None:
        """Empty-tree XML restoration suppresses configured extra import tags."""
        path = self.input_file("xml", ".xml")
        original = config.get
        with patch.object(
            config,
            "get",
            side_effect=lambda key: (
                True if key == "preferences.tag-on-import" else original(key)
            ),
        ):
            receipt = self.execute({"operation": "restore", "file_path": path})
        self.assertEqual(receipt["outcome"], "completed", receipt)
        self.assertEqual(self.destination.get_number_of_tags(), 0)
        self.assertEqual(
            set(self.db.get_person_handles()),
            set(self.destination.get_person_handles()),
        )
        self.assertIsNone(self.destination.get_feature("skip-import-additions"))
        with self.assertRaises(ValueError):
            self.imports.dispatch({"operation": "restore", "file_path": path})

    def test_stale_input_preserves_empty_destination(self) -> None:
        """Changed file bytes invalidate review before native writes."""
        path = self.input_file("xml", ".xml")
        args = {"operation": "restore", "file_path": path}
        preview = self.imports.dispatch(args)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            self.imports.dispatch(
                {**args, "apply": True, "expected_plan": preview["plan_revision"]}
            )
        self.assertEqual(self.destination.get_number_of_people(), 0)

    def test_partial_failure_and_postwrite_diagnostic_receipts(self) -> None:
        """Committed changes retain receipts even when importer/readback/info fails."""
        path = self.input_file("xml", ".xml")
        for mode in ("exception", "readback", "info"):
            with self.subTest(failure=mode):
                invoked = False
                original_state = self.imports.state

                def cb_import(db: Any, filename: str, user: Any) -> Any:
                    """Commit a fixture before injecting a native failure."""
                    nonlocal invoked
                    person = lib.Person()
                    with DbTxn("Synthetic partial import", db) as txn:
                        db.add_person(person, txn)
                    invoked = True
                    user.warn("Synthetic warning", "Kept in receipt")
                    db.readonly = True
                    db.disable_signals()
                    if mode == "exception":
                        raise OSError("synthetic importer failure")

                    def cb_info() -> str:
                        """Inject an importer information formatting failure."""
                        raise ValueError("synthetic info failure")

                    return (
                        SimpleNamespace(info_text=cb_info) if mode == "info" else True
                    )

                def cb_state(maximum: int) -> dict[str, Any]:
                    """Fail only the post-import inspection."""
                    if invoked and mode == "readback":
                        raise ValueError("synthetic result inspection failure")
                    return original_state(maximum)

                manager = BasePluginManager.get_instance()
                with patch.object(
                    manager,
                    "load_plugin",
                    return_value=SimpleNamespace(importData=cb_import),
                ), patch.object(self.imports, "state", side_effect=cb_state):
                    receipt = self.execute(
                        {
                            "operation": "run",
                            "importer_id": "im_gramps",
                            "file_path": path,
                        }
                    )
                self.assertEqual(
                    receipt["outcome"],
                    "failed" if mode == "exception" else "indeterminate",
                )
                self.assertEqual(self.imports.receipts[receipt["receipt_id"]], receipt)
                self.assertTrue(receipt["warnings"])
                self.assertFalse(self.destination.readonly)
                self.assertFalse(
                    getattr(
                        self.destination, "_Callback__block_instance_signals", False
                    )
                )

    def test_rejected_native_prompt_receives_cancelled_result(self) -> None:
        """A reviewed rejection is recorded without implying successful import."""
        manager = BasePluginManager.get_instance()
        module = SimpleNamespace(
            importData=lambda db, filename, user: user.prompt("Synthetic", "Proceed?")
        )
        with patch.object(manager, "load_plugin", return_value=module):
            receipt = self.execute(
                {
                    "operation": "run",
                    "importer_id": "im_gramps",
                    "file_path": self.input_file("xml", ".xml"),
                }
            )
        self.assertEqual(receipt["outcome"], "cancelled")
        self.assertFalse(receipt["database_changed"])
