"""Reviewable native batch updates with session receipts and guarded rollback."""
import copy
import os
from pathlib import Path
import time
import uuid
from gramps.gen.lib.json_utils import object_to_dict


class BatchSupport:
    def __init__(self, support, decode, merge_patch, revision):
        self.support = support
        self.decode, self.merge_patch, self.revision = decode, merge_patch, revision
        self.receipts = {}

    def context(self, durable=False):
        db = self.support.db
        path = db.get_save_path()
        ident = db.get_dbid()
        backend = type(db).__module__ + '.' + type(db).__name__
        if path and ident and str(path) != ':memory:' and str(ident) != ':memory:':
            from gramps.version import VERSION
            return {'type': 'local_tree_location', 'backend': backend, 'path': os.path.normcase(os.path.realpath(path)),
                    'id': str(ident), 'gramps_release': '.'.join(VERSION.split('.')[:2])}
        if durable:
            raise ValueError('Saved plans require an identified on-disk tree; in-memory trees are session-only')
        if not hasattr(self, 'ephemeral'):
            self.ephemeral = uuid.uuid4().hex
        return {'type': 'session_tree', 'backend': backend, 'session': self.ephemeral, 'object': id(db)}

    def receipt(self, ident):
        if ident not in self.receipts:
            raise ValueError('Unknown receipt for this Gramps session')
        return copy.deepcopy(self.receipts[ident])

    def execute(self, entries, a, details=None):
        s = self.support
        preview = s.changed(entries, a.get('label', 'Plugin batch update'), False)
        preview.update(tree=self.context(), **(details or {}))
        plan = self.revision(preview)
        preview['plan_revision'] = plan
        if not a.get('apply', False):
            return preview
        if a.get('expected_plan') != plan:
            raise ValueError('Batch preview changed or missing; preview again before applying')
        if not entries:
            return {**preview, 'applied': True, 'after': [], 'receipt_id': None, 'changed_records': 0}
        result = s.changed(entries, a.get('label', 'Plugin batch update'), True)
        result.update(receipt_id=uuid.uuid4().hex, plan_revision=plan, tree=preview['tree'],
                      created_at=time.time(), **(details or {}),
                      rollback_scope='Current session and tree; all records must match their saved revisions')
        self.receipts[result['receipt_id']] = copy.deepcopy(result)
        if len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return result

    def batch(self, a):
        s = self.support
        if a.get('operation') == 'receipts':
            return {'receipts': [{'receipt_id': ident, 'created_at': receipt.get('created_at'),
                    'undo_label': receipt.get('undo_label'), 'record_count': len(receipt['after']),
                    'tree': receipt.get('tree')} for ident, receipt in self.receipts.items()]}
        if a.get('operation') == 'receipt':
            return self.receipt(a.get('receipt_id'))
        if a.get('operation', 'update') == 'rollback':
            receipt = self.receipt(a.get('receipt_id'))
            if receipt.get('tree') != self.context():
                raise ValueError('Receipt belongs to another tree or predates tree binding; rollback rejected')
            entries = []
            for before, after in zip(receipt['before'], receipt['after']):
                current = s.get(after['kind'], handle=after['handle'])
                s.ensure_revision(current, after['revision'])
                entries.append((before['kind'], self.decode(before['data'])))
        else:
            changes = a.get('changes', [])
            if not 1 <= len(changes) <= 200:
                raise ValueError('Supply between 1 and 200 scoped updates')
            entries, seen = [], set()
            for change in changes:
                required = {'kind', 'handle', 'expected_revision', 'patch'}
                if not isinstance(change, dict) or set(change) != required or not isinstance(change['patch'], dict):
                    raise ValueError('Batch updates require exactly kind, handle, expected_revision and an object patch')
                kind = s.kind(change['kind'])
                obj = s.get(kind, handle=change['handle'])
                key = (kind, obj.handle)
                if key in seen:
                    raise ValueError('A batch may update each record only once')
                seen.add(key)
                s.ensure_revision(obj, change.get('expected_revision'))
                patch = change['patch']
                protected = ('handle', 'gramps_id', 'change', '_class')
                relationships = {'person': ('family_list', 'parent_family_list'),
                                 'family': ('father_handle', 'mother_handle', 'child_ref_list')}
                if any(field in patch for field in protected + relationships.get(kind, ())):
                    raise ValueError('Preserve identifiers; use family_member for reciprocal relationships')
                updated = self.decode(self.merge_patch(object_to_dict(obj), patch))
                entries.append((kind, updated))
        return self.execute(entries, a)

    def attachments(self, a):
        s = self.support
        changes = a.get('changes', [])
        if not 1 <= len(changes) <= 200:
            raise ValueError('Supply between 1 and 200 ordered attachment operations')
        owners, originals, targets = {}, {}, {}
        for change in changes:
            allowed = {'kind', 'handle', 'expected_revision', 'target_kind', 'target_handle',
                       'target_revision', 'operation', 'reference_patch', 'all_references'}
            required = allowed - {'operation', 'reference_patch', 'all_references'}
            if not isinstance(change, dict) or set(change) - allowed or required - set(change):
                raise ValueError('Attachment operation fields are missing or unknown')
            if type(change.get('all_references', False)) is not bool or (
                    'reference_patch' in change and not isinstance(change['reference_patch'], dict)):
                raise ValueError('Use a boolean all_references and an object reference_patch')
            kind, target_kind = s.kind(change['kind']), s.kind(change['target_kind'])
            key = (kind, change['handle'])
            if key not in owners:
                obj = s.get(kind, handle=change['handle'])
                originals[key] = s.snapshot(kind, obj)
                owners[key] = obj
            if change['expected_revision'] != originals[key]['revision']:
                raise ValueError('Every owner operation requires the same current original revision')
            target = s.get(target_kind, handle=change['target_handle'])
            s.ensure_revision(target, change['target_revision'])
            targets[(target_kind, target.handle)] = s.snapshot(target_kind, target)
            s.prepare_attachment(kind, owners[key], target_kind, target, change.get('operation', 'add'),
                                 change.get('reference_patch'), strict=True, all_references=change.get('all_references', False))
        entries = [(kind, obj) for (kind, handle), obj in owners.items()
                   if object_to_dict(obj) != originals[(kind, handle)]['data']]
        return self.execute(entries, a, {'operations': copy.deepcopy(changes), 'owner_records': list(originals.values()),
                                        'targets': list(targets.values()), 'changed_records': len(entries)})

    def files(self, a):
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('gramps_batch_files', Path(__file__).with_name('batch_files.py'))
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.dispatch(self, a)
