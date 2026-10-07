"""Profile-wide preference previews and compensating restoration; never atomic."""
import copy
import time
import uuid
from gramps.gen.config import config


class Preferences:
    def __init__(self, support, revision, manager=config):
        self.support, self.revision, self.config = support, revision, manager
        self.receipts = {}

    def context(self):
        return {'session': self.support.bridge.session, 'profile': str(self.config.filename)}

    def dispatch(self, a):
        op = a.get('operation', 'get')
        config = self.config
        if op == 'keys':
            return [section + '.' + key for section in config.get_sections() for key in config.get_section_settings(section)]
        if op == 'receipts':
            return {'receipts': [{'receipt_id': item['receipt_id'], 'keys': sorted(item['after']),
                                 'created_at': item['created_at']} for item in self.receipts.values()]}
        if op in ('receipt', 'rollback'):
            receipt = self.receipts.get(a.get('receipt_id'))
            if receipt is None:
                raise ValueError('Unknown preference receipt in this session')
            if op == 'receipt':
                return copy.deepcopy(receipt)
            if receipt['context'] != self.context():
                raise ValueError('Preference receipt belongs to another session/profile')
            changes = copy.deepcopy(receipt['before'])
            if {key: config.get(key) for key in changes} != receipt['after']:
                raise ValueError('Preferences changed after this receipt')
        elif op in ('get', 'set'):
            key = a['key']
            previous = config.get(key)
            if op == 'get':
                return {'key': key, 'value': previous, 'previous': previous,
                        'default': config.get_default(key) if config.has_default(key) else None,
                        'revision': self.revision(previous), 'scope': 'Gramps profile; every open tree'}
            # Preserve the existing explicit single-key set contract.
            if type(a['value']) is not type(previous):
                raise ValueError('Preference type must match the existing setting')
            config.set(key, copy.deepcopy(a['value']))
            config.save()
            return {'key': key, 'value': config.get(key), 'previous': previous,
                    'save_requested': True, 'scope': 'Gramps profile; every open tree'}
        elif op == 'update':
            changes = a.get('changes')
        else:
            raise ValueError('Select keys, get, set, update, receipts, receipt or rollback')
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 50:
            raise ValueError('Supply 1–50 preference keys and values')
        changes = copy.deepcopy(changes)
        before = {key: copy.deepcopy(config.get(key)) for key in changes}
        for key, value in changes.items():
            if type(value) is not type(before[key]):
                raise ValueError('Preference type must match the existing setting: ' + key)
        plan_data = {'context': self.context(), 'operation': op, 'before': before, 'proposed': changes}
        plan = self.revision(plan_data)
        preview = {**plan_data, 'plan_revision': plan, 'applied': False,
                   'atomic': False, 'scope': 'Gramps profile; every open tree', 'record_history': False,
                   'persistence': 'Native save requested; native configuration may log filesystem save failures'}
        if not a.get('apply', False):
            return preview
        if a.get('expected_plan') != plan:
            raise ValueError('Preference plan missing or changed; preview again')
        written = []
        try:
            for key, value in changes.items():
                if config.get(key) != before[key]:
                    raise ValueError('Preference changed during callbacks: ' + key)
                written.append(key)
                config.set(key, copy.deepcopy(value))
            if {key: config.get(key) for key in changes} != changes:
                raise ValueError('Native callback changed a proposed preference')
            config.save()
        except Exception as error:
            failed = []
            for key in reversed(list(before)):
                try:
                    config.set(key, copy.deepcopy(before[key]))
                except Exception:
                    failed.append(key)
            failed.extend(key for key in before if config.get(key) != before[key] and key not in failed)
            try:
                config.save()
            except Exception:
                failed.append('native_save')
            if failed:
                raise RuntimeError('Preference update failed; restoration incomplete for: ' + ', '.join(failed)) from error
            raise
        result = {**preview, 'receipt_id': uuid.uuid4().hex, 'after': copy.deepcopy(changes),
                  'applied': True, 'created_at': time.time(), 'save_requested': True}
        self.receipts[result['receipt_id']] = copy.deepcopy(result)
        if len(self.receipts) > 100:
            del self.receipts[next(iter(self.receipts))]
        return result
