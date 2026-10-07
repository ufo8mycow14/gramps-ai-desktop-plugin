"""Meaningful bulk-edit checks on a synthetic SQLite :memory: database only."""
import copy
import json
from pathlib import Path
import tempfile
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
        from gramps.gen import lib
        from gramps.gen.lib.json_utils import object_to_dict
        attribute = lib.Attribute()
        attribute.add_note(note['handle'])
        owner = create('person', {'attribute_list': [object_to_dict(attribute)]})
        family = create('family')
        source = create('source', {'title': 'Synthetic source'})
        citation = create('citation')
        call('attach', kind='citation', handle=citation['handle'], expected_revision=citation['revision'],
             target_kind='source', target_handle=source['handle'], apply=True)
        event = create('event')
        type_data = copy.deepcopy(event['data']['type'])
        type_data['value'] = lib.EventType.BIRTH
        call('mutate', kind='event', handle=event['handle'], operation='update', expected_revision=event['revision'],
             patch={'type': type_data}, apply=True)
        def action(kind, handle, target_kind, target_handle, operation='add', **extra):
            return {'kind': kind, 'handle': handle, 'expected_revision': read(kind, handle)['revision'],
                    'target_kind': target_kind, 'target_handle': target_handle,
                    'target_revision': read(target_kind, target_handle)['revision'], 'operation': operation, **extra}
        attachments = [action('person', owner['handle'], 'tag', tag['handle']),
                       action('person', owner['handle'], 'note', note['handle']),
                       action('person', owner['handle'], 'citation', citation['handle']),
                       action('person', owner['handle'], 'event', event['handle']),
                       action('family', family['handle'], 'event', event['handle'])]
        attached_preview = call('batch_attach', changes=attachments)
        check('attachments_group_owner_operations_without_writes', len(attached_preview['proposed']) == 2 and
              read('person', owner['handle'])['revision'] == owner['revision'])
        bad_target = copy.deepcopy(attachments)
        bad_target[-1]['target_revision'] = 'stale'
        reject('attachments_stale_target_rejects_entire_batch', lambda: call('batch_attach', changes=bad_target))
        commit_family = db.commit_family
        db.commit_family = fail_commit
        try:
            reject('attachment_transaction_failure_reported', lambda: call('batch_attach', changes=attachments,
                   apply=True, expected_plan=attached_preview['plan_revision']))
        finally:
            db.commit_family = commit_family
        check('attachment_failure_rolls_back_first_owner', read('person', owner['handle'])['revision'] == owner['revision'])
        attached = call('batch_attach', changes=attachments, apply=True, expected_plan=attached_preview['plan_revision'])
        check('attachments_native_event_indexes_and_family_role', db.get_person_from_handle(owner['handle']).get_birth_ref().ref == event['handle'] and
              db.get_family_from_handle(family['handle']).get_event_ref_list()[0].get_role() == lib.EventRoleType.FAMILY)
        check('attachment_receipt_retrievable', call('batch', operation='receipt', receipt_id=attached['receipt_id'])['after'] == attached['after'])
        check('receipt_list_contains_actual_change', attached['receipt_id'] in [r['receipt_id'] for r in call('batch', operation='receipts')['receipts']])
        repeated = [{**item, 'expected_revision': read(item['kind'], item['handle'])['revision']} for item in attachments]
        noop = call('batch_attach', changes=repeated)
        applied_noop = call('batch_attach', changes=repeated, apply=True, expected_plan=noop['plan_revision'])
        check('duplicate_attachment_noop_has_no_commit_or_receipt', applied_noop['changed_records'] == 0 and applied_noop['receipt_id'] is None and
              read('person', owner['handle'])['revision'] == attached['after'][0]['revision'])
        remove_note = [action('person', owner['handle'], 'note', note['handle'], 'remove')]
        preview_note = call('batch_attach', changes=remove_note)
        call('batch_attach', changes=remove_note, apply=True, expected_plan=preview_note['plan_revision'])
        check('owner_note_detach_preserves_nested_note', not db.get_person_from_handle(owner['handle']).get_note_list() and
              db.get_person_from_handle(owner['handle']).get_attribute_list()[0].get_note_list() == [note['handle']])
        witness = lib.EventRef()
        witness.set_role(lib.EventRoleType.WITNESS)
        witness_patch = {'role': object_to_dict(witness)['role']}
        witness_actions = [action('person', owner['handle'], 'event', event['handle'], reference_patch=witness_patch)]
        preview_witness = call('batch_attach', changes=witness_actions)
        call('batch_attach', changes=witness_actions, apply=True, expected_plan=preview_witness['plan_revision'])
        check('same_event_distinct_native_roles_preserved', len(db.get_person_from_handle(owner['handle']).get_event_ref_list()) == 2)
        reject('ambiguous_event_removal_rejected', lambda: call('batch_attach', changes=[action('person', owner['handle'], 'event', event['handle'], 'remove')]))
        selected = [action('person', owner['handle'], 'event', event['handle'], 'remove', reference_patch=witness_patch)]
        selected_preview = call('batch_attach', changes=selected)
        selected_result = call('batch_attach', changes=selected, apply=True, expected_plan=selected_preview['plan_revision'])
        check('explicit_event_selector_preserves_primary_and_index', len(db.get_person_from_handle(owner['handle']).get_event_ref_list()) == 1 and
              db.get_person_from_handle(owner['handle']).get_birth_ref().ref == event['handle'])
        selected_rollback = call('batch', operation='rollback', receipt_id=selected_result['receipt_id'])
        call('batch', operation='rollback', receipt_id=selected_result['receipt_id'], apply=True, expected_plan=selected_rollback['plan_revision'])
        media, repository = create('media'), create('repository')
        media_ref, repo_ref = lib.MediaRef(), lib.RepoRef()
        media_ref.set_rectangle((10, 10, 90, 90))
        repo_ref.set_call_number('Synthetic shelf')
        reference_actions = [action('person', owner['handle'], 'media', media['handle'],
                                    reference_patch={'rect': object_to_dict(media_ref)['rect']}),
                             action('source', source['handle'], 'repository', repository['handle'],
                                    reference_patch={'call_number': object_to_dict(repo_ref)['call_number']})]
        reference_preview = call('batch_attach', changes=reference_actions)
        call('batch_attach', changes=reference_actions, apply=True, expected_plan=reference_preview['plan_revision'])
        check('media_crop_and_repository_call_number_preserved', db.get_person_from_handle(owner['handle']).get_media_list()[0].get_rectangle() == (10, 10, 90, 90) and
              db.get_source_from_handle(source['handle']).get_reporef_list()[0].get_call_number() == 'Synthetic shelf')
        remove_events = [action('person', owner['handle'], 'event', event['handle'], 'remove', all_references=True)]
        preview_events = call('batch_attach', changes=remove_events)
        removed = call('batch_attach', changes=remove_events, apply=True, expected_plan=preview_events['plan_revision'])
        check('event_removal_repairs_birth_index', db.get_person_from_handle(owner['handle']).get_birth_ref() is None)
        rollback_events = call('batch', operation='rollback', receipt_id=removed['receipt_id'])
        call('batch', operation='rollback', receipt_id=removed['receipt_id'], apply=True, expected_plan=rollback_events['plan_revision'])
        check('attachment_rollback_restores_event_references_and_index', db.get_person_from_handle(owner['handle']).get_birth_ref().ref == event['handle'])
        empty_citation = create('citation')
        reject('source_less_citation_attachment_rejected', lambda: call('batch_attach', changes=[action('person', owner['handle'], 'citation', empty_citation['handle'])]))
        reject('direct_attachment_patch_rejected', lambda: call('batch_attach', changes=[action('person', owner['handle'], 'tag', tag['handle'], reference_patch={'value': 1})]))
        reject('attachment_action_limit_rejected', lambda: call('batch_attach', changes=attachments * 41))
        with tempfile.TemporaryDirectory(prefix='gramps-plan-') as temp:
            path = Path(temp) / 'plan.json'
            reject('in_memory_durable_plan_rejected', lambda: call('batch_file', operation='save_plan', file_path=str(path), changes=changes))
            original_path, original_id = db.get_save_path, db.get_dbid
            db.get_save_path = lambda: temp
            db.get_dbid = lambda: 'synthetic-local-tree'
            try:
                current = read('person', person['handle'])
                plan_changes = [{'kind': 'person', 'handle': person['handle'], 'expected_revision': current['revision'], 'patch': {'gender': 1}}]
                args = {'operation': 'save_plan', 'file_path': str(path), 'changes': plan_changes}
                file_preview = call('batch_file', **args)
                check('saved_plan_preview_writes_neither_file_nor_records', not path.exists() and read('person', person['handle'])['revision'] == current['revision'])
                saved = call('batch_file', **args, apply=True, expected_plan=file_preview['plan_revision'], expected_file_revision=file_preview['file_revision'])
                check('saved_plan_exact_json_and_no_record_write', path.is_file() and not saved['tree_records_changed'] and
                      read('person', person['handle'])['revision'] == current['revision'])
                reject('existing_saved_file_preserved_by_default', lambda: call('batch_file', **args))
                preview_file = call('batch_file', operation='run_plan', file_path=str(path))
                check('saved_plan_repreview_without_write', not preview_file['applied'] and read('person', person['handle'])['revision'] == current['revision'])
                renewed = module.GrampsSupport(bridge, state)
                renewed_preview = renewed.dispatch('batch_file', {'operation': 'run_plan', 'file_path': str(path)})
                check('saved_plan_survives_support_session_recreation', renewed_preview['plan_revision'] == preview_file['plan_revision'])
                original_document = json.loads(path.read_text(encoding='utf-8'))
                from support import revision
                for malformed in (None, [], 'invalid'):
                    document = copy.deepcopy(original_document)
                    document['payload']['preview'] = malformed
                    document['payload_revision'] = revision(document['payload'])
                    path.write_text(json.dumps(document), encoding='utf-8')
                    reject('malformed_saved_preview_' + str(len(checks)), lambda: call('batch_file', operation='run_plan', file_path=str(path)))
                path.write_text(json.dumps(original_document), encoding='utf-8')
                misleading = copy.deepcopy(original_document)
                misleading['payload']['preview']['proposed'][0]['data']['gender'] = 99
                misleading['payload_revision'] = revision(misleading['payload'])
                path.write_text(json.dumps(misleading), encoding='utf-8')
                reject('inconsistent_saved_review_rejected', lambda: call('batch_file', operation='run_plan', file_path=str(path)))
                for archive_type in ('plan', 'receipt'):
                    invalid_document = {**original_document, 'type': archive_type, 'payload': None, 'payload_revision': revision(None)}
                    path.write_text(json.dumps(invalid_document), encoding='utf-8')
                    reject('inspect_malformed_' + archive_type + '_rejected', lambda: call('batch_file', operation='inspect', file_path=str(path)))
                path.write_text(json.dumps(original_document), encoding='utf-8')
                preview_file = call('batch_file', operation='run_plan', file_path=str(path))
                db.get_save_path = lambda: str(Path(temp) / 'another-tree')
                reject('saved_plan_wrong_tree_rejected', lambda: call('batch_file', operation='run_plan', file_path=str(path)))
                db.get_save_path = lambda: temp
                file_applied = call('batch_file', operation='run_plan', file_path=str(path), apply=True,
                                    expected_file_revision=preview_file['file_revision'], expected_plan=preview_file['plan_revision'])
                check('saved_plan_reviewed_execution_receipt', file_applied['applied'] and read('person', person['handle'])['data']['gender'] == 1)
                db.get_save_path = lambda: str(Path(temp) / 'another-tree')
                reject('rollback_wrong_tree_rejected', lambda: call('batch', operation='rollback', receipt_id=file_applied['receipt']['receipt_id']))
                db.get_save_path = lambda: temp
                reject('saved_plan_stale_after_execution', lambda: call('batch_file', operation='run_plan', file_path=str(path)))
                archive = Path(temp) / 'receipt.json'
                export_args = {'operation': 'export_receipt', 'file_path': str(archive), 'receipt_id': file_applied['receipt']['receipt_id']}
                export_preview = call('batch_file', **export_args)
                call('batch_file', **export_args, apply=True, expected_plan=export_preview['plan_revision'], expected_file_revision=export_preview['file_revision'])
                inspected = call('batch_file', operation='inspect', file_path=str(archive))
                check('exported_receipt_complete_readback', inspected['document']['payload']['after'] == file_applied['receipt']['after'])
                reject('receipt_archive_not_an_executable_plan', lambda: call('batch_file', operation='run_plan', file_path=str(archive)))
                document = json.loads(archive.read_text(encoding='utf-8'))
                document['payload']['after'][0]['data']['gender'] = 0
                archive.write_text(json.dumps(document), encoding='utf-8')
                reject('archive_integrity_mismatch_rejected', lambda: call('batch_file', operation='inspect', file_path=str(archive)))
            finally:
                db.get_save_path, db.get_dbid = original_path, original_id
        return {'passed': len(checks), 'checks': checks, 'synthetic_database': ':memory:', 'live_family_record_writes': 0}
    finally:
        db.close()


if __name__ == '__main__':
    import json
    print(json.dumps(run_inside(), indent=2))
