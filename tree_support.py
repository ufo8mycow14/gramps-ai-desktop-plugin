"""Reviewed native bookmarks, home person and researcher metadata with guarded receipts."""
import copy
import time
import uuid

BOOKMARKS = {'person': 'bookmarks', 'family': 'family_bookmarks', 'event': 'event_bookmarks',
             'place': 'place_bookmarks', 'source': 'source_bookmarks', 'citation': 'citation_bookmarks',
             'repository': 'repo_bookmarks', 'media': 'media_bookmarks', 'note': 'note_bookmarks'}
RESEARCHER = ('name', 'address', 'email', 'street', 'locality', 'city', 'county', 'state', 'country', 'postal_code', 'phone')


class TreeSupport:
    def __init__(self, support, revision):
        self.support, self.revision = support, revision
        self.receipts = {}

    def context(self):
        self.support.dispatch('batch', {'operation': 'receipts'})
        return self.support.batches.context()

    def read(self, field, kind=None):
        db = self.support.db
        if field == 'bookmarks':
            if kind not in BOOKMARKS:
                raise ValueError('Bookmarks support the nine native record kinds; tags have no bookmark list')
            return list(getattr(db, BOOKMARKS[kind]).get())
        if field == 'home':
            return db.get_default_handle()
        if field == 'researcher':
            owner = db.get_researcher()
            return {key: getattr(owner, 'get_' + key)() for key in RESEARCHER}
        raise ValueError('Unknown tree metadata field')

    def write(self, field, value, kind=None):
        db = self.support.db
        if field == 'bookmarks':
            getattr(db, BOOKMARKS[kind]).set(value)
        elif field == 'home':
            db.set_default_person_handle(value)
        else:
            from gramps.gen.lib import Researcher
            owner = Researcher(db.get_researcher())
            for key, item in value.items():
                getattr(owner, 'set_' + key)(item)
            db.set_researcher(owner)

    def dispatch(self, a):
        operation = a.get('operation', 'get')
        if operation == 'receipts':
            return {'receipts': [{key: item[key] for key in ('receipt_id', 'field', 'kind', 'created_at', 'tree')}
                                 for item in self.receipts.values()]}
        if operation == 'receipt':
            if a.get('receipt_id') not in self.receipts:
                raise ValueError('Unknown tree metadata receipt in this session')
            return copy.deepcopy(self.receipts[a['receipt_id']])
        if operation == 'rollback':
            receipt = self.dispatch({'operation': 'receipt', 'receipt_id': a.get('receipt_id')})
            if receipt['tree'] != self.context():
                raise ValueError('Metadata receipt belongs to another tree')
            field, kind = receipt['field'], receipt['kind']
            current = self.read(field, kind)
            if current != receipt['after']:
                raise ValueError('Tree metadata changed after this receipt')
            value = copy.deepcopy(receipt['before'])
        else:
            if operation not in ('get', 'set'):
                raise ValueError('Select get, set, receipts, receipt or rollback')
            field, kind = a.get('field', 'bookmarks'), a.get('kind', 'person')
            current = self.read(field, kind)
            if operation == 'get':
                result = {'field': field, 'kind': kind, 'value': current, 'revision': self.revision(current),
                          'metadata_changed': False, 'record_history': False}
                if field == 'bookmarks':
                    result['records'] = [self.support.summary(kind, self.support.get(kind, handle=handle))
                                         if self.support.exists(kind, handle) else {'handle': handle, 'missing': True} for handle in current]
                return result
            value = copy.deepcopy(a.get('value'))
            if field == 'researcher':
                if not isinstance(value, dict) or set(value) - set(RESEARCHER) or any(not isinstance(v, str) for v in value.values()):
                    raise ValueError('Researcher updates use known string fields; read the current value first')
                value = {**current, **value}
        targets = []
        if field == 'bookmarks':
            if not isinstance(value, list) or len(value) > 200 or any(not isinstance(h, str) for h in value) or len(set(value)) != len(value):
                raise ValueError('Supply an ordered list of up to 200 distinct existing bookmark handles')
            targets = [(kind, handle) for handle in value]
        elif field == 'home':
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError('Home person requires an existing handle or null to clear it')
            targets = [('person', value)] if value else []
        target_revisions = [self.support.snapshot(k, self.support.get(k, handle=h))['revision'] for k, h in targets]
        plan_data = {'field': field, 'kind': kind, 'before': current, 'proposed': value,
                     'tree': self.context(), 'target_revisions': target_revisions, 'operation': operation}
        plan = self.revision(plan_data)
        preview = {**plan_data, 'plan_revision': plan, 'applied': False, 'record_history': False,
                   'persistence': 'Native metadata lifecycle; bookmarks and researcher are saved when the tree closes'}
        if not a.get('apply', False):
            return preview
        if a.get('expected_plan') != plan:
            raise ValueError('Tree metadata preview missing or changed; preview again')
        if current == value:
            return {**preview, 'applied': True, 'metadata_changed': False, 'receipt_id': None}
        self.support.writable()
        try:
            self.write(field, value, kind)
        except Exception:
            if field == 'bookmarks':
                getattr(self.support.db, BOOKMARKS[kind]).load(current)
            elif self.read(field, kind) != current:
                self.write(field, current, kind)
            raise
        saved = self.read(field, kind)
        result = {'receipt_id': uuid.uuid4().hex, 'field': field, 'kind': kind,
                  'before': current, 'after': saved, 'tree': plan_data['tree'], 'plan_revision': plan,
                  'created_at': time.time(), 'applied': True, 'metadata_changed': True, 'record_history': False,
                  'persistence': preview['persistence']}
        self.receipts[result['receipt_id']] = copy.deepcopy(result)
        if len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return result
