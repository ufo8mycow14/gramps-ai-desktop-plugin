"""Run structured-support checks inside Gramps, with synthetic :memory: data only."""
import json
from pathlib import Path


def run_inside(bridge):
    from types import SimpleNamespace
    from gramps.plugins.db.dbapi.sqlite import SQLite
    from gramps.gen import lib
    from gramps.gen.db import DbTxn
    from gramps.gen.lib.json_utils import object_to_dict
    support_module = __import__('importlib.util', fromlist=['util'])
    spec = support_module.spec_from_file_location('support_test', Path(__file__).with_name('support.py'))
    module = support_module.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = SQLite()
    db.load(':memory:')
    state = SimpleNamespace(db=db)
    support = module.GrampsSupport(bridge, state)
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    def call(method, **arguments):
        return support.dispatch(method, arguments)
    def read(kind, handle):
        return call('object', kind=kind, handle=handle)
    def create(kind, patch=None):
        return call('mutate', kind=kind, operation='create', patch=patch or {}, apply=True)['after'][0]
    def attach(owner, target, operation='add'):
        return call('attach', kind=owner['kind'], handle=owner['handle'],
                    expected_revision=read(owner['kind'], owner['handle'])['revision'],
                    target_kind=target['kind'], target_handle=target['handle'], operation=operation, apply=True)
    try:
        records = {}
        for kind in module.KINDS:
            patch = {'name': 'Synthetic MCP tag'} if kind == 'tag' else {}
            template = call('schema', kind=kind)['template']
            check('schema_' + kind, template['_class'] == module.KINDS[kind])
            records[kind] = create(kind, patch)
            check('create_' + kind, bool(records[kind]['handle']))
        person = records['person']
        check('preview_no_write', not call('mutate', kind='person', operation='update', handle=person['handle'],
              expected_revision=person['revision'], patch={'gender': 1})['applied'] and
              read('person', person['handle'])['revision'] == person['revision'])
        updated = call('mutate', kind='person', operation='update', handle=person['handle'],
                       expected_revision=person['revision'], patch={'gender': 1}, apply=True)['after'][0]
        check('update_readback', updated['data']['gender'] == 1 and updated['handle'] == person['handle'])
        try:
            call('mutate', kind='person', operation='update', handle=person['handle'],
                 expected_revision='stale', patch={}, apply=True)
        except ValueError:
            checks.append('stale_revision_rejected')
        else:
            raise AssertionError('stale revision accepted')
        family = records['family']
        child = create('person')
        for p, role in ((person, 'father'), (child, 'child')):
            call('family_member', family_handle=family['handle'], person_handle=p['handle'], role=role,
                 family_revision=read('family', family['handle'])['revision'],
                 person_revision=read('person', p['handle'])['revision'], apply=True)
        relatives = call('relatives', handle=child['handle'])
        check('reciprocal_membership', len(relatives['parent_families']) == 1 and
              db.get_person_from_handle(person['handle']).get_family_handle_list() == [family['handle']])
        other_family = create('family')
        call('family_member', family_handle=other_family['handle'], person_handle=child['handle'], role='father',
             family_revision=other_family['revision'], person_revision=read('person', child['handle'])['revision'], apply=True)
        try:
            call('family_member', family_handle=other_family['handle'], person_handle=person['handle'], role='child',
                 family_revision=read('family', other_family['handle'])['revision'],
                 person_revision=read('person', person['handle'])['revision'], apply=True)
        except ValueError:
            checks.append('ancestry_cycle_rejected')
        else:
            raise AssertionError('ancestry cycle accepted')
        for target in ('note', 'citation', 'tag', 'event', 'media'):
            attach(person, records[target])
            check('attach_' + target, (target, records[target]['handle']) in
                  [(x['kind'], x['handle']) for x in call('links', kind='person', handle=person['handle'])['references']])
            attach(person, records[target], 'remove')
            check('detach_' + target, records[target]['handle'] not in
                  [x['handle'] for x in call('links', kind='person', handle=person['handle'])['references']])
        attach(records['source'], records['repository'])
        attach(records['source'], records['repository'], 'remove')
        checks.append('repository_reference_roundtrip')
        attach(records['citation'], records['source'])
        check('citation_source', db.get_citation_from_handle(records['citation']['handle']).get_reference_handle() == records['source']['handle'])
        try:
            call('mutate', kind='source', operation='delete', handle=records['source']['handle'],
                 expected_revision=read('source', records['source']['handle'])['revision'], apply=True)
        except ValueError:
            checks.append('referenced_delete_rejected')
        else:
            raise AssertionError('referenced deletion accepted')
        check('history_read', bool(call('history')['undo']))
        note = records['note']
        call('mutate', kind='note', operation='delete', handle=note['handle'], expected_revision=note['revision'], apply=True)
        check('delete_readback', not db.has_note_handle(note['handle']))
        call('history', operation='undo')
        check('undo_readback', db.get_note_from_handle(note['handle']) is not None)
        call('history', operation='redo')
        check('redo_readback', not db.has_note_handle(note['handle']))
        for kind in module.KINDS:
            if kind == 'tag':
                continue
            left, right = create(kind), create(kind)
            call('merge', kind=kind, keep_handle=left['handle'], remove_handle=right['handle'],
                 keep_revision=left['revision'], remove_revision=right['revision'], apply=True)
            check('merge_' + kind, not support.exists(kind, right['handle']))
        check('field_search', bool(call('find', kind='person', filters=[{'field': 'gender', 'value': 1}])['items']))
        check('date_parse', call('date', text='7 Oct 2026')['valid'])
        check('research_context', 'related_records' in call('research', kind='person', handle=person['handle']))
        check('media_path', 'resolved_path' in call('media_info', handle=records['media']['handle']))
        try:
            with DbTxn('Synthetic rollback', db) as txn:
                temporary = lib.Note()
                db.add_note(temporary, txn)
                raise ValueError('deliberate transaction failure')
        except ValueError:
            pass
        check('native_transaction_rollback', not db.has_note_handle(temporary.handle))
        check('live_db_untouched', bridge.dbstate is not state)
        return {'checks': checks, 'passed': len(checks), 'synthetic_database': ':memory:', 'family_record_writes': 0}
    except Exception:
        import traceback
        raise RuntimeError('After %s: %s' % (checks[-3:], traceback.format_exc()))
    finally:
        db.close()


if __name__ == '__main__':
    import server
    code = "import importlib.util\ns = importlib.util.spec_from_file_location('verify_support_runtime', %r)\nm = importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\nresult = m.run_inside(bridge)" % str(Path(__file__).resolve())
    response = server.call_tool('gramps_python', {'code': code})
    print(json.dumps(response, indent=2))
    if 'error' in response:
        raise SystemExit(1)
    Path(__file__).with_name('support_verification.json').write_text(json.dumps(response, indent=2), encoding='utf-8')
