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
"""Check Windows lock ownership against disposable files and mocked tree state."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import lock_support


# -------------------------------------------------------------------------
# AbandonedLocks
# -------------------------------------------------------------------------
@unittest.skipUnless(os.name == "nt", "Windows file-sharing ownership contract")
class AbandonedLocks(unittest.TestCase):
    """Exercise real Windows file sharing without accessing a live tree."""

    def setUp(self) -> None:
        """Create an independent local fake database and user/host context."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        (self.tree / "database.txt").write_text("sqlite")
        (self.tree / "sqlite.db").write_bytes(b"synthetic file ownership fixture")
        self.lock = self.tree / "lock"
        self.lock.write_text("synthetic@SYNTHETIC")
        self.state = SimpleNamespace(is_open=lambda: False)
        self.config = SimpleNamespace(get=lambda key: str(self.root))
        self.mocks = [
            patch.dict(
                os.environ,
                {
                    "USERNAME": "synthetic",
                    "USERDOMAIN": "SYNTHETIC",
                    "COMPUTERNAME": "SYNTHETIC",
                },
            ),
            patch.dict(
                sys.modules, {"gramps.gen.config": SimpleNamespace(config=self.config)}
            ),
            patch.object(lock_support, "other_gramps_processes", return_value=[]),
        ]
        for mock in self.mocks:
            mock.start()

    def tearDown(self) -> None:
        """Release plugin-owned test handles and remove only temporary fixtures."""
        for mapping in (lock_support.PENDING, lock_support.HANDOFF):
            guard = mapping.pop(self.tree, None)
            if guard is not None:
                guard.__exit__(None, None, None)
        for mock in reversed(self.mocks):
            mock.stop()
        self.temporary.cleanup()

    def test_abandoned_local_lock_recovered(self) -> None:
        """Only the stale marker is removed; database bytes remain identical."""
        before = (self.tree / "sqlite.db").read_bytes()
        report = lock_support.recover(self.tree, self.state)
        self.assertTrue(report["recovered"])
        self.assertFalse(self.lock.exists())
        self.assertEqual((self.tree / "sqlite.db").read_bytes(), before)

    def test_active_file_and_other_process_preserved(self) -> None:
        """A native open handle or another Gramps process prevents recovery."""
        with (self.tree / "sqlite.db").open("rb"):
            self.assertFalse(lock_support.recover(self.tree, self.state)["recovered"])
            self.assertTrue(self.lock.exists())
        with patch.object(lock_support, "other_gramps_processes", return_value=[123]):
            report = lock_support.recover(self.tree, self.state)
            self.assertEqual(report["other_gramps_pids"], [123])
            self.assertTrue(self.lock.exists())

    def test_shared_open_tree_and_foreign_locks_preserved(self) -> None:
        """Reuse the shared open tree and retain unverifiable foreign markers."""
        state = SimpleNamespace(
            is_open=lambda: True,
            db=SimpleNamespace(get_save_path=lambda: str(self.tree)),
        )
        self.assertEqual(
            lock_support.recover(self.tree, state)["state"], "shared_open_tree"
        )
        self.assertTrue(self.lock.exists())
        self.lock.write_text("someone@OTHERHOST")
        self.assertFalse(lock_support.recover(self.tree, self.state)["recovered"])
        self.assertEqual(self.lock.read_text(), "someone@OTHERHOST")

    def test_reservation_keeps_marker_and_excludes_writers(self) -> None:
        """Hold data files until the native opening handoff establishes its marker."""
        report = lock_support.recover(self.tree, self.state, reserve=True)
        self.assertTrue(report["recovered"])
        self.assertTrue(self.lock.exists())
        with self.assertRaises(OSError):
            (self.tree / "sqlite.db").open("rb")
        guard = lock_support.PENDING.pop(self.tree)
        guard.__exit__(None, None, None)
        self.assertEqual(
            (self.tree / "sqlite.db").read_bytes(), b"synthetic file ownership fixture"
        )

    def test_uncertain_owner_and_native_recovery_preserved(self) -> None:
        """Reject unavailable owner queries and native recovery prerequisites."""
        with patch.object(
            lock_support, "file_owners", side_effect=OSError("uncertain owners")
        ):
            self.assertFalse(lock_support.recover(self.tree, self.state)["recovered"])
            self.assertTrue(self.lock.exists())
        (self.tree / "need_recover").touch()
        self.assertFalse(lock_support.recover(self.tree, self.state)["recovered"])
        self.assertTrue(self.lock.exists())


# -------------------------------------------------------------------------
# NativeLockHooks
# -------------------------------------------------------------------------
@unittest.skipUnless(os.name == "nt", "Windows native lock handoff")
class NativeLockHooks(unittest.TestCase):
    """Exercise exact opening hooks without touching the application state."""

    def setUp(self) -> None:
        """Install hooks against disposable native-interface substitutes."""
        AbandonedLocks.setUp(self)
        self.idles: list[Any] = []
        self.timers: list[Any] = []
        fixture = self

        class DbGeneric:
            """Represent the native load/write ordering."""

            def __init__(self) -> None:
                """Start closed with controllable load outcomes."""
                self.opened, self.readonly = False, False
                self.failure = False

            def is_open(self) -> bool:
                """Return the synthetic database state."""
                return self.opened

            def get_save_path(self) -> str:
                """Return the exact disposable tree."""
                return str(fixture.tree)

            def load(self, directory: str) -> None:
                """Write the marker, release data guards, then finish opening."""
                fixture.generic.write_lock_file(directory)
                with fixture.assertRaises(OSError):
                    fixture.lock.read_bytes()
                (fixture.tree / "sqlite.db").read_bytes()
                if self.failure:
                    raise OSError("injected native loading failure")
                self.opened = True

        class DbManager:
            """Provide the native lock-prompt method for wrapping."""

            def _DbManager__ask_to_break_lock(self, store: Any, node: Any) -> None:
                """Record a manual native prompt."""
                fixture.prompt()

        class ArgHandler:
            """Provide the native autoload check for wrapping."""

            def check_db(self, path: str, force_unlock: bool = False) -> bool:
                """Record whether an unsafe native force option was passed."""
                fixture.checked(force_unlock)
                return not fixture.lock.exists()

        class CLIManager:
            """Provide the native recent-file method for wrapping."""

            def _read_recent_file(
                self,
                filename: str,
                username: str | None = None,
                password: str | None = None,
            ) -> None:
                """Record fallback to native recent-file behaviour."""
                fixture.recent()

        def cb_write_lock(directory: str) -> None:
            """Emulate the native marker writer without removing the old marker."""
            (Path(directory) / "lock").write_text("synthetic@SYNTHETIC")

        def cb_clear_lock(directory: str) -> None:
            """Emulate native marker deletion after closing the backend."""
            (Path(directory) / "lock").unlink(missing_ok=True)

        self.generic = SimpleNamespace(
            DbGeneric=DbGeneric,
            write_lock_file=cb_write_lock,
            clear_lock_file=cb_clear_lock,
        )
        self.prompt, self.checked, self.recent, self.post = (
            Mock(),
            Mock(),
            Mock(),
            Mock(),
        )
        self.manager_class, self.handler_class, self.cli_class = (
            DbManager,
            ArgHandler,
            CLIManager,
        )
        self.database = DbGeneric()
        self.state = SimpleNamespace(db=self.database, is_open=self.database.is_open)
        self.bridge = SimpleNamespace(dbstate=self.state, lock_recovery=None)
        self.glib = SimpleNamespace(
            idle_add=lambda callback: self.idles.append(callback),
            timeout_add_seconds=lambda seconds, callback, *args: self.timers.append(
                (callback, args)
            ),
        )
        native = {
            "gi.repository": SimpleNamespace(
                GLib=self.glib, Gtk=SimpleNamespace(ResponseType=SimpleNamespace(OK=1))
            ),
            "gramps.gen.db": SimpleNamespace(generic=self.generic),
            "gramps.cli.arghandler": SimpleNamespace(ArgHandler=ArgHandler),
            "gramps.cli.grampscli": SimpleNamespace(CLIManager=CLIManager),
            "gramps.gui.dbman": SimpleNamespace(
                DbManager=DbManager, PATH_COL=1, ICON_COL=2, OPEN_COL=3
            ),
        }
        mock = patch.dict(sys.modules, native)
        mock.start()
        self.mocks.append(mock)
        lock_support.install(self.bridge)
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

    def tearDown(self) -> None:
        """Restore imported modules and release all disposable handles."""
        AbandonedLocks.tearDown(self)

    def test_recent_open_handoff_and_shared_tree_reuse(self) -> None:
        """Open an abandoned tree and reuse it without a second native load."""
        self.cli_class._read_recent_file(self.manager, str(self.tree))
        self.assertTrue(self.database.opened)
        self.assertEqual(self.bridge.lock_recovery["state"], "shared_open_tree")
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.assertEqual(self.lock.read_text(), "synthetic@SYNTHETIC")
        self.post.assert_called_once()
        self.cli_class._read_recent_file(self.manager, str(self.tree))
        self.assertTrue(self.handler_class.check_db(self.handler, str(self.tree)))
        self.post.assert_called_once()
        self.recent.assert_not_called()
        self.checked.assert_not_called()

    def test_failed_load_releases_marker_and_data_guards(self) -> None:
        """Native failure receives an honest state and retains a usable marker."""
        self.database.failure = True
        with self.assertRaises(OSError):
            self.cli_class._read_recent_file(self.manager, str(self.tree))
        self.assertEqual(self.bridge.lock_recovery["state"], "native_open_failed")
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.lock.read_bytes()
        (self.tree / "sqlite.db").read_bytes()
        self.post.assert_not_called()

    def test_cancelled_load_releases_reservation(self) -> None:
        """A native cancellation before the writer preserves the original lock."""
        self.manager.db_loader.read_file = lambda *args: None
        self.cli_class._read_recent_file(self.manager, str(self.tree))
        self.assertEqual(self.bridge.lock_recovery["state"], "native_open_incomplete")
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.assertEqual(self.lock.read_text(), "synthetic@SYNTHETIC")
        self.post.assert_not_called()

    def test_autoload_preserves_active_owner_even_with_force_flag(self) -> None:
        """Automatic access never forwards force-unlock for an uncertain owner."""
        with patch.object(lock_support, "other_gramps_processes", return_value=[123]):
            self.assertFalse(
                self.handler_class.check_db(self.handler, str(self.tree), True)
            )
        self.checked.assert_called_once_with(False)
        self.assertTrue(self.lock.exists())

    def test_autoload_reservation_expiry_and_repeat(self) -> None:
        """Repeated checks retain one guard and cancellation expires it safely."""
        self.assertTrue(self.handler_class.check_db(self.handler, str(self.tree)))
        guard = lock_support.PENDING[self.tree]
        self.assertTrue(self.handler_class.check_db(self.handler, str(self.tree)))
        self.assertIs(lock_support.PENDING[self.tree], guard)
        callback, args = self.timers[-1]
        callback(*args)
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.assertTrue(self.lock.exists())
        (self.tree / "sqlite.db").read_bytes()

    def test_install_repairs_partial_hook_upgrade(self) -> None:
        """A current marker cannot hide a missing recent-file hook."""
        current = self.cli_class._read_recent_file
        native_check = self.handler_class.check_db._desktop_lock_original
        lock_support.install(self.bridge)
        self.assertIs(self.cli_class._read_recent_file, current)
        original = current._desktop_lock_original
        self.cli_class._read_recent_file = original
        lock_support.install(self.bridge)
        self.assertIs(self.cli_class._read_recent_file._desktop_lock_original, original)
        self.assertEqual(
            self.cli_class._read_recent_file._desktop_lock_recovery,
            lock_support.HOOK_VERSION,
        )
        self.assertIs(self.handler_class.check_db._desktop_lock_original, native_check)

    def test_native_close_can_clear_handoff_marker(self) -> None:
        """Native version/upgrade refusal can close without leaving our marker guard."""
        lock_support.recover(self.tree, self.state, reserve=True)
        self.generic.write_lock_file(str(self.tree))
        self.generic.clear_lock_file(str(self.tree))
        self.assertFalse(self.lock.exists())
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)

    def test_same_bridge_refreshes_changed_source(self) -> None:
        """Source revisions replace callbacks without stacking native wrappers."""
        current = self.cli_class._read_recent_file
        native = current._desktop_lock_original
        with patch.object(lock_support, "HOOK_REVISION", "synthetic-new-source"):
            lock_support.install(self.bridge)
            refreshed = self.cli_class._read_recent_file
            self.assertIsNot(refreshed, current)
            self.assertIs(refreshed._desktop_lock_original, native)
            self.assertEqual(refreshed._desktop_lock_revision, "synthetic-new-source")
            lock_support.install(self.bridge)
            self.assertIs(self.cli_class._read_recent_file, refreshed)

    def test_install_repairs_missing_manager_hook(self) -> None:
        """A missing first hook cannot leave other native wrappers nested."""
        native_recent = self.cli_class._read_recent_file._desktop_lock_original
        native_load = self.generic.DbGeneric.load._desktop_lock_original
        self.manager_class._DbManager__ask_to_break_lock = (
            self.manager_class._DbManager__ask_to_break_lock._desktop_lock_original
        )
        lock_support.install(self.bridge)
        self.assertIs(
            self.cli_class._read_recent_file._desktop_lock_original, native_recent
        )
        self.assertIs(self.generic.DbGeneric.load._desktop_lock_original, native_load)

    def test_fresh_generation_preserves_pending_and_handoff(self) -> None:
        """A refresh cannot discard guards owned by the installed callbacks."""
        from platform_paths import load_sibling

        fresh = load_sibling("synthetic_lock_hook_refresh", "lock_support.py")
        fresh.HOOK_REVISION = "synthetic-new-source"
        current = self.cli_class._read_recent_file
        lock_support.recover(self.tree, self.state, reserve=True)
        pending = lock_support.PENDING[self.tree]
        with self.assertRaisesRegex(RuntimeError, "pending native tree opening"):
            fresh.install(self.bridge)
        self.assertIs(self.cli_class._read_recent_file, current)
        self.assertIs(lock_support.PENDING[self.tree], pending)
        self.generic.write_lock_file(str(self.tree))
        handoff = lock_support.HANDOFF[self.tree]
        with self.assertRaisesRegex(RuntimeError, "pending native tree opening"):
            fresh.install(self.bridge)
        self.assertIs(self.cli_class._read_recent_file, current)
        self.assertIs(lock_support.HANDOFF[self.tree], handoff)
        self.generic.clear_lock_file(str(self.tree))
        fresh.install(self.bridge)
        self.assertIsNot(self.cli_class._read_recent_file, current)

    def test_manager_cancel_before_idle_releases_guard(self) -> None:
        """A destroyed selection cancels the queued opening without a leaked guard."""
        store = SimpleNamespace(
            get_value=lambda node, col: str(self.tree), set_value=Mock()
        )
        manager = SimpleNamespace(dbstate=self.state)
        self.manager_class._DbManager__ask_to_break_lock(manager, store, "node")
        self.assertEqual(len(self.idles), 1)
        self.idles[0]()
        self.assertFalse(lock_support.PENDING or lock_support.HANDOFF)
        self.assertTrue(self.lock.exists())

    def test_expired_timer_cannot_release_new_reservation(self) -> None:
        """A delayed old cancellation cannot remove a later opening guard."""
        self.handler_class.check_db(self.handler, str(self.tree))
        callback, args = self.timers[-1]
        callback(*args)
        self.handler_class.check_db(self.handler, str(self.tree))
        current = lock_support.PENDING[self.tree]
        callback(*args)
        self.assertIs(lock_support.PENDING[self.tree], current)


if __name__ == "__main__":
    unittest.main()
