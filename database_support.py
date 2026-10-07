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
"""Reviewed exact-path native tree lifecycle with preserved tree removal."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import hashlib
import os
from pathlib import Path
import time
from typing import Any
import uuid

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.cli.clidbman import CLIDbManager
from gramps.gen.config import config
from gramps.gen.db.utils import make_database
from gramps.gen.plug import BasePluginManager


def file_revision(path: Path) -> str:
    """Bind closed-tree files without publishing their contents."""
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


# -------------------------------------------------------------------------
# DatabaseSupport
# -------------------------------------------------------------------------
class DatabaseSupport:
    """Preserve native opening/closing and avoid regex-based tree deletion."""

    def __init__(self, support: Any, revision: Any) -> None:
        """Share the application's state and reviewed session receipts."""
        self.support, self.revision = support, revision
        self.receipts: dict[str, dict[str, Any]] = {}

    def state(self) -> dict[str, Any]:
        """Read the current native tree identity without opening another database."""
        state = self.support.dbstate
        opened = state.is_open()
        return {
            "open": opened,
            "path": str(Path(state.db.get_save_path()).resolve()) if opened else None,
            "name": state.db.get_dbname() if opened else None,
            "readonly": state.db.readonly if opened else None,
        }

    def dispatch(self, a: dict[str, Any]) -> dict[str, Any]:
        """Discover/preview/create/open/close/rename/remove a native tree."""
        operation = a.get("operation", "list")
        if operation == "receipt":
            if a.get("receipt_id") not in self.receipts:
                raise ValueError("Unknown native lifecycle receipt in this session")
            return self.receipts[a["receipt_id"]]
        root = Path(config.get("database.path")).resolve()
        if not root.is_dir():
            raise ValueError("Configured native database directory must already exist")
        manager = CLIDbManager(self.support.dbstate)
        trees = [
            {
                "name": row[0],
                "path": str(Path(row[1]).resolve()),
                "backend": row[7],
                "locked": manager.is_locked(row[1]),
                "needs_recovery": manager.needs_recovery(row[1]),
            }
            for row in manager.current_names
        ]
        backends = [
            {
                "id": plugin.id,
                "name": plugin.name,
                "dedicated_creation": plugin.id == "sqlite",
            }
            for plugin in BasePluginManager.get_instance().get_reg_databases()
        ]
        before = self.state()
        if operation in ("list", "status"):
            return {
                "trees": trees,
                "backends": backends,
                "current": before,
                "legacy_repair": "Unavailable: this native GUI repair route calls a missing restore method",
                "database_changed": False,
            }
        if operation not in ("create", "open", "close", "rename", "delete"):
            raise ValueError(
                "Select list, status, create, open, close, rename, delete or receipt"
            )
        target: dict[str, Any] = {}
        if operation in ("open", "rename", "delete"):
            value = a.get("tree_path", "")
            path = Path(value)
            if (
                not path.is_absolute()
                or path.is_symlink()
                or path.resolve().parent != root
            ):
                raise ValueError(
                    "Select an exact tree path from the configured native tree list"
                )
            target = next(
                (item for item in trees if item["path"] == str(path.resolve())), {}
            )
            if not target:
                raise ValueError("The exact native tree is no longer registered")
            if target["needs_recovery"]:
                raise ValueError(
                    "Native database recovery is required; normal lifecycle cannot bypass it"
                )
            if operation in ("rename", "delete") and (
                before["path"] == target["path"] or target["locked"]
            ):
                raise ValueError(
                    "Close the exact tree before renaming/removing it; active locks are preserved"
                )
        name: Any = a.get("name")
        backend = a.get("backend", "sqlite")
        if operation in ("create", "rename") and (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or "\n" in name
            or "\r" in name
        ):
            raise ValueError(
                "Use a nonempty single-line tree name up to 200 characters"
            )
        if operation == "create" and (
            backend != "sqlite" or not any(item["id"] == backend for item in backends)
        ):
            raise ValueError(
                "Dedicated creation currently supports installed SQLite; other backends use native setup"
            )
        preserve = Path()
        files = []
        if operation in ("rename", "delete"):
            folder = Path(target["path"])
            files = [
                {
                    "name": item.name,
                    "size": item.stat().st_size,
                    "revision": file_revision(item),
                }
                for item in sorted(folder.iterdir())
                if item.is_file()
            ]
            if any(item.is_symlink() for item in folder.rglob("*")):
                raise ValueError("Tree lifecycle must preserve unresolved linked files")
        if operation == "delete":
            preserve = Path(a.get("preservation_path", ""))
            if (
                not preserve.is_absolute()
                or preserve.exists()
                or not preserve.parent.is_dir()
                or preserve.resolve() == root
                or root in preserve.resolve().parents
                or preserve.anchor.casefold() != root.anchor.casefold()
            ):
                raise ValueError(
                    "Removal requires a fresh absolute preservation directory outside the tree root on the same volume"
                )
        plan_data = {
            "operation": operation,
            "root": str(root),
            "trees": trees,
            "before": before,
            "target": target or None,
            "name": name,
            "backend": backend if operation == "create" else None,
            "files": files,
            "preservation_path": str(preserve) if operation == "delete" else None,
            "limits": [
                "Opening may close the previous tree before a native load failure",
                "Other backend setup/migration and legacy repair use native interfaces",
            ],
        }
        plan = self.revision(plan_data)
        if not a.get("apply", False):
            return {**plan_data, "plan_revision": plan, "applied": False}
        if a.get("expected_plan") != plan:
            raise ValueError("Native tree lifecycle preview missing or changed")
        if self.support.dbstate is self.support.bridge.dbstate:
            dialogs = [
                window
                for window in self.support.bridge.windows()
                if window["type"] not in ("ApplicationWindow", "GtkTooltipWindow")
            ]
            if dialogs:
                raise ValueError(
                    "Finish native dialogs before a tree lifecycle operation"
                )
        created, error = None, None
        creation_cleanup = None
        try:
            if operation == "create":
                path, title = manager.create_new_db_cli(name, dbid=backend)
                created = {
                    "path": str(Path(path).resolve()),
                    "name": title,
                    "backend": backend,
                }
                database = make_database(backend)
                try:
                    database.load(path)
                finally:
                    if database.is_open():
                        database.close(update=False)
                    elif getattr(database, "dbapi", None) is not None:
                        database.dbapi.close()
                        from platform_paths import load_sibling

                        recovery = load_sibling(
                            "gramps_creation_locks", "lock_support.py"
                        )
                        creation_cleanup = recovery.recover(path, self.support.dbstate)
            elif operation == "open":
                if before["path"] != target["path"]:
                    self.support.bridge.uistate.viewmanager.open_activate(
                        target["path"]
                    )
            elif operation == "close":
                if before["open"]:
                    self.support.bridge.uistate.viewmanager.close_database()
            elif operation == "rename":
                if manager.is_locked(target["path"]):
                    raise ValueError("Tree became locked before renaming")
                path = Path(target["path"]) / "name.txt"
                temporary = path.with_name("name.plugin.tmp")
                temporary.write_text(name, encoding="utf-8")
                os.replace(temporary, path)
            else:
                if manager.is_locked(target["path"]):
                    raise ValueError("Tree became locked before removal")
                os.rename(target["path"], preserve)
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        diagnostics: list[str] = []
        try:
            after = self.state()
        except Exception as exc:
            after = None
            diagnostics.append(
                "Native state readback: " + type(exc).__name__ + ": " + str(exc)
            )
        success = error is None
        created_state = None
        try:
            if operation == "create" and created:
                created_path = Path(created["path"])
                created_state = {
                    "exists": created_path.is_dir(),
                    "database_file_exists": (created_path / "sqlite.db").is_file(),
                    "locked": manager.is_locked(created["path"]),
                }
                success = (
                    success
                    and created_state["database_file_exists"]
                    and not created_state["locked"]
                )
            elif operation == "open":
                success = (
                    success
                    and after is not None
                    and after["open"]
                    and after["path"] == target["path"]
                )
            elif operation == "close":
                success = success and after is not None and not after["open"]
            elif operation == "rename":
                success = (
                    success
                    and (Path(target["path"]) / "name.txt").read_text(encoding="utf-8")
                    == name
                )
            elif operation == "delete":
                success = (
                    success and not Path(target["path"]).exists() and preserve.is_dir()
                )
        except Exception as exc:
            diagnostics.append(
                "Lifecycle result readback: " + type(exc).__name__ + ": " + str(exc)
            )
        outcome = (
            "failed"
            if error
            else (
                "indeterminate" if diagnostics else "completed" if success else "failed"
            )
        )
        receipt = {
            "receipt_id": uuid.uuid4().hex,
            "created_at": time.time(),
            "operation": operation,
            "plan_revision": plan,
            "before": before,
            "after": after,
            "target": target or None,
            "created": created,
            "preservation_path": str(preserve) if operation == "delete" else None,
            "applied": True,
            "outcome": outcome,
            "error": error,
            "diagnostics": diagnostics,
            "created_state": created_state,
            "creation_cleanup": creation_cleanup,
            "partial_changes": outcome != "completed"
            and (created is not None or before != after),
            "record_undo": False,
        }
        self.receipts[receipt["receipt_id"]] = receipt
        while len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return receipt
