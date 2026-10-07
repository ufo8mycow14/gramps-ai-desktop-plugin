"""Native filter/report workflows and atomic scoped record/media updates."""
import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import uuid
from urllib.parse import urlsplit

from gramps.gen.db import DbTxn
from gramps.gen.lib.json_utils import object_to_dict

METHODS = {'filter', 'report', 'batch', 'media_manage', 'sync_apply', 'sync_refs'}


class WorkflowSupport:
    def __init__(self, support, decode, merge_patch, revision):
        self.support = support
        self.decode, self.merge_patch, self.revision = decode, merge_patch, revision
        self.receipts = {}

    def dispatch(self, method, a):
        if method in ('filter', 'report'):
            from importlib.util import spec_from_file_location, module_from_spec
            spec = spec_from_file_location('gramps_native_' + method,
                                           Path(__file__).with_name('native_' + method + 's.py'))
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.dispatch(self, a)
        return getattr(self, method)(a)

    def batch(self, a):
        return self.support.dispatch('batch', a)

    def media_manage(self, a):
        s = self.support
        if a.get('operation', 'inspect') == 'relink':
            changes = []
            for item in a.get('changes', []):
                path = Path(item['path'])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError('Relinking requires an explicit existing absolute file path')
                patch = {'path': str(path)}
                if a.get('refresh_mime', False):
                    mime = mimetypes.guess_type(str(path))[0]
                    if mime:
                        patch['mime'] = mime
                changes.append({'kind': 'media', 'handle': item['handle'],
                                'expected_revision': item['expected_revision'], 'patch': patch})
            result = self.batch({**a, 'operation': 'update', 'changes': changes,
                                 'label': a.get('label', 'Plugin media relink')})
            result['files_moved'] = False
            return result
        from gramps.gen.utils.file import media_path_full
        offset, limit = max(0, a.get('offset', 0)), max(1, min(a.get('limit', 50), 200))
        handles = a.get('handles')
        if handles is None:
            handles = list(s.db.get_media_handles())
        rows = []
        roots = [Path(root) for root in a.get('search_roots', [])]
        if any(not root.is_absolute() or not root.is_dir() for root in roots):
            raise ValueError('Search roots must be existing absolute directories')
        candidates = {}
        count = 0
        truncated = False
        for root in roots:
            for path in root.rglob('*'):
                count += 1
                if count > 10000:
                    truncated = True
                    break
                if path.is_file():
                    candidates.setdefault(path.name, []).append(str(path))
            if truncated:
                break
        for handle in handles[offset:offset + limit]:
            obj = s.get('media', handle=handle)
            raw = obj.get_path()
            remote = urlsplit(raw).scheme.lower() in ('http', 'https', 'ftp')
            path = Path(media_path_full(s.db, raw)) if raw and not remote else None
            exists = bool(path and path.is_file())
            stat = path.stat() if exists else None
            rows.append({'record': s.snapshot('media', obj), 'path': raw,
                         'resolved_path': str(path) if path else None,
                         'remote': remote, 'exists': exists, 'mime_type': obj.get_mime_type(),
                         'file_size': stat.st_size if stat else None,
                         'modified_ns': stat.st_mtime_ns if stat else None,
                         'candidates': candidates.get(Path(raw).name, []) if not exists and not remote else []})
        return {'media': rows, 'total': len(handles), 'search_truncated': truncated,
                'next_offset': offset + limit if offset + limit < len(handles) else None,
                'files_moved': False, 'records_changed': False}

    def sync_apply(self, a):
        # Adapter-only application of a scoped Web pull. Prevalidate the entire
        # overlay before opening one local native transaction.
        s = self.support
        changes = a.get('changes', [])
        if not 1 <= len(changes) <= 200:
            raise ValueError('Supply between 1 and 200 scoped Web updates')
        objects, seen = [], set()
        for item in changes:
            kind = s.kind(item['kind'])
            current = s.get(kind, handle=item['handle'])
            s.ensure_revision(current, item['expected_revision'])
            key = (kind, current.handle)
            if key in seen:
                raise ValueError('Duplicate sync record')
            seen.add(key)
            updated = self.decode(item['data'])
            if type(updated) is not type(current) or updated.handle != current.handle:
                raise ValueError('Sync must preserve record class and handle')
            if getattr(updated, 'gramps_id', None) != getattr(current, 'gramps_id', None):
                raise ValueError('Sync must preserve the existing Gramps ID')
            objects.append((kind, updated))
        overlay = {(kind, obj.handle): obj for kind, obj in objects}
        for kind, obj in objects:
            s.validate_refs(obj, overlay)
        def lookup(kind, handle):
            return overlay.get((kind, handle)) or s.get(kind, handle=handle)
        for kind, obj in objects:
            if kind == 'person':
                for handle in obj.get_family_handle_list():
                    fam = lookup('family', handle)
                    if obj.handle not in (fam.get_father_handle(), fam.get_mother_handle()):
                        raise ValueError('Sync would break reciprocal partner links')
                for handle in obj.get_parent_family_handle_list():
                    fam = lookup('family', handle)
                    if obj.handle not in [ref.ref for ref in fam.get_child_ref_list()]:
                        raise ValueError('Sync would break reciprocal child links')
                old = s.get('person', handle=obj.handle)
                for handle in set(old.get_family_handle_list()) - set(obj.get_family_handle_list()):
                    fam = lookup('family', handle)
                    if obj.handle in (fam.get_father_handle(), fam.get_mother_handle()):
                        raise ValueError('Include the reciprocal family update in this sync')
                for handle in set(old.get_parent_family_handle_list()) - set(obj.get_parent_family_handle_list()):
                    if obj.handle in [ref.ref for ref in lookup('family', handle).get_child_ref_list()]:
                        raise ValueError('Include the reciprocal family update in this sync')
            if kind == 'family':
                for handle in (obj.get_father_handle(), obj.get_mother_handle()):
                    if handle and obj.handle not in lookup('person', handle).get_family_handle_list():
                        raise ValueError('Include the reciprocal person update in this sync')
                for ref in obj.get_child_ref_list():
                    if obj.handle not in lookup('person', ref.ref).get_parent_family_handle_list():
                        raise ValueError('Include the reciprocal child update in this sync')
                old = s.get('family', handle=obj.handle)
                for handle in set(h for h in (old.get_father_handle(), old.get_mother_handle()) if h) - set(
                        h for h in (obj.get_father_handle(), obj.get_mother_handle()) if h):
                    if obj.handle in lookup('person', handle).get_family_handle_list():
                        raise ValueError('Include removed partner links in this sync')
                for handle in set(ref.ref for ref in old.get_child_ref_list()) - set(ref.ref for ref in obj.get_child_ref_list()):
                    if obj.handle in lookup('person', handle).get_parent_family_handle_list():
                        raise ValueError('Include removed child links in this sync')
        # Follow the destination's proposed ancestry, including unselected local
        # families. Reciprocal memberships alone cannot rule out a new cycle.
        def reaches_parent(person_handle, target):
            todo, visited = [person_handle], set()
            while todo:
                handle = todo.pop()
                if handle == target:
                    return True
                if handle in visited:
                    continue
                visited.add(handle)
                if len(visited) > 10000:
                    raise ValueError('Ancestry scope exceeds the bounded sync check')
                person = lookup('person', handle)
                for family in person.get_parent_family_handle_list():
                    fam = lookup('family', family)
                    todo.extend(h for h in (fam.get_father_handle(), fam.get_mother_handle()) if h)
            return False
        families = {obj.handle: obj for kind, obj in objects if kind == 'family'}
        for kind, obj in objects:
            if kind == 'person':
                for family in obj.get_parent_family_handle_list() + obj.get_family_handle_list():
                    families[family] = lookup('family', family)
        for fam in families.values():
            for parent in (fam.get_father_handle(), fam.get_mother_handle()):
                for child in fam.get_child_ref_list():
                    if parent and reaches_parent(parent, child.ref):
                        raise ValueError('Sync would introduce an ancestry cycle')
        preview = s.changed(objects, 'Plugin Gramps Web pull', False)
        plan = self.revision(preview)
        if not a.get('apply', False):
            return {**preview, 'plan_revision': plan}
        if a.get('expected_plan') != plan:
            raise ValueError('Local sync preview changed; preview again')
        return s.changed(objects, 'Plugin Gramps Web pull', True)

    def sync_refs(self, a):
        result = []
        for item in a.get('records', []):
            kind = self.support.kind(item['kind'])
            obj = self.decode(item['data'])
            if obj.__class__.__name__ != kind.capitalize():
                raise ValueError('Native reference record kind differs')
            result.append({'kind': kind, 'handle': obj.handle,
                           'references': [{'kind': cls.lower(), 'handle': handle}
                                          for cls, handle in obj.get_referenced_handles_recursively()]})
        return result
