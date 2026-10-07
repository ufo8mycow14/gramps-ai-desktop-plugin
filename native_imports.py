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
"""Reviewed native file import with explicit execution limits and outcome receipts."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import time
from typing import Any, Iterator
import uuid

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.cli.user import User
from gramps.gen.config import config
from gramps.gen.plug import BasePluginManager

AUTOMATED = frozenset({"im_gramps", "im_ged", "im_csv", "im_geneweb"})
KINDS = (
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


def digest(path: Path) -> str:
    """Hash a local input without retaining its bytes in a receipt."""
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@contextmanager
def readable_input(path: Path) -> Iterator[None]:
    """On Windows hold a read-sharing lease excluding input writes/deletion."""
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [
        wintypes.HANDLE
    ], wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(
            ctypes.get_last_error(),
            "Cannot hold the reviewed import input against concurrent writes",
        )
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


# -------------------------------------------------------------------------
# NativeImports
# -------------------------------------------------------------------------
class NativeImports:
    """Bind importer, file, native settings and destination state before execution."""

    def __init__(self, support: Any, revision: Any) -> None:
        """Retain outcomes in the existing bridge session."""
        self.support, self.revision = support, revision
        self.receipts: dict[str, dict[str, Any]] = {}

    def state(self, maximum: int) -> dict[str, Any]:
        """Bind records and persistent import-relevant metadata without opening a tree."""
        db = self.support.db
        records: list[list[Any]] = []
        counts = {}
        for kind in KINDS:
            handles = sorted(getattr(db, "get_%s_handles" % kind)())
            counts[kind] = len(handles)
            for handle in handles:
                if len(records) >= maximum:
                    raise ValueError("Import inspection exceeds max_records")
                records.append(
                    [
                        kind,
                        handle,
                        self.support.snapshot(
                            kind, self.support.get(kind, handle=handle)
                        )["revision"],
                    ]
                )
        self.support.dispatch("batch", {"operation": "receipts"})
        metadata = {
            "home": db.get_default_handle(),
            "researcher": db.get_researcher().serialize(),
            "media_base": db.get_mediapath(),
            "name_formats": db.name_formats,
            "name_groups": {
                key: db.get_name_group_mapping(key) for key in db.get_name_group_keys()
            },
            "bookmarks": {
                key: getattr(db, key).get()
                for key in (
                    "bookmarks",
                    "family_bookmarks",
                    "event_bookmarks",
                    "place_bookmarks",
                    "source_bookmarks",
                    "citation_bookmarks",
                    "repo_bookmarks",
                    "media_bookmarks",
                    "note_bookmarks",
                )
            },
        }
        return {
            "tree": self.support.batches.context(),
            "counts": counts,
            "revision": self.revision({"records": records, "metadata": metadata}),
        }

    def dispatch(self, a: dict[str, Any]) -> dict[str, Any]:
        """Discover/preview/import, restore into an empty tree, or read receipts."""
        manager = BasePluginManager.get_instance()
        registered = {plugin.id: plugin for plugin in manager.get_reg_importers()}
        operation = a.get("operation", "list")
        if operation == "list":
            return {
                "importers": [
                    {
                        "id": plugin.id,
                        "name": plugin.name,
                        "extension": plugin.extension,
                        "dedicated": plugin.id in AUTOMATED,
                        "route": (
                            "reviewed import"
                            if plugin.id in AUTOMATED
                            else "native import dialog"
                        ),
                        "options": [],
                        "preview": "Execution plan; native importers do not offer a generic dry run",
                    }
                    for plugin in registered.values()
                ],
                "database_changed": False,
            }
        if operation == "receipt":
            if a.get("receipt_id") not in self.receipts:
                raise ValueError("Unknown import receipt in this session")
            return self.receipts[a["receipt_id"]]
        if operation not in ("run", "restore"):
            raise ValueError("Select list, run, restore or receipt")
        ident = a.get("importer_id", "im_gramps" if operation == "restore" else None)
        if ident not in registered or ident not in AUTOMATED:
            raise ValueError(
                "Select a dedicated installed importer; other importers use the native dialog"
            )
        if operation == "restore" and ident != "im_gramps":
            raise ValueError(
                "Native restoration requires XML in an empty destination tree"
            )
        path = Path(a.get("file_path", ""))
        maximum = a.get("max_records", 50000)
        if type(maximum) is not int or not 1 <= maximum <= 200000:
            raise ValueError("max_records must be between 1 and 200000")
        if (
            not path.is_absolute()
            or not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > 1024 * 1024 * 1024
        ):
            raise ValueError("Select an existing absolute local input file up to 1 GiB")
        expected_suffixes = {"." + registered[ident].extension.lower()}
        if ident == "im_gramps":
            expected_suffixes.add(".xml")
        if path.suffix.lower() not in expected_suffixes:
            raise ValueError(
                "Input extension does not match the selected native importer"
            )
        policy = a.get("prompt_policy", "reject")
        if policy not in ("reject", "accept"):
            raise ValueError("prompt_policy must be reject or accept")
        before = self.state(maximum)
        if operation == "restore" and any(before["counts"].values()):
            raise ValueError(
                "Restore requires an empty destination; existing records must be preserved"
            )
        settings = {
            key: config.get(key)
            for key in ("preferences.tag-on-import", "preferences.tag-on-import-format")
        }
        settings["skip-import-additions"] = self.support.db.get_feature(
            "skip-import-additions"
        )
        plan_data = {
            "operation": operation,
            "importer_id": ident,
            "file_path": str(path.resolve()),
            "sha256": digest(path),
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "destination": before,
            "settings": settings,
            "prompt_policy": policy,
            "preview_kind": "execution_plan",
            "limits": [
                "Native importer determines changes during execution",
                "Imports may discard undo and change tree metadata",
                "No atomic import rollback; inspect partial outcomes before retrying",
            ],
            "input_lease": (
                "Windows denies concurrent writes/deletion during import"
                if os.name == "nt"
                else "Hash checked before/after; concurrent file writers cannot be excluded"
            ),
        }
        plan = self.revision(plan_data)
        preview = {**plan_data, "plan_revision": plan, "applied": False}
        if not a.get("apply", False):
            return preview
        if a.get("expected_plan") != plan:
            raise ValueError("Import execution plan missing or changed")
        self.support.writable()
        db = self.support.db
        readonly = db.readonly
        blocked = getattr(db, "_Callback__block_instance_signals", False)
        errors, prompts, returned, exception = [], [], None, None
        warnings: list[list[str]] = []
        information: list[list[str]] = []
        skip_additions = db.get_feature("skip-import-additions")
        diagnostics: list[str] = []
        started, input_changed = False, None
        user = User(
            quiet=True, error=lambda *args: errors.append([str(arg) for arg in args])
        )

        def cb_prompt(*args: Any, **kwargs: Any) -> bool:
            """Capture native prompts and use only the reviewed prompt policy."""
            prompts.append(
                {"text": [str(arg) for arg in args], "accepted": policy == "accept"}
            )
            return policy == "accept"

        user.prompt = cb_prompt
        user.warn = lambda *args, **kwargs: warnings.append([str(arg) for arg in args])
        user.info = lambda *args, **kwargs: information.append(
            [str(arg) for arg in args]
        )
        module = manager.load_plugin(registered[ident])
        if module is None:
            raise ValueError("Selected native importer could not load")
        try:
            with readable_input(path):
                if digest(path) != plan_data["sha256"] or self.state(maximum) != before:
                    raise ValueError(
                        "Import input or destination changed before execution"
                    )
                started = True
                try:
                    if operation == "restore":
                        db.set_feature("skip-import-additions", True)
                    returned = getattr(module, registered[ident].import_function)(
                        db, str(path), user
                    )
                except Exception as exc:
                    exception = type(exc).__name__ + ": " + str(exc)
                try:
                    input_changed = digest(path) != plan_data["sha256"]
                except Exception as exc:
                    diagnostics.append(
                        "Input readback: " + type(exc).__name__ + ": " + str(exc)
                    )
        finally:
            for label, cb_restore in (
                ("Readonly restoration", lambda: setattr(db, "readonly", readonly)),
                (
                    "Import feature restoration",
                    lambda: db.set_feature("skip-import-additions", skip_additions),
                ),
                (
                    "Signal restoration",
                    db.disable_signals if blocked else db.enable_signals,
                ),
                ("Native rebuild", (lambda: None) if blocked else db.request_rebuild),
            ):
                try:
                    cb_restore()
                except Exception as exc:
                    if not started:
                        raise
                    diagnostics.append(
                        label + ": " + type(exc).__name__ + ": " + str(exc)
                    )
        try:
            after = self.state(maximum + 200000)
        except Exception as exc:
            after = None
            diagnostics.append(
                "Destination readback: " + type(exc).__name__ + ": " + str(exc)
            )
        changed = before["revision"] != after["revision"] if after is not None else None
        if exception or errors:
            outcome = "failed"
        elif any(not item["accepted"] for item in prompts):
            outcome = "cancelled"
        elif returned is False:
            outcome = "failed"
        elif returned is None or input_changed is not False or diagnostics:
            outcome = "indeterminate"
        else:
            outcome = "completed"
        info = None
        if returned is not None and hasattr(returned, "info_text"):
            try:
                info = returned.info_text()
            except Exception as exc:
                diagnostics.append(
                    "Importer information: " + type(exc).__name__ + ": " + str(exc)
                )
                if outcome == "completed":
                    outcome = "indeterminate"
        receipt = {
            "receipt_id": uuid.uuid4().hex,
            "created_at": time.time(),
            "plan_revision": plan,
            "importer_id": ident,
            "applied": True,
            "outcome": outcome,
            "before": before,
            "after": after,
            "database_changed": changed,
            "partial_changes": (
                changed and outcome != "completed" if changed is not None else None
            ),
            "errors": errors,
            "warnings": warnings,
            "information": information,
            "exception": exception,
            "diagnostics": diagnostics,
            "prompts": prompts,
            "input_changed_during_import": input_changed,
            "info": info,
            "rollback_available": False,
        }
        self.receipts[receipt["receipt_id"]] = receipt
        while len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return receipt
