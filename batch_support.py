"""Reviewable native batch updates with session receipts and guarded rollback."""
import copy
import uuid
from gramps.gen.lib.json_utils import object_to_dict


class BatchSupport:
    def __init__(self, support, decode, merge_patch, revision):
        self.support = support
        self.decode, self.merge_patch, self.revision = decode, merge_patch, revision
        self.receipts = {}

    def batch(self, a):
        s = self.support
        if a.get('operation', 'update') == 'rollback':
            receipt = self.receipts.get(a.get('receipt_id'))
            if receipt is None:
                raise ValueError('Unknown receipt for this Gramps session')
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
        preview = s.changed(entries, a.get('label', 'Plugin batch update'), False)
        plan = self.revision(preview)
        preview['plan_revision'] = plan
        if not a.get('apply', False):
            return preview
        if a.get('expected_plan') != plan:
            raise ValueError('Batch preview changed or missing; preview again before applying')
        result = s.changed(entries, a.get('label', 'Plugin batch update'), True)
        receipt_id = uuid.uuid4().hex
        self.receipts[receipt_id] = copy.deepcopy(result)
        if len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        result.update(receipt_id=receipt_id, plan_revision=plan,
                      rollback_scope='Current session; all records must still match their saved revisions')
        return result
