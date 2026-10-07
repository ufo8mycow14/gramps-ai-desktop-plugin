"""Meaningful bulk-edit checks on a synthetic SQLite :memory: database only."""
import copy
from types import SimpleNamespace


def run_inside():
    from gramps.plugins.db.dbapi.sqlite import SQLite
    import support as module
    db = SQLite()
    db.load(':memory:')
    state = SimpleNamespace(db=db)
    bridge = SimpleNamespace(dbstate=state, windows=lambda: [], session='synthetic-batch-session')
    service = module.GrampsSupport(bridge, state)
    checks = []
    def check(name, condition):
        assert condition, name
        checks.append(name)
    def reject(name, action):
        try:
            action()
        except (ValueError, RuntimeError):
            checks.append(name)
        else:
            raise AssertionError(name)
    def call(method, **a):
        return service.dispatch(method, a)
    def read(kind, handle):
        return call('object', kind=kind, handle=handle)
    def create(kind, patch=None):
        return call('mutate', kind=kind, operation='create', patch=patch or {}, apply=True)['after'][0]
    try:
        person, note, tag = create('person'), create('note'), create('tag', {'name': 'Synthetic old'})
        changes = [{'kind': 'person', 'handle': person['handle'], 'expected_revision': person['revision'], 'patch': {'gender': 1}},
                   {'kind': 'note', 'handle': note['handle'], 'expected_revision': note['revision'], 'patch': {'format': 1}},
                   {'kind': 'tag', 'handle': tag['handle'], 'expected_revision': tag['revision'], 'patch': {'name': 'Synthetic new'}}]
        originals = [person, note, tag]
        preview = call('batch', changes=changes)
        check('preview_all_before_and_proposed_records', len(preview['before']) == len(preview['proposed']) == 3)
        check('preview_zero_writes', all(read(item['kind'], item['handle'])['revision'] == item['revision'] for item in originals))
        reject('apply_requires_preview_plan', lambda: call('batch', changes=changes, apply=True))
        invalid = copy.deepcopy(changes)
        invalid[1]['expected_revision'] = 'stale'
        reject('stale_member_rejects_whole_batch', lambda: call('batch', changes=invalid, apply=True, expected_plan=preview['plan_revision']))
        check('stale_rejection_preserves_all_records', all(read(item['kind'], item['handle'])['revision'] == item['revision'] for item in originals))
        reject('duplicate_record_rejected', lambda: call('batch', changes=[changes[0], changes[0]]))
        reject('identifiers_preserved', lambda: call('batch', changes=[{**changes[0], 'patch': {'gramps_id': 'I_REPLACE'}}]))
        reject('relationship_patch_requires_reciprocal_route', lambda: call('batch', changes=[{**changes[0], 'patch': {'family_list': []}}]))
        reject('unknown_field_rejected', lambda: call('batch', changes=[{**changes[0], 'patch': {'no_such_field': 1}}]))
        reject('batch_limit_rejected', lambda: call('batch', changes=changes * 67))
        commit_note = db.commit_note
        def fail_commit(*args, **kwargs):
            raise RuntimeError('Synthetic injected transaction failure')
        db.commit_note = fail_commit
        try:
            reject('native_transaction_failure_reported', lambda: call('batch', changes=changes, apply=True,
                   expected_plan=preview['plan_revision']))
        finally:
            db.commit_note = commit_note
        check('native_transaction_failure_rolls_back_first_write', all(
            read(item['kind'], item['handle'])['revision'] == item['revision'] for item in originals))
        applied = call('batch', changes=changes, apply=True, expected_plan=preview['plan_revision'])
        check('mixed_record_batch_readback', applied['applied'] and read('person', person['handle'])['data']['gender'] == 1 and
              read('note', note['handle'])['data']['format'] == 1 and read('tag', tag['handle'])['data']['name'] == 'Synthetic new')
        check('explicit_receipt_full_before_after', bool(applied['receipt_id']) and len(applied['before']) == len(applied['after']) == 3)
        rollback = call('batch', operation='rollback', receipt_id=applied['receipt_id'])
        check('rollback_preview_zero_writes', not rollback['applied'] and read('tag', tag['handle'])['data']['name'] == 'Synthetic new')
        call('batch', operation='rollback', receipt_id=applied['receipt_id'], apply=True, expected_plan=rollback['plan_revision'])
        check('guarded_rollback_restores_values', all({k: v for k, v in read(item['kind'], item['handle'])['data'].items() if k != 'change'} ==
              {k: v for k, v in item['data'].items() if k != 'change'} for item in originals))
        reject('stale_rollback_rejected', lambda: call('batch', operation='rollback', receipt_id=applied['receipt_id']))
        reject('unknown_receipt_rejected', lambda: call('batch', operation='rollback', receipt_id='unknown'))
        return {'passed': len(checks), 'checks': checks, 'synthetic_database': ':memory:', 'live_family_record_writes': 0}
    finally:
        db.close()


if __name__ == '__main__':
    import json
    print(json.dumps(run_inside(), indent=2))
