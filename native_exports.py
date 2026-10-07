"""Reviewed native whole-tree exports and portable XML/media backups."""

import copy
from datetime import date
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlsplit

from gramps.gen.plug import BasePluginManager
from gramps.gen.utils.file import media_path_full

FORMATS = {
    "gramps": ("ex_gramps", ".gramps"),
    "xml": ("ex_gramps", ".xml"),
    "gedcom": ("ex_ged", ".ged"),
    "gpkg": ("ex_gpkg", ".gpkg"),
    "csv": ("ex_csv", ".csv"),
    "web_family_tree": ("ex_webfamtree", ".wft"),
    "geneweb": ("ex_geneweb", ".gw"),
    "vcalendar": ("ex_vcal", ".vcs"),
    "vcard": ("ex_vcard", ".vcf"),
}
LOSSLESS = frozenset({"gramps", "xml", "gpkg"})
CSV_OPTIONS = (
    "include_individuals",
    "include_marriages",
    "include_children",
    "include_places",
    "translate_headers",
)
LOSSES = {
    "csv": "Primary names and selected place/person/family columns; notes and non-primary surnames can be omitted",
    "web_family_tree": "Semicolon-delimited interchange file, not a website; names/dates/parents only, unescaped separators",
    "geneweb": "Families with both parents only; unconnected people and single-parent families can be omitted",
    "vcalendar": "Recurring complete Gregorian birth/death/marriage anniversaries only",
    "vcard": "Person contact cards; not a genealogy backup",
    "gedcom": "Native Gramps details can be omitted",
}
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
BOOKMARKS = {
    "person": "bookmarks",
    "family": "family_bookmarks",
    "event": "event_bookmarks",
    "place": "place_bookmarks",
    "source": "source_bookmarks",
    "citation": "citation_bookmarks",
    "repository": "repo_bookmarks",
    "media": "media_bookmarks",
    "note": "note_bookmarks",
}


class ScopedMetadata:
    """Never export bookmarks/home links to excluded records or implicit private tree metadata."""

    def __init__(self, db, include_metadata, filtered, rewrite_media):
        from types import SimpleNamespace

        self._db, self._include, self._rewrite_media = (
            db,
            include_metadata,
            rewrite_media,
        )
        self.members = {
            kind: set(getattr(db, "get_%s_handles" % kind)()) for kind in KINDS
        }
        self.name_formats = db.name_formats if include_metadata else []
        for kind, name in BOOKMARKS.items():
            values = [
                h
                for h in getattr(db, name).get()
                if not filtered or h in self.members[kind]
            ]
            setattr(self, name, SimpleNamespace(get=lambda values=values: list(values)))

    def __getattr__(self, name):
        if name.startswith("get_") and name[4:] in BOOKMARKS.values():
            return lambda: getattr(self, name[4:])
        return getattr(self._db, name)

    def get_default_handle(self):
        handle = self._db.get_default_handle()
        return handle if handle in self.members["person"] else None

    def get_default_person(self):
        handle = self.get_default_handle()
        return self._db.get_person_from_handle(handle) if handle else None

    def get_researcher(self):
        from gramps.gen.lib import Researcher

        return self._db.get_researcher() if self._include else Researcher()

    def get_mediapath(self):
        return self._db.get_mediapath() if self._include else None

    def get_media_from_handle(self, handle):
        media = copy.deepcopy(self._db.get_media_from_handle(handle))
        raw = media.get_path()
        if self._rewrite_media and raw and not ("://" in raw and urlsplit(raw).scheme):
            media.set_path(str(Path(media_path_full(self._db, raw)).resolve()))
        return media

    def get_name_group_keys(self):
        return self._db.get_name_group_keys() if self._include else []


def export_view(workflow, a):
    from gramps.gen.proxy import (
        PrivateProxyDb,
        LivingProxyDb,
        FilterProxyDb,
        ReferencedBySelectionProxyDb,
    )
    from gramps.gen.config import config

    db = workflow.support.db
    mode = a.get("living_mode", "include")
    modes = {
        "include": 99,
        "exclude": 0,
        "surname_only": 1,
        "name_only": 2,
        "redact": 3,
    }
    if mode not in modes:
        raise ValueError("Select an observed living_mode")
    year, interval = a.get("current_year", date.today().year), a.get(
        "years_after_death", 0
    )
    if (
        type(year) is not int
        or not 1 <= year <= 9999
        or type(interval) is not int
        or not 0 <= interval <= 150
    ):
        raise ValueError("Use current_year 1–9999 and years_after_death 0–150")
    handles = a.get("person_handles")
    person_filter = a.get("person_filter")
    if handles is not None and person_filter is not None:
        raise ValueError("Select explicit person_handles or a native person_filter")
    if handles is not None:
        if (
            not isinstance(handles, list)
            or len(handles) > 10000
            or any(not isinstance(h, str) for h in handles)
            or len(set(handles)) != len(handles)
        ):
            raise ValueError("Supply up to 10,000 distinct existing person handles")
        for handle in handles:
            workflow.support.get("person", handle=handle)
    active = bool(
        a.get("exclude_private", False)
        or mode != "include"
        or handles is not None
        or person_filter is not None
        or a.get("linked_only", False)
    )
    if active and a.get("linked_only") is False:
        raise ValueError(
            "Filtered/privacy exports require linked-only reference closure"
        )
    include_metadata = a.get("include_tree_metadata", not active)
    if a.get("operation") == "backup" and (active or not include_metadata):
        raise ValueError(
            "Backups preserve the whole tree and its metadata; use run for scoped/privacy exports"
        )
    view = PrivateProxyDb(db) if a.get("exclude_private", False) else db
    if mode != "include":
        view = LivingProxyDb(view, modes[mode], year, interval)
    filter_state = None
    if person_filter is not None:
        if not isinstance(person_filter, dict) or set(person_filter) - {
            "name",
            "definition",
            "store_path",
        }:
            raise ValueError(
                "person_filter accepts name/definition and an optional native store_path"
            )
        from importlib.util import spec_from_file_location, module_from_spec

        spec = spec_from_file_location(
            "gramps_export_person_filters",
            Path(__file__).with_name("native_filters.py"),
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        filter_state = module.dispatch(
            workflow,
            {"operation": "run", "kind": "person", **person_filter},
            all_handles=True,
            database=view,
        )
        handles = filter_state["handles"]
    if active:
        permitted = (
            set(handles) if handles is not None else set(view.iter_person_handles())
        )

        class Selection:
            def apply(self, database, id_list=None, user=None):
                return permitted & set(database.iter_person_handles())

        view = FilterProxyDb(view, Selection())
    if active:
        view = ReferencedBySelectionProxyDb(view, all_people=True)
    source_view = (
        view  # Resolve original relative media paths before omitting tree metadata.
    )
    is_package = a.get("format") == "gpkg" or (
        a.get("operation") == "backup" and a.get("include_media", False)
    )
    view = ScopedMetadata(
        view, include_metadata, active, not include_metadata and not is_package
    )
    living_settings = (
        {
            key: config.get(key)
            for key in (
                "preferences.private-given-text",
                "preferences.private-surname-text",
                "behavior.max-age-prob-alive",
                "behavior.max-sib-age-diff",
                "behavior.avg-generation-gap",
                "behavior.min-generation-years",
                "behavior.max-gen-estimate",
            )
        }
        if mode != "include"
        else {}
    )
    scope = {
        "whole_tree": not active,
        "exclude_private": a.get("exclude_private", False),
        "living_mode": mode,
        "current_year": year if mode != "include" else None,
        "years_after_death": interval if mode != "include" else None,
        "selected_person_handles": sorted(handles) if handles is not None else None,
        "linked_only": active,
        "include_tree_metadata": include_metadata,
        "filter": filter_state,
        "living_settings": living_settings,
        "proxy_order": ["private", "living", "person", "reference", "metadata"],
    }
    return view, source_view, scope


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def media_inputs(db):
    rows = []
    for handle in sorted(db.get_media_handles()):
        media = db.get_media_from_handle(handle)
        record_path = media.get_path()
        remote = bool(
            record_path and "://" in record_path and urlsplit(record_path).scheme
        )
        path = (
            Path(media_path_full(db, record_path))
            if record_path and not remote
            else None
        )
        exists = bool(path and path.is_file())
        suffix = path.suffix.lower() if path else ""
        if not suffix.isascii() or not suffix[1:].isalnum():
            suffix = ""
        archive_path = (
            "media/" + hashlib.sha256(handle.encode("utf-8")).hexdigest() + suffix
        )
        rows.append(
            {
                "handle": handle,
                "record_path": record_path,
                "remote": remote,
                "source_path": str(path.resolve()) if path else None,
                "exists": exists,
                "size": path.stat().st_size if exists else None,
                "sha256": digest(path) if exists else None,
                "archive_path": archive_path,
            }
        )
    return rows


def protect_destination(db, output):
    resolved = output.resolve()
    save_path = db.get_save_path()
    if save_path and str(save_path) != ":memory:":
        tree_folder = Path(save_path).resolve()
        if resolved == tree_folder or tree_folder in resolved.parents:
            raise ValueError(
                "Output must be outside the active native database directory"
            )
    for handle in db.get_media_handles():
        media = db.get_media_from_handle(handle)
        if not media.get_path():
            continue
        path = Path(media_path_full(db, media.get_path()))
        if resolved == path.resolve() or (
            output.exists() and path.is_file() and output.samefile(path)
        ):
            raise ValueError("Output must not replace referenced source media")


class PortableMedia:
    """Native XML reads detached media records with safe archive-relative paths."""

    def __init__(self, db, media):
        self._db = db
        self._paths = {
            item["handle"]: item["archive_path"] for item in media if item["exists"]
        }

    def __getattr__(self, name):
        return getattr(self._db, name)

    def get_mediapath(self):
        return ""

    def get_media_from_handle(self, handle):
        media = copy.deepcopy(self._db.get_media_from_handle(handle))
        if handle in self._paths:
            media.set_path(self._paths[handle])
        return media


def check_place_dependencies(db):
    """Reject native CSV's nonterminating cyclic or unavailable place ordering."""
    handles = set(db.iter_place_handles())
    dependencies = {
        handle: {ref.ref for ref in db.get_place_from_handle(handle).placeref_list}
        for handle in handles
    }
    if any(target not in handles for refs in dependencies.values() for target in refs):
        raise ValueError("CSV cannot export unavailable enclosing-place references")
    incoming = {handle: len(refs) for handle, refs in dependencies.items()}
    children = {handle: [] for handle in handles}
    for handle, refs in dependencies.items():
        for parent in refs:
            children[parent].append(handle)
    ready = [handle for handle, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        parent = ready.pop()
        visited += 1
        for child in children[parent]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if visited != len(handles):
        raise ValueError("CSV cannot export cyclic enclosing-place references")


def export_options(format_name, a):
    """Bind explicit generator options and native runtime settings to the plan."""
    from gramps.gen.config import config

    options = a.get("options", {})
    if not isinstance(options, dict) or (options and format_name != "csv"):
        raise ValueError("Dedicated exporter options currently support CSV booleans")
    if set(options) - set(CSV_OPTIONS) or any(
        type(value) is not bool for value in options.values()
    ):
        raise ValueError("CSV options use the five discovered native boolean fields")
    result = {}
    if format_name == "csv":
        result["values"] = {key: options.get(key, True) for key in CSV_OPTIONS}
        if not any(result["values"][key] for key in CSV_OPTIONS[:-1]):
            raise ValueError("CSV must include at least one content section")
        result["dialect"], result["delimiter"] = config.get("csv.dialect"), config.get(
            "csv.delimiter"
        )
    if format_name == "vcalendar":
        result["generation_year"] = date.today().year
    return result


def generate(db, output, format_name, media, manager, options=None):
    from gramps.cli.user import User

    errors = []
    user = User(
        quiet=True, error=lambda *args: errors.append([str(arg) for arg in args])
    )
    user.prompt = lambda *args, **kwargs: False
    if format_name not in LOSSLESS:
        plugin = next(
            p for p in manager.get_reg_exporters() if p.id == FORMATS[format_name][0]
        )
        module = manager.load_plugin(plugin)
        option_box = None
        if format_name == "csv":
            from types import SimpleNamespace

            check_place_dependencies(db)
            option_box = SimpleNamespace(
                **options["values"],
                parse_options=lambda: None,
                get_filtered_database=lambda database: database
            )
        success = getattr(module, plugin.export_function)(
            db, str(output), user, option_box
        )
    else:
        from gramps.plugins.export.exportxml import XmlWriter

        if format_name == "gpkg":
            with tarfile.open(output, "w:gz") as archive:
                for item in media:
                    if item["exists"]:
                        # Stream regular files only; never archive symlinks or source paths.
                        info = tarfile.TarInfo(item["archive_path"])
                        info.size = item["size"]
                        info.mode = 0o600
                        with open(item["source_path"], "rb") as stream:
                            archive.addfile(info, stream)

                class Capture(BytesIO):
                    def close(self):
                        pass  # Native XML owns/closes its stream; retain bytes for TAR.

                with Capture() as stream:
                    XmlWriter(PortableMedia(db, media), user, 0, False).write_handle(
                        stream
                    )
                    info = tarfile.TarInfo("data.gramps")
                    info.size = len(stream.getvalue())
                    info.mode = 0o600
                    stream.seek(0)
                    archive.addfile(info, stream)
            success = True
        else:
            success = XmlWriter(db, user, 0, format_name == "gramps").write(str(output))
    if not success or errors or not output.is_file() or not output.stat().st_size:
        raise RuntimeError("Native export failed: " + str(errors))


def dispatch(workflow, a):
    support, manager = workflow.support, BasePluginManager.get_instance()
    exporters = {p.id: p for p in manager.get_reg_exporters()}
    available = {name: spec for name, spec in FORMATS.items() if spec[0] in exporters}
    operation = a.get("operation", "list")
    if operation == "list":
        return {
            "formats": [
                {
                    "format": name,
                    "extension": spec[1],
                    "exporter_id": spec[0],
                    "name": exporters[spec[0]].name,
                    "includes_media_files": name == "gpkg",
                    "lossless_native": name in LOSSLESS,
                    "loss_semantics": LOSSES.get(name),
                    "options": list(CSV_OPTIONS) if name == "csv" else [],
                }
                for name, spec in available.items()
            ],
            "scope": "Whole-tree by default; run supports native private/living/person filters and linked-only records",
            "living_modes": [
                "include",
                "exclude",
                "surname_only",
                "name_only",
                "redact",
            ],
            "backup_scope": "Whole tree with metadata; no filtering",
            "database_changed": False,
        }
    if operation not in ("run", "backup"):
        raise ValueError("Use list, run or backup")
    format_name = (
        ("gpkg" if a.get("include_media", False) else "gramps")
        if operation == "backup"
        else a.get("format")
    )
    if operation == "run" and "include_media" in a:
        raise ValueError(
            "include_media belongs to backup; run selects its format explicitly"
        )
    if format_name not in available:
        raise ValueError("Select an installed supported native export format")
    output = Path(a.get("output_path", ""))
    if (
        not output.is_absolute()
        or not output.parent.is_dir()
        or output.is_dir()
        or output.suffix.lower() != available[format_name][1]
    ):
        raise ValueError(
            "Use an absolute output in an existing directory with the selected extension"
        )
    if output.is_symlink():
        raise ValueError("Export destinations must not be symbolic links")
    if output.exists() and not a.get("overwrite", False):
        raise ValueError(
            "Output exists; explicitly request overwrite or choose a new file"
        )
    db = support.db
    protect_destination(db, output)
    view, source_view, scope = export_view(workflow, a)
    options = export_options(format_name, a)
    if format_name == "csv":
        check_place_dependencies(view)
    state = [
        {
            "kind": kind,
            "handle": handle,
            "revision": support.snapshot(kind, support.get(kind, handle=handle))[
                "revision"
            ],
        }
        for kind in KINDS
        for handle in sorted(getattr(db, "get_%s_handles" % kind)())
    ]
    media = media_inputs(source_view) if format_name == "gpkg" else []
    missing = [item["handle"] for item in media if not item["exists"]]
    if missing and not a.get("allow_missing_media", False):
        raise ValueError(
            "Media package is incomplete; repair missing files or explicitly set allow_missing_media"
        )
    if format_name != "gpkg" and a.get("allow_missing_media", False):
        raise ValueError("allow_missing_media is available only for media packages")
    support.dispatch("batch", {"operation": "receipts"})
    metadata = {
        "media_base": view.get_mediapath(),
        "default_person": view.get_default_handle(),
        "tree": support.batches.context(),
        "researcher": view.get_researcher().serialize(),
        "bookmarks": {
            name: getattr(view, name).get()
            for name in (
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
        "name_formats": view.name_formats,
        "name_groups": {
            key: view.get_name_group_mapping(key) for key in view.get_name_group_keys()
        },
    }
    exported = [
        {
            "kind": kind,
            "handle": handle,
            "revision": support.snapshot(
                kind, getattr(view, "get_%s_from_handle" % kind)(handle)
            )["revision"],
        }
        for kind in KINDS
        for handle in sorted(view.members[kind])
    ]
    existing = digest(output) if output.exists() else None
    plan = workflow.revision(
        {
            "operation": operation,
            "format": format_name,
            "output": str(output),
            "records": state,
            "metadata": metadata,
            "media": media,
            "existing": existing,
            "allow_missing_media": a.get("allow_missing_media", False),
            "scope": scope,
            "exported": exported,
            "options": options,
        }
    )
    result = {
        "applied": False,
        "plan_revision": plan,
        "format": format_name,
        "output_path": str(output),
        "record_counts": {kind: len(view.members[kind]) for kind in KINDS},
        "scope": scope,
        "media": media,
        "missing_media": missing,
        "media_complete": not missing,
        "includes_media_files": format_name == "gpkg",
        "lossless_native": format_name in LOSSLESS
        and scope["whole_tree"]
        and scope["include_tree_metadata"],
        "options": options,
        "loss_semantics": LOSSES.get(format_name),
        "database_changed": False,
    }
    if not a.get("apply", False):
        return result
    if a.get("expected_plan") != plan:
        raise ValueError("Export preview missing or changed; preview again")
    with tempfile.TemporaryDirectory(
        prefix="." + output.stem + ".", dir=output.parent
    ) as temp:
        staged = Path(temp) / output.name
        generate(view, staged, format_name, media, manager, options)
        if format_name == "gpkg" and media_inputs(source_view) != media:
            raise ValueError(
                "Source media changed during export; destination preserved"
            )
        if (
            output.is_symlink()
            or (digest(output) if output.exists() else None) != existing
        ):
            raise ValueError("Destination changed during export; destination preserved")
        if existing is None:
            os.link(staged, output)
        else:
            os.replace(staged, output)
    return {
        **result,
        "applied": True,
        "file_size": output.stat().st_size,
        "sha256": digest(output),
    }
