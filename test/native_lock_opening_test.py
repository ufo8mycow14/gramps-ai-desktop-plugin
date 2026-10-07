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
"""Verify automatic lock opening through real native disposable SQLite trees."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.cli.arghandler import ArgHandler
from gramps.cli.grampscli import CLIManager
from gramps.gen.config import config
from gramps.gen.db import generic
from gramps.gui.dbman import DbManager
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import lock_support


# -------------------------------------------------------------------------
# NativeOpening
# -------------------------------------------------------------------------
@unittest.skipUnless(os.name == "nt", "Windows native opening contract")
class NativeOpening(unittest.TestCase):
    """Run opening and failure paths while preserving the live application's hooks."""

    def setUp(self) -> None:
        """Create a native tree and patch only this test's tree-root lookup."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        (self.tree / "database.txt").write_text("sqlite")
        (self.tree / "name.txt").write_text("Synthetic lock fixture")
        initial = SQLite()
        initial.load(str(self.tree))
        initial.close(update=False)
        self.lock = self.tree / "lock"
        self.lock.write_text(os.environ["USERNAME"] + "@" + os.environ["USERDOMAIN"])
        self.database = SQLite()
        self.state = SimpleNamespace(db=self.database, is_open=self.database.is_open)
        self.bridge = SimpleNamespace(dbstate=self.state, lock_recovery=None)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for owner, key in (
            (DbManager, "_DbManager__ask_to_break_lock"),
            (ArgHandler, "check_db"),
            (CLIManager, "_read_recent_file"),
            (generic, "write_lock_file"),
            (generic, "clear_lock_file"),
            (generic.DbGeneric, "load"),
        ):
            callback = getattr(owner, key)
            self.stack.enter_context(
                patch.object(
                    owner, key, getattr(callback, "_desktop_lock_original", callback)
                )
            )
        original_get = config.get
        self.stack.enter_context(
            patch.object(
                config,
                "get",
                side_effect=lambda key: (
                    str(self.root) if key == "database.path" else original_get(key)
                ),
            )
        )
        lock_support.install(self.bridge)
        self.post = Mock()
        self.manager = SimpleNamespace(
            dbstate=self.state,
            db_loader=SimpleNamespace(
                read_file=lambda path, *args: self.database.load(path)
            ),
            _post_load_newdb=self.post,
        )
        self.handler = SimpleNamespace(
            dbstate=self.state,
            dbman=SimpleNamespace(
                is_locked=lambda path: self.lock.exists(),
                needs_recovery=lambda path: False,
                backend_unavailable=lambda path: False,
            ),
        )
        self.addCleanup(self.release)

    def release(self) -> None:
        """Close only the disposable database and release its outstanding guards."""
        if self.database.is_open():
            self.database.close(update=False)
        for mapping in (lock_support.PENDING, lock_support.HANDOFF):
            guard = mapping.pop(self.tree, None)
            if guard is not None:
                guard.__exit__(None, None, None)

    def test_autoload_then_recent_native_open(self) -> None:
        """Reserved autoload reaches native SQLite and retains its live marker."""
        self.assertTrue(ArgHandler.check_db(self.handler, str(self.tree)))
        self.assertTrue(self.lock.exists())
        CLIManager._read_recent_file(self.manager, str(self.tree))
        self.assertTrue(self.database.is_open())
        self.assertFalse(self.database.readonly)
        self.assertEqual(self.bridge.lock_recovery["state"], "shared_open_tree")
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.assertTrue(self.lock.exists())
        self.post.assert_called_once()
        CLIManager._read_recent_file(self.manager, str(self.tree))
        self.post.assert_called_once()

    def test_backend_failure_releases_native_handoff(self) -> None:
        """Failure after marker writing releases both guards without claiming success."""
        with patch.object(
            self.database, "_initialize", side_effect=OSError("synthetic load failure")
        ):
            with self.assertRaises(OSError):
                CLIManager._read_recent_file(self.manager, str(self.tree))
        self.assertEqual(self.bridge.lock_recovery["state"], "native_open_failed")
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.lock.read_bytes()
        (self.tree / "sqlite.db").read_bytes()

    def test_native_schema_refusal_clears_own_marker(self) -> None:
        """Native schema refusal closes before clearing our reserved marker."""
        original = self.database._get_metadata

        def cb_metadata(key: str, *args: object, **kwargs: object) -> object:
            """Present a synthetic unsupported schema only to native load."""
            return (
                self.database.VERSION[0] + 1
                if key == "version"
                else original(key, *args, **kwargs)
            )

        with patch.object(self.database, "_get_metadata", side_effect=cb_metadata):
            with self.assertRaises(Exception):
                CLIManager._read_recent_file(self.manager, str(self.tree))
        self.assertFalse(self.database.is_open())
        self.assertFalse(self.lock.exists())
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
