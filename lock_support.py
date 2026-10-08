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
"""Recover abandoned local Windows SQLite tree locks without bypassing owners."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Iterator

LOG = logging.getLogger(__name__)
PENDING: dict[Path, Any] = {}
HANDOFF: dict[Path, Any] = {}
HOOK_VERSION = "guarded-native-handoff-v4"
HOOK_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@contextmanager
def exclusive_files(paths: list[Path]) -> Iterator[None]:
    """Hold Windows handles that exclude existing readers/writers during recovery."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    handles = []
    try:
        for path in paths:
            handle = create(str(path), 0xC0000000, 0, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError(
                    ctypes.get_last_error(),
                    "Database file is in use or cannot be inspected",
                )
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def other_gramps_processes() -> list[int]:
    """Enumerate native Windows Gramps processes without terminating anything."""

    class ProcessEntry(ctypes.Structure):
        """Match the native PROCESSENTRY32W layout."""

        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    (
        kernel.CreateToolhelp32Snapshot.argtypes,
        kernel.CreateToolhelp32Snapshot.restype,
    ) = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        func = getattr(kernel, name)
        func.argtypes, func.restype = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry),
        ], wintypes.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [
        wintypes.HANDLE
    ], wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(
            ctypes.get_last_error(), "Cannot establish Gramps process ownership"
        )
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OSError(ctypes.get_last_error(), "Cannot enumerate Gramps processes")
        result = []
        while True:
            if (
                entry.szExeFile.lower() in ("grampsw.exe", "gramps.exe", "grampsd.exe")
                and entry.th32ProcessID != os.getpid()
            ):
                result.append(int(entry.th32ProcessID))
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:
                    raise OSError(
                        ctypes.get_last_error(), "Gramps process enumeration incomplete"
                    )
                return result
    finally:
        kernel.CloseHandle(snapshot)


def local_computer() -> str:
    """Read the native host name when packaged launches omit COMPUTERNAME."""
    configured = os.environ.get("COMPUTERNAME")
    if configured:
        return configured
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetComputerNameW.argtypes, kernel.GetComputerNameW.restype = [
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ], wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(256)
    length = wintypes.DWORD(len(buffer))
    if not kernel.GetComputerNameW(buffer, ctypes.byref(length)):
        raise OSError(
            ctypes.get_last_error(), "Cannot establish the local computer name"
        )
    return buffer.value


def file_owners(paths: list[Path]) -> list[int]:
    """Query Windows Restart Manager owners without stopping/restarting processes."""

    class UniqueProcess(ctypes.Structure):
        """Match RM_UNIQUE_PROCESS."""

        _fields_ = [("pid", wintypes.DWORD), ("start", wintypes.FILETIME)]

    class ProcessInfo(ctypes.Structure):
        """Match RM_PROCESS_INFO."""

        _fields_ = [
            ("process", UniqueProcess),
            ("name", wintypes.WCHAR * 256),
            ("service", wintypes.WCHAR * 64),
            ("type", ctypes.c_int),
            ("status", wintypes.ULONG),
            ("session", wintypes.DWORD),
            ("restartable", wintypes.BOOL),
        ]

    library = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    library.RmStartSession.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        wintypes.LPWSTR,
    ]
    library.RmRegisterResources.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_void_p,
    ]
    library.RmGetList.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(ProcessInfo),
        ctypes.POINTER(wintypes.DWORD),
    ]
    library.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"):
        getattr(library, name).restype = wintypes.DWORD
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    status = library.RmStartSession(ctypes.byref(session), 0, key)
    if status:
        raise OSError(status, "Cannot establish database file owners")
    try:
        resources = (wintypes.LPCWSTR * len(paths))(*(str(path) for path in paths))
        status = library.RmRegisterResources(
            session, len(paths), resources, 0, None, 0, None
        )
        if status:
            raise OSError(
                status, "Cannot register database files for ownership inspection"
            )
        needed, count, reason = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD()
        status = library.RmGetList(
            session,
            ctypes.byref(needed),
            ctypes.byref(count),
            None,
            ctypes.byref(reason),
        )
        if status == 0:
            return []
        if status != 234 or not 0 < needed.value <= 1024:
            raise OSError(status, "Database owner query was incomplete")
        owners = (ProcessInfo * needed.value)()
        count.value = needed.value
        status = library.RmGetList(
            session,
            ctypes.byref(needed),
            ctypes.byref(count),
            owners,
            ctypes.byref(reason),
        )
        if status:
            raise OSError(status, "Database owner query changed or was incomplete")
        return [int(owners[index].process.pid) for index in range(count.value)]
    finally:
        library.RmEndSession(session)


def recover(path: Path | str, dbstate: Any, reserve: bool = False) -> dict[str, Any]:
    """Remove only a provably unused local SQLite lock; otherwise preserve it."""
    tree = Path(path).resolve()
    lock = tree / "lock"
    result = {"path": str(tree), "recovered": False, "state": "unlocked"}
    if dbstate.is_open() and Path(dbstate.db.get_save_path()).resolve() == tree:
        return {**result, "state": "shared_open_tree"}
    if tree in PENDING:
        return {**result, "state": "reserved_for_native_open", "recovered": True}
    if not lock.exists():
        return result
    if os.name != "nt":
        return {
            **result,
            "state": "ownership_unverified",
            "reason": "Automatic recovery currently requires the Windows local-file ownership checks",
        }
    try:
        root = Path(tree.anchor)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes, kernel.GetDriveTypeW.restype = [
            wintypes.LPCWSTR
        ], wintypes.UINT
        if str(tree).startswith("\\\\") or kernel.GetDriveTypeW(str(root)) != 3:
            raise ValueError("Automatic recovery requires a local fixed drive")
        from gramps.gen.config import config

        configured = Path(config.get("database.path")).resolve()
        if tree.parent != configured or tree.is_symlink() or lock.is_symlink():
            raise ValueError(
                "Recover only a direct tree in the configured local database directory"
            )
        if (tree / "need_recover").exists():
            raise ValueError("Native database recovery is required before opening")
        if (tree / "database.txt").read_text(encoding="utf-8").strip() != "sqlite":
            raise ValueError("Automatic lock recovery currently supports SQLite only")
        expected = (
            os.environ.get("USERNAME", "") + "@" + os.environ.get("USERDOMAIN", "")
        )
        if (
            not os.environ.get("USERNAME")
            or os.environ.get("USERDOMAIN", "").casefold()
            != local_computer().casefold()
        ):
            raise ValueError("The lock's local user and host cannot be established")
        original = lock.read_bytes()
        if original.decode("utf-8").strip().casefold() != expected.casefold():
            raise ValueError("Lock belongs to a different or unknown user/host")
        stamp = lock.stat()
        others = other_gramps_processes()
        if others:
            return {
                **result,
                "state": "active_or_uncertain_owner",
                "other_gramps_pids": others,
            }
        files = [
            item
            for item in tree.iterdir()
            if item.is_file() and item.name not in ("lock", "name.txt", "database.txt")
        ]
        if not (tree / "sqlite.db").is_file() or any(
            item.is_symlink() for item in files
        ):
            raise ValueError("A regular SQLite database is required")
        if any(
            getattr(item.stat(), "st_file_attributes", 0) & 0x400
            for item in (tree, lock, *files)
        ):
            raise ValueError("Reparse targets require manual ownership review")
        owners = file_owners(files)
        if owners:
            return {
                **result,
                "state": "active_or_uncertain_owner",
                "owner_pids": owners,
            }
        guard = exclusive_files(files)
        guard.__enter__()
        retained = False
        try:
            current = lock.stat()
            if original != lock.read_bytes() or (
                stamp.st_ino,
                stamp.st_mtime_ns,
                stamp.st_size,
            ) != (current.st_ino, current.st_mtime_ns, current.st_size):
                raise ValueError("Lock changed during recovery")
            if other_gramps_processes():
                raise ValueError("Another Gramps process appeared during recovery")
            if reserve:
                PENDING[tree] = guard
                retained = True
            else:
                lock.unlink()
        finally:
            if not retained:
                guard.__exit__(None, None, None)
        LOG.info("Recovered abandoned Gramps SQLite lock at %s", tree)
        return {
            **result,
            "state": (
                "reserved_for_native_open" if reserve else "recovered_abandoned_lock"
            ),
            "recovered": True,
            "previous_lock": original.decode("utf-8").strip(),
        }
    except (OSError, ValueError, UnicodeError) as exc:
        return {**result, "state": "active_or_uncertain_owner", "reason": str(exc)}


def install(bridge: Any) -> None:
    """Integrate recovery with the native selected-tree lock prompt once."""
    from gi.repository import GLib, Gtk
    from gramps.gen.db import generic
    from gramps.cli.arghandler import ArgHandler
    from gramps.cli.grampscli import CLIManager
    from gramps.gui.dbman import DbManager, PATH_COL, ICON_COL, OPEN_COL

    original = DbManager._DbManager__ask_to_break_lock
    callbacks = (
        original,
        generic.write_lock_file,
        generic.clear_lock_file,
        generic.DbGeneric.load,
        ArgHandler.check_db,
        CLIManager._read_recent_file,
    )
    if all(
        getattr(callback, "_desktop_lock_recovery", None) == HOOK_VERSION
        and getattr(callback, "_desktop_lock_revision", None) == HOOK_REVISION
        and getattr(callback, "_desktop_lock_bridge", None) is bridge
        for callback in callbacks
    ):
        return
    if (
        PENDING
        or HANDOFF
        or any(
            getattr(callback, "_desktop_lock_recovery", False)
            and any(callback.__globals__.get(key) for key in ("PENDING", "HANDOFF"))
            for callback in callbacks
        )
    ):
        raise RuntimeError(
            "Finish the pending native tree opening before upgrading lock hooks"
        )

    def unwrap(callback: Any, name: str) -> Any:
        """Recover only this plugin's original callbacks during a live upgrade."""
        if not getattr(callback, "_desktop_lock_recovery", False):
            return callback
        if getattr(callback, "_desktop_lock_original", None) is not None:
            return callback._desktop_lock_original
        if callback.__name__.startswith("cb_") and getattr(
            callback, "__closure__", None
        ):
            values = dict(
                zip(
                    callback.__code__.co_freevars,
                    (cell.cell_contents for cell in callback.__closure__),
                )
            )
            return values.get(name, callback)
        return callback

    original = unwrap(original, "original")
    generic.write_lock_file = unwrap(generic.write_lock_file, "write_lock")
    generic.clear_lock_file = unwrap(generic.clear_lock_file, "clear_lock")
    generic.DbGeneric.load = unwrap(generic.DbGeneric.load, "load_database")
    ArgHandler.check_db = unwrap(ArgHandler.check_db, "original_check")
    CLIManager._read_recent_file = unwrap(CLIManager._read_recent_file, "read_recent")
    write_lock = generic.write_lock_file
    clear_lock = generic.clear_lock_file
    load_database = generic.DbGeneric.load

    def cb_write_lock(path: str) -> None:
        """Hand an exact reserved tree to the native writer before releasing guards."""
        tree = Path(path).resolve()
        guard = PENDING.pop(tree, None)
        try:
            write_lock(path)
            if guard is not None:
                marker = exclusive_files([tree / "lock"])
                marker.__enter__()
                HANDOFF[tree] = marker
        finally:
            if guard is not None:
                guard.__exit__(None, None, None)

    generic.write_lock_file = cb_write_lock

    def cb_clear_lock(path: str) -> None:
        """Release a handoff marker after native backend close before clearing it."""
        guard = HANDOFF.pop(Path(path).resolve(), None)
        if guard is not None:
            guard.__exit__(None, None, None)
        clear_lock(path)

    generic.clear_lock_file = cb_clear_lock

    def cb_load_database(
        database: Any, directory: str, *args: Any, **kwargs: Any
    ) -> None:
        """Retain the new marker until the reserved native database finishes opening."""
        tree = Path(directory).resolve()
        reserved = tree in PENDING or tree in HANDOFF
        try:
            load_database(database, directory, *args, **kwargs)
            if tree in HANDOFF:
                bridge.lock_recovery = {
                    "path": str(tree),
                    "recovered": True,
                    "state": (
                        "shared_open_tree"
                        if database.is_open() and not database.readonly
                        else "native_open_incomplete"
                    ),
                }
        except Exception:
            if reserved:
                bridge.lock_recovery = {
                    "path": str(tree),
                    "recovered": False,
                    "state": "native_open_failed",
                }
            raise
        finally:
            cb_expire(tree)
            guard = HANDOFF.pop(tree, None)
            if guard is not None:
                guard.__exit__(None, None, None)

    generic.DbGeneric.load = cb_load_database

    def cb_expire(tree: Path, expected: Any = None) -> bool:
        """Release abandoned reservations while preserving the original marker."""
        if expected is not None and PENDING.get(tree) is not expected:
            return False
        guard = PENDING.pop(tree, None)
        if guard is not None:
            guard.__exit__(None, None, None)
        return False

    def cb_recover_selected(manager: Any, store: Any, node: Any) -> None:
        """Resume the native opening loop only after verified abandoned recovery."""
        report = recover(store.get_value(node, PATH_COL), manager.dbstate, reserve=True)
        bridge.lock_recovery = report
        if report["recovered"]:
            reserved_tree = Path(report["path"])
            reservation = PENDING[reserved_tree]
            store.set_value(node, ICON_COL, "")
            store.set_value(node, OPEN_COL, False)

            def cb_continue() -> bool:
                """Continue native loading after the lock callback returns."""
                if not hasattr(manager, "selection"):
                    cb_expire(reserved_tree, reservation)
                    return False
                selected_store, selected_node = manager.selection.get_selected()
                if selected_node is None or Path(
                    selected_store.get_value(selected_node, PATH_COL)
                ).resolve() != Path(report["path"]):
                    cb_expire(reserved_tree, reservation)
                    store.set_value(node, ICON_COL, "gramps-lock")
                    return False
                manager.top.response(Gtk.ResponseType.OK)
                return False

            GLib.idle_add(cb_continue)
            GLib.timeout_add_seconds(30, cb_expire, reserved_tree, reservation)
        else:
            original(manager, store, node)

    DbManager._DbManager__ask_to_break_lock = cb_recover_selected
    original_check = ArgHandler.check_db

    def cb_check_db(handler: Any, path: str, force_unlock: bool = False) -> bool:
        """Recover the exact autoload target without passing native force-unlock."""
        if getattr(handler, "dbstate", None) is not bridge.dbstate:
            return original_check(handler, path, force_unlock)
        native_state: Any = bridge.dbstate
        if (
            native_state.is_open()
            and Path(native_state.db.get_save_path()).resolve() == Path(path).resolve()
        ):
            bridge.lock_recovery = {
                "path": str(Path(path).resolve()),
                "recovered": False,
                "state": "shared_open_tree",
            }
            return True
        if handler.dbman.is_locked(path):
            if handler.dbman.needs_recovery(path) or handler.dbman.backend_unavailable(
                path
            ):
                return original_check(handler, path, False)
            report = recover(path, bridge.dbstate, reserve=True)
            bridge.lock_recovery = report
            if report["recovered"]:
                reserved_tree = Path(report["path"])
                GLib.timeout_add_seconds(
                    30, cb_expire, reserved_tree, PENDING[reserved_tree]
                )
                return True
        return original_check(handler, path, False)

    ArgHandler.check_db = cb_check_db
    read_recent = CLIManager._read_recent_file

    def cb_read_recent(
        manager: Any,
        filename: str,
        username: str | None = None,
        password: str | None = None,
    ) -> Any:
        """Route one reserved GUI target through native load and verified post-load."""
        tree = Path(filename).resolve()
        if manager.dbstate is not bridge.dbstate:
            return read_recent(manager, filename, username, password)
        if (
            bridge.dbstate.is_open()
            and Path(bridge.dbstate.db.get_save_path()).resolve() == tree
        ):
            bridge.lock_recovery = {
                "path": str(tree),
                "recovered": False,
                "state": "shared_open_tree",
            }
            return None
        if tree not in PENDING and (tree / "lock").is_file():
            report = recover(tree, bridge.dbstate, reserve=True)
            bridge.lock_recovery = report
        if tree not in PENDING:
            return read_recent(manager, filename, username, password)
        try:
            manager.db_loader.read_file(filename, username, password)
            title_file = tree / "name.txt"
            try:
                title = title_file.read_text(encoding="utf-8").splitlines()[0].strip()
            except (OSError, UnicodeError, IndexError):
                title = filename
            if (
                not manager.dbstate.is_open()
                or Path(manager.dbstate.db.get_save_path()).resolve() != tree
            ):
                bridge.lock_recovery = {
                    "path": str(tree),
                    "recovered": False,
                    "state": "native_open_incomplete",
                }
                complete_gui = getattr(manager, "_post_load_newdb_gui", None)
                if callable(complete_gui):
                    complete_gui(filename, "x-directory/normal", title)
                return None
            manager._post_load_newdb(filename, "x-directory/normal", title)
            if manager.dbstate.db.readonly:
                bridge.lock_recovery = {
                    "path": str(tree),
                    "recovered": False,
                    "state": "native_open_readonly",
                }
            return None
        except Exception:
            bridge.lock_recovery = {
                "path": str(tree),
                "recovered": False,
                "state": "native_open_failed",
            }
            raise
        finally:
            cb_expire(tree)

    CLIManager._read_recent_file = cb_read_recent
    for callback, native in (
        (cb_recover_selected, original),
        (cb_write_lock, write_lock),
        (cb_clear_lock, clear_lock),
        (cb_load_database, load_database),
        (cb_check_db, original_check),
        (cb_read_recent, read_recent),
    ):
        setattr(callback, "_desktop_lock_recovery", HOOK_VERSION)
        setattr(callback, "_desktop_lock_revision", HOOK_REVISION)
        setattr(callback, "_desktop_lock_bridge", bridge)
        setattr(callback, "_desktop_lock_original", native)
