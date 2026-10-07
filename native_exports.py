"""Reviewed native whole-tree exports and portable XML/media backups."""
import copy
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlsplit

from gramps.gen.plug import BasePluginManager
from gramps.gen.utils.file import media_path_full

FORMATS = {'gramps': ('ex_gramps', '.gramps'), 'xml': ('ex_gramps', '.xml'),
           'gedcom': ('ex_ged', '.ged'), 'gpkg': ('ex_gpkg', '.gpkg')}
KINDS = ('person', 'family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note', 'tag')


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def media_inputs(db):
    rows = []
    for handle in sorted(db.get_media_handles()):
        media = db.get_media_from_handle(handle)
        record_path = media.get_path()
        remote = bool(record_path and '://' in record_path and urlsplit(record_path).scheme)
        path = Path(media_path_full(db, record_path)) if record_path and not remote else None
        exists = bool(path and path.is_file())
        suffix = path.suffix.lower() if path else ''
        if not suffix.isascii() or not suffix[1:].isalnum():
            suffix = ''
        archive_path = 'media/' + hashlib.sha256(handle.encode('utf-8')).hexdigest() + suffix
        rows.append({'handle': handle, 'record_path': record_path, 'remote': remote, 'source_path': str(path.resolve()) if path else None,
                     'exists': exists, 'size': path.stat().st_size if exists else None,
                     'sha256': digest(path) if exists else None, 'archive_path': archive_path})
    return rows


def protect_destination(db, output):
    resolved = output.resolve()
    save_path = db.get_save_path()
    if save_path and str(save_path) != ':memory:':
        tree_folder = Path(save_path).resolve()
        if resolved == tree_folder or tree_folder in resolved.parents:
            raise ValueError('Output must be outside the active native database directory')
    for handle in db.get_media_handles():
        media = db.get_media_from_handle(handle)
        if not media.get_path():
            continue
        path = Path(media_path_full(db, media.get_path()))
        if resolved == path.resolve() or (output.exists() and path.is_file() and output.samefile(path)):
            raise ValueError('Output must not replace referenced source media')


class PortableMedia:
    """Native XML reads detached media records with safe archive-relative paths."""
    def __init__(self, db, media):
        self._db = db
        self._paths = {item['handle']: item['archive_path'] for item in media if item['exists']}

    def __getattr__(self, name):
        return getattr(self._db, name)

    def get_mediapath(self):
        return ''

    def get_media_from_handle(self, handle):
        media = copy.deepcopy(self._db.get_media_from_handle(handle))
        if handle in self._paths:
            media.set_path(self._paths[handle])
        return media


def generate(db, output, format_name, media, manager):
    from gramps.cli.user import User
    errors = []
    user = User(quiet=True, error=lambda *args: errors.append([str(arg) for arg in args]))
    user.prompt = lambda *args, **kwargs: False
    if format_name == 'gedcom':
        plugin = next(p for p in manager.get_reg_exporters() if p.id == 'ex_ged')
        module = manager.load_plugin(plugin)
        success = getattr(module, plugin.export_function)(db, str(output), user)
    else:
        from gramps.plugins.export.exportxml import XmlWriter
        if format_name == 'gpkg':
            with tarfile.open(output, 'w:gz') as archive:
                for item in media:
                    if item['exists']:
                        # Stream regular files only; never archive symlinks or source paths.
                        info = tarfile.TarInfo(item['archive_path'])
                        info.size = item['size']
                        info.mode = 0o600
                        with open(item['source_path'], 'rb') as stream:
                            archive.addfile(info, stream)
                class Capture(BytesIO):
                    def close(self):
                        pass  # Native XML owns/closes its stream; retain bytes for TAR.
                with Capture() as stream:
                    XmlWriter(PortableMedia(db, media), user, 0, False).write_handle(stream)
                    info = tarfile.TarInfo('data.gramps')
                    info.size = len(stream.getvalue())
                    info.mode = 0o600
                    stream.seek(0)
                    archive.addfile(info, stream)
            success = True
        else:
            success = XmlWriter(db, user, 0, format_name == 'gramps').write(str(output))
    if not success or errors or not output.is_file() or not output.stat().st_size:
        raise RuntimeError('Native export failed: ' + str(errors))


def dispatch(workflow, a):
    support, manager = workflow.support, BasePluginManager.get_instance()
    exporters = {p.id: p for p in manager.get_reg_exporters()}
    available = {name: spec for name, spec in FORMATS.items() if spec[0] in exporters}
    operation = a.get('operation', 'list')
    if operation == 'list':
        return {'formats': [{'format': name, 'extension': spec[1], 'exporter_id': spec[0],
                             'name': exporters[spec[0]].name, 'includes_media_files': name == 'gpkg',
                             'lossless_native': name != 'gedcom'} for name, spec in available.items()],
                'scope': 'Whole open tree, including private and living records; no filtering',
                'database_changed': False}
    if operation not in ('run', 'backup'):
        raise ValueError('Use list, run or backup')
    format_name = ('gpkg' if a.get('include_media', False) else 'gramps') if operation == 'backup' else a.get('format')
    if operation == 'run' and 'include_media' in a:
        raise ValueError('include_media belongs to backup; run selects its format explicitly')
    if format_name not in available:
        raise ValueError('Select an installed supported native export format')
    output = Path(a.get('output_path', ''))
    if not output.is_absolute() or not output.parent.is_dir() or output.is_dir() or output.suffix.lower() != available[format_name][1]:
        raise ValueError('Use an absolute output in an existing directory with the selected extension')
    if output.is_symlink():
        raise ValueError('Export destinations must not be symbolic links')
    if output.exists() and not a.get('overwrite', False):
        raise ValueError('Output exists; explicitly request overwrite or choose a new file')
    db = support.db
    protect_destination(db, output)
    state = [{'kind': kind, 'handle': handle, 'revision': support.snapshot(kind, support.get(kind, handle=handle))['revision']}
             for kind in KINDS for handle in sorted(getattr(db, 'get_%s_handles' % kind)())]
    media = media_inputs(db) if format_name == 'gpkg' else []
    missing = [item['handle'] for item in media if not item['exists']]
    if missing and not a.get('allow_missing_media', False):
        raise ValueError('Media package is incomplete; repair missing files or explicitly set allow_missing_media')
    if format_name != 'gpkg' and a.get('allow_missing_media', False):
        raise ValueError('allow_missing_media is available only for media packages')
    support.dispatch('batch', {'operation': 'receipts'})
    metadata = {'media_base': db.get_mediapath(), 'default_person': db.get_default_handle(),
                'tree': support.batches.context(), 'researcher': db.get_researcher().serialize(),
                'bookmarks': {name: getattr(db, name).get() for name in
                              ('bookmarks', 'family_bookmarks', 'event_bookmarks', 'place_bookmarks',
                               'source_bookmarks', 'citation_bookmarks', 'repo_bookmarks', 'media_bookmarks', 'note_bookmarks')},
                'name_formats': db.name_formats,
                'name_groups': {key: db.get_name_group_mapping(key) for key in db.get_name_group_keys()}}
    existing = digest(output) if output.exists() else None
    plan = workflow.revision({'operation': operation, 'format': format_name, 'output': str(output),
                              'records': state, 'metadata': metadata, 'media': media, 'existing': existing,
                              'allow_missing_media': a.get('allow_missing_media', False)})
    result = {'applied': False, 'plan_revision': plan, 'format': format_name, 'output_path': str(output),
              'record_counts': {kind: sum(item['kind'] == kind for item in state) for kind in KINDS},
              'scope': 'Whole open tree, including private and living records; no filtering',
              'media': media, 'missing_media': missing, 'media_complete': not missing,
              'includes_media_files': format_name == 'gpkg', 'lossless_native': format_name != 'gedcom',
              'database_changed': False}
    if not a.get('apply', False):
        return result
    if a.get('expected_plan') != plan:
        raise ValueError('Export preview missing or changed; preview again')
    with tempfile.TemporaryDirectory(prefix='.' + output.stem + '.', dir=output.parent) as temp:
        staged = Path(temp) / output.name
        generate(db, staged, format_name, media, manager)
        if format_name == 'gpkg' and media_inputs(db) != media:
            raise ValueError('Source media changed during export; destination preserved')
        if output.is_symlink() or (digest(output) if output.exists() else None) != existing:
            raise ValueError('Destination changed during export; destination preserved')
        if existing is None:
            os.link(staged, output)
        else:
            os.replace(staged, output)
    return {**result, 'applied': True, 'file_size': output.stat().st_size, 'sha256': digest(output)}
