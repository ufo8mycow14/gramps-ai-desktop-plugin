"""Integration outcome checks: mock Web API, portable paths and synthetic native DB."""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def offline():
    import platform_paths
    import web_support as web
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
    home = Path(tempfile.gettempdir()) / 'synthetic-home'
    for platform in ('win32', 'linux', 'darwin'):
        for version in ('6.0', '6.1'):
            path = platform_paths.addon_dir(version, {}, platform, home)
            check('addon_path_' + platform + '_' + version, 'gramps' + version.replace('.', '') in str(path))
        check('runtime_path_' + platform, platform_paths.runtime_dir({}, platform, home).is_absolute())
    check('grampshome_override', platform_paths.addon_dir('6.1', {'GRAMPSHOME': str(home)}, 'linux', home) ==
          home / 'gramps/gramps61/plugins/DesktopMCPControl')
    check('xdg_data_override', platform_paths.addon_dir('6.0', {'XDG_DATA_HOME': str(home)}, 'darwin', home) ==
          home / 'gramps/gramps60/plugins/DesktopMCPControl')
    reject('relative_runtime_rejected', lambda: platform_paths.runtime_dir({'GRAMPS_DESKTOP_RUNTIME': 'relative'}))
    reject('remote_plain_http_rejected', lambda: web.WebClient({'GRAMPS_WEB_URL': 'http://example.com'}))
    local = {'_class': 'Person', 'handle': 'p1', 'gramps_id': 'I001', 'change': 1,
             'gender': 1, 'family_list': [], 'parent_family_list': []}
    remote = {**local, 'gender': 0}
    state = {'record': remote, 'queue': False, 'writes': 0, 'version': '6.1.0',
             'task_state': 'SUCCESS', 'force_conflict': False, 'references': [], 'guard_record': None}
    local_source = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, value, status=200):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('X-Total-Count', '1')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer synthetic-token':
                return self.reply({}, 401)
            path = self.path.split('?')[0]
            if path == '/api/metadata/':
                return self.reply({'gramps': {'version': state['version']}, 'server': {'task_queue': state['queue']}})
            if path == '/api/trees/-':
                return self.reply({'id': 'synthetic-tree'})
            if path == '/api/people/p1':
                data = copy.deepcopy(state['record'])
                if 'backlinks=1' in self.path:
                    data['backlinks'] = {'family': []}
                return self.reply(data)
            if path == '/api/people/':
                return self.reply([state['record']])
            if path == '/api/sources/s1' and state['guard_record'] is not None:
                return self.reply(state['guard_record'])
            if path == '/api/search/':
                return self.reply([{'handle': 'p1', 'object_type': 'person', 'score': 1, 'object': state['record']}])
            if path == '/api/tasks/synthetic-task':
                return self.reply({'state': state['task_state'], 'result_object': []})
            return self.reply({}, 404)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/token/':
                return self.reply({'access_token': 'synthetic-token', 'refresh_token': 'synthetic-refresh'})
            if self.path == '/api/token/sync/':
                return self.reply({'access_token': 'synthetic-token'})
            if self.headers.get('Authorization') != 'Bearer synthetic-token':
                return self.reply({}, 401)
            if self.path.startswith('/api/transactions/'):
                state['writes'] += 1
                if state.pop('delete_guard_on_post', False):
                    state['guard_record'] = None
                before = {'p1': state['record'], 's1': state['guard_record']}
                if state['force_conflict'] or any(item['old'] != before.get(item['handle']) for item in data) or 'force=false' not in self.path:
                    return self.reply({'error': 'Object has changed'}, 400)
                if state['queue']:
                    return self.reply({'task': {'id': 'synthetic-task', 'href': '/api/tasks/synthetic-task'}}, 202)
                state['record'] = data[0]['new']
                state['last_transaction'] = data
                return self.reply(data)
            return self.reply({}, 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = 'http://127.0.0.1:%d' % server.server_port
    client = web.WebClient({'GRAMPS_WEB_URL': base, 'GRAMPS_WEB_TOKEN': 'synthetic-token'})
    def bridge(method, a):
        if method == 'capabilities':
            return {'state': 'done', 'result': {'gramps_version': '6.1.0'}}
        if method == 'object':
            if a['kind'] == 'source':
                return {'state': 'done', 'result': {'kind': 'source', 'handle': 's1',
                        'revision': web.revision(local_source), 'data': copy.deepcopy(local_source)}}
            return {'state': 'done', 'result': {'kind': 'person', 'handle': 'p1',
                    'revision': web.revision(local), 'data': copy.deepcopy(local)}}
        if method == 'sync_refs':
            return {'state': 'done', 'result': [{'kind': 'person', 'handle': 'p1', 'references': state['references']}]}
        if method == 'sync_apply':
            proposal = {'before': copy.deepcopy(local), 'proposed': a['changes'][0]['data']}
            plan = web.revision(proposal)
            if a.get('apply'):
                if a['expected_plan'] != plan or a['changes'][0]['expected_revision'] != web.revision(local):
                    raise ValueError('Local state changed')
                local.update(a['changes'][0]['data'])
            return {'state': 'done', 'result': {'plan_revision': plan, 'applied': a.get('apply', False)}}
        raise ValueError('Unexpected bridge call')
    args = {'records': [{'kind': 'person', 'handle': 'p1'}], 'direction': 'push'}
    try:
        check('web_authenticated_tree_identity', web.web({'operation': 'status'}, client)['tree_id'] == 'synthetic-tree')
        check('web_bare_array_search', web.web({'operation': 'search', 'query': 'Synthetic'}, client)['records'][0]['object_type'] == 'person')
        check('web_backlink_shape', 'backlinks' in web.web({'operation': 'get', 'handle': 'p1', 'backlinks': True}, client)['data'])
        login = web.WebClient({'GRAMPS_WEB_URL': base, 'GRAMPS_WEB_USERNAME': 'synthetic', 'GRAMPS_WEB_PASSWORD': 'synthetic'})
        check('web_login_token_not_returned', 'access_token' not in json.dumps(login.status()))
        sync_login = web.WebClient({'GRAMPS_WEB_URL': base, 'GRAMPS_WEB_SYNC_TOKEN': 'synthetic'})
        check('web_sync_token_authentication', sync_login.status()['tree_id'] == 'synthetic-tree')
        preview = web.web_sync(args, bridge, client)
        check('web_sync_preview_no_write', not preview['applied'] and state['writes'] == 0)
        reject('web_wrong_tree_rejected', lambda: web.web_sync({**args, 'apply': True,
               'expected_plan': preview['plan_revision'], 'expected_tree_id': 'different-tree'}, bridge, client))
        state['record']['gender'] = 2
        reject('web_remote_edit_invalidates_preview', lambda: web.web_sync({**args, 'apply': True,
               'expected_plan': preview['plan_revision'], 'expected_tree_id': 'synthetic-tree'}, bridge, client))
        preview = web.web_sync(args, bridge, client)
        pushed = web.web_sync({**args, 'apply': True, 'expected_plan': preview['plan_revision'],
                              'expected_tree_id': 'synthetic-tree'}, bridge, client)
        check('web_push_native_old_state_and_readback', pushed['applied'] and state['record']['gender'] == 1)
        state['record']['gender'] = 0
        pull_args = {**args, 'direction': 'pull'}
        preview = web.web_sync(pull_args, bridge, client)
        pulled = web.web_sync({**pull_args, 'apply': True, 'expected_plan': preview['plan_revision'],
                              'expected_tree_id': 'synthetic-tree'}, bridge, client)
        check('web_pull_native_preview_and_apply', pulled['applied'] and local['gender'] == 0)
        local['gender'] = 1
        state['force_conflict'] = True
        preview = web.web_sync(args, bridge, client)
        reject('web_concurrent_transaction_rejected', lambda: web.web_sync({**args, 'apply': True,
               'expected_plan': preview['plan_revision'], 'expected_tree_id': 'synthetic-tree'}, bridge, client))
        state['force_conflict'] = False
        state['version'] = '6.0.4'
        reject('web_gramps_release_mismatch_rejected', lambda: web.web_sync(args, bridge, client))
        state['version'], state['queue'] = '6.1.0', True
        preview = web.web_sync(args, bridge, client)
        pending = web.web_sync({**args, 'apply': True, 'expected_plan': preview['plan_revision'],
                               'expected_tree_id': 'synthetic-tree'}, bridge, client)
        check('web_async_acceptance_not_completion', pending['state'] == 'pending' and not pending['applied'])
        check('web_async_success_receipt', web.web({'operation': 'task', 'task_id': 'synthetic-task'}, client)['state'] == 'SUCCESS')
        state['task_state'] = 'FAILURE'
        check('web_async_failure_receipt', web.web({'operation': 'task', 'task_id': 'synthetic-task'}, client)['state'] == 'FAILURE')
        state['task_state'] = 'PENDING'
        check('web_async_pending_receipt', web.web({'operation': 'task', 'task_id': 'synthetic-task'}, client)['state'] == 'PENDING')
        state['queue'] = False
        state['references'] = [{'kind': 'source', 'handle': 's1'}]
        reject('web_push_missing_native_reference_rejected', lambda: web.web_sync(args, bridge, client))
        state['guard_record'] = {'_class': 'Source', 'handle': 's1', 'gramps_id': 'S001', 'change': 1, 'title': 'Synthetic guarded source'}
        preview = web.web_sync(args, bridge, client)
        check('web_reference_guard_in_reviewed_preview', preview['guard_updates'][0]['handle'] == 's1')
        state['guard_record']['title'] = 'Intervening dependency edit'
        reject('web_dependency_edit_invalidates_preview', lambda: web.web_sync({**args, 'apply': True,
               'expected_plan': preview['plan_revision'], 'expected_tree_id': 'synthetic-tree'}, bridge, client))
        preview = web.web_sync(args, bridge, client)
        web.web_sync({**args, 'apply': True, 'expected_plan': preview['plan_revision'],
                      'expected_tree_id': 'synthetic-tree'}, bridge, client)
        check('web_noop_dependency_guard_in_same_transaction', len(state['last_transaction']) == 2 and
              state['last_transaction'][1]['old'] == state['last_transaction'][1]['new'])
        local_source.update(copy.deepcopy(state['guard_record']))
        local['gender'] = 2
        selected = {**args, 'records': args['records'] + [{'kind': 'source', 'handle': 's1'}]}
        preview = web.web_sync(selected, bridge, client)
        check('web_selected_unchanged_dependency_guarded', preview['changed_records'] == 1 and
              preview['guard_updates'] == [{'kind': 'source', 'handle': 's1', 'revision': web.revision(local_source)}])
        state['delete_guard_on_post'] = True
        reject('web_selected_dependency_atomic_delete_rejected', lambda: web.web_sync({**selected, 'apply': True,
               'expected_plan': preview['plan_revision'], 'expected_tree_id': 'synthetic-tree'}, bridge, client))
        check('web_dependency_conflict_preserves_changed_record', state['record']['gender'] == 1)
        state['guard_record'] = copy.deepcopy(local_source)
        preview = web.web_sync(selected, bridge, client)
        web.web_sync({**selected, 'apply': True, 'expected_plan': preview['plan_revision'],
                      'expected_tree_id': 'synthetic-tree'}, bridge, client)
        check('web_selected_dependency_no_duplicate_updates', len(state['last_transaction']) == 2 and
              len({item['handle'] for item in state['last_transaction']}) == 2)
        graph = {('person', 'parent'): {'parent_family_list': ['old-family']},
                 ('family', 'old-family'): {'father_handle': 'child', 'child_ref_list': [{'ref': 'parent'}]}}
        proposed = {('family', 'new-family'): {'father_handle': 'parent', 'child_ref_list': [{'ref': 'child'}]}}
        reject('web_push_cycle_through_unselected_ancestry_rejected', lambda: web.check_ancestry(
               proposed, lambda kind, handle: graph[(kind, handle)]))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    return {'passed': len(checks), 'checks': checks, 'real_web_server_access': False, 'family_data_access': False}


def native():
    from gramps.plugins.db.dbapi.sqlite import SQLite
    from gramps.gen.plug import BasePluginManager
    from gramps.gen.const import PLUGINS_DIR
    from gramps.gen.errors import HandleError
    import support as module
    db = SQLite()
    db.load(':memory:')
    db.db_name = 'Synthetic integration fixtures'
    state = SimpleNamespace(db=db)
    bridge = SimpleNamespace(dbstate=state, windows=lambda: [], uistate=SimpleNamespace(emit=lambda *args: None))
    service = module.GrampsSupport(bridge, state)
    manager = BasePluginManager.get_instance()
    manager.reg_plugins(PLUGINS_DIR, state, None, load_on_reg=False)
    checks = []
    def check(name, condition):
        assert condition, name
        checks.append(name)
    def reject(name, action):
        try:
            action()
        except (ValueError, RuntimeError, HandleError):
            checks.append(name)
        else:
            raise AssertionError(name)
    def call(method, **args):
        return service.dispatch(method, args)
    def create(kind, patch=None):
        return call('mutate', kind=kind, operation='create', patch=patch or {}, apply=True)['after'][0]
    def read(kind, handle):
        return call('object', kind=kind, handle=handle)
    try:
        with tempfile.TemporaryDirectory(prefix='gramps-integration-') as temp:
            folder = Path(temp)
            person = create('person')
            other = create('person')
            db.set_default_person_handle(person['handle'])
            note = create('note')
            changes = [{'kind': 'person', 'handle': person['handle'], 'expected_revision': person['revision'], 'patch': {'gender': 1}},
                       {'kind': 'note', 'handle': note['handle'], 'expected_revision': note['revision'], 'patch': {'format': 1}}]
            preview = call('batch', changes=changes)
            check('batch_preview_preserves_database', read('person', person['handle'])['revision'] == person['revision'])
            reject('batch_missing_plan_rejected', lambda: call('batch', changes=changes, apply=True))
            invalid = copy.deepcopy(changes)
            invalid[1]['expected_revision'] = 'stale'
            reject('batch_stale_member_rejects_entire_batch', lambda: call('batch', changes=invalid, apply=True, expected_plan=preview['plan_revision']))
            check('batch_rejection_preserves_first_record', read('person', person['handle'])['revision'] == person['revision'])
            result = call('batch', changes=changes, apply=True, expected_plan=preview['plan_revision'])
            check('batch_atomic_readback', result['applied'] and read('person', person['handle'])['data']['gender'] == 1)
            rollback = call('batch', operation='rollback', receipt_id=result['receipt_id'])
            call('batch', operation='rollback', receipt_id=result['receipt_id'], apply=True, expected_plan=rollback['plan_revision'])
            check('batch_receipt_rollback', read('person', person['handle'])['data']['gender'] == person['data']['gender'])
            reject('batch_relationship_patch_rejected', lambda: call('batch', changes=[{'kind': 'person', 'handle': person['handle'],
                   'expected_revision': read('person', person['handle'])['revision'], 'patch': {'family_list': []}}]))
            file = folder / 'synthetic.txt'
            file.write_text('Synthetic media metadata fixture', encoding='utf-8')
            media = create('media', {'path': str(folder / 'missing' / 'synthetic.txt')})
            inspected = call('media_manage', handles=[media['handle']], search_roots=[str(folder)])
            check('media_missing_path_candidates_and_no_write', not inspected['media'][0]['exists'] and
                  inspected['media'][0]['candidates'] == [str(file)] and read('media', media['handle'])['revision'] == media['revision'])
            relink = [{'handle': media['handle'], 'expected_revision': media['revision'], 'path': str(file)}]
            preview = call('media_manage', operation='relink', changes=relink, refresh_mime=True)
            call('media_manage', operation='relink', changes=relink, refresh_mime=True, apply=True, expected_plan=preview['plan_revision'])
            inspected = call('media_manage', handles=[media['handle']])['media'][0]
            check('media_relink_metadata_without_move', inspected['exists'] and inspected['file_size'] == file.stat().st_size and
                  inspected['mime_type'] == 'text/plain' and file.is_file())
            catalogue = call('filter', operation='rules', kind='person')
            check('filter_native_rule_catalogue', any(item['class'] == 'HasIdOf' for item in catalogue['rules']))
            for kind in ('family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note'):
                handles = list(getattr(db, 'get_%s_handles' % kind)())
                specimen = read(kind, handles[0]) if handles else create(kind)
                rules = call('filter', operation='rules', kind=kind)
                check('filter_' + kind + '_native_rule_catalogue', any(rule['class'] == 'HasIdOf' for rule in rules['rules']))
                definition = {'name': 'Synthetic ' + kind, 'rules': [{'class': 'HasIdOf', 'values': [specimen['gramps_id']]}]}
                found = call('filter', operation='run', kind=kind, definition=definition, store_path=str(folder / 'namespace-filters.xml'))
                check('filter_' + kind + '_native_run', [record['handle'] for record in found['records']] == [specimen['handle']])
            spec = {'name': 'Synthetic selected', 'rules': [{'class': 'HasIdOf', 'values': [person['gramps_id']]}]}
            store = str(folder / 'filters.xml')
            run = call('filter', operation='run', kind='person', definition=spec, store_path=store)
            check('filter_native_run', [record['handle'] for record in run['records']] == [person['handle']])
            run = call('filter', operation='run', kind='person', definition={**spec, 'invert': True}, store_path=store)
            check('filter_native_invert', [record['handle'] for record in run['records']] == [other['handle']])
            preview = call('filter', operation='save', kind='person', definition=spec, store_path=store)
            reject('filter_stale_store_rejected', lambda: call('filter', operation='save', kind='person', definition=spec,
                   store_path=store, apply=True, expected_revision='stale'))
            saved = call('filter', operation='save', kind='person', definition=spec, store_path=store,
                         apply=True, expected_revision=preview['store_revision'])
            check('filter_native_save_load', call('filter', operation='get', kind='person', name=spec['name'], store_path=store)['definition']['name'] == spec['name'])
            dependent = {'name': 'Synthetic dependent', 'rules': [{'class': 'MatchesFilter', 'values': [spec['name']]}]}
            saved_dependent = call('filter', operation='save', kind='person', definition=dependent, store_path=store,
                                   apply=True, expected_revision=saved['store_revision'])
            original_filter_bytes = Path(store).read_bytes()
            reject('filter_dependent_delete_rejected', lambda: call('filter', operation='delete', kind='person', name=spec['name'],
                   store_path=store, apply=True, expected_revision=saved_dependent['store_revision']))
            check('filter_dependent_rejection_preserves_store', Path(store).read_bytes() == original_filter_bytes)
            removed_dependent = call('filter', operation='delete', kind='person', name=dependent['name'], store_path=store,
                                     apply=True, expected_revision=saved_dependent['store_revision'])
            saved['store_revision'] = removed_dependent['store_revision']
            bad_store = folder / 'unavailable_filters.xml'
            bad_store.write_text('<filters><object type="Person"><filter name="Other" function="and"><rule class="UnavailableRule"><arg value="x"/></rule></filter></object></filters>', encoding='utf-8')
            bad_bytes = bad_store.read_bytes()
            reject('filter_unknown_existing_rule_rejected', lambda: call('filter', operation='save', kind='person', definition=spec,
                   store_path=str(bad_store), apply=True, expected_revision='stale'))
            check('filter_unavailable_rule_bytes_preserved', bad_store.read_bytes() == bad_bytes)
            legacy_store = folder / 'legacy_filters.xml'
            legacy_store.write_text('<filters><object type="Person"><filter name="Legacy ID" function="0">'
                                   '<rule class="Has the Id"><arg value="' + person['gramps_id'] + '"/></rule></filter>'
                                   '<filter name="Qualified" function="xor"><rule class="rules.person.HasIdOf">'
                                   '<arg value="' + person['gramps_id'] + '"/></rule></filter></object>'
                                   '<object type="MediaObject"><filter name="Legacy media" function="1">'
                                   '<rule class="HasIdOf"><arg value="' + media['gramps_id'] + '"/></rule></filter></object></filters>', encoding='utf-8')
            legacy = call('filter', operation='list', kind='person', store_path=str(legacy_store))['filters']
            check('filter_native_legacy_aliases_normalised', legacy[0]['logical_op'] == 'and' and
                  legacy[0]['rules'][0]['class'] == 'HasIdOf' and legacy[1]['logical_op'] == 'one')
            check('filter_legacy_media_namespace_supported', call('filter', operation='list', kind='media',
                  store_path=str(legacy_store))['filters'][0]['logical_op'] == 'or')
            extra_store = folder / 'extra_arguments.xml'
            extra_store.write_text('<filters><object type="Person"><filter name="Too many"><rule class="HasIdOf">'
                                   '<arg value="x"/><arg value="discarded"/></rule></filter></object></filters>', encoding='utf-8')
            reject('filter_discarded_legacy_arguments_rejected', lambda: call('filter', operation='list', kind='person',
                   store_path=str(extra_store)))
            padded = {'name': ' Padded name ', 'comment': ' Padded comment ',
                      'rules': [{'class': 'HasIdOf', 'values': [' ' + person['gramps_id'] + ' ']}]}
            padded_store = str(folder / 'padded.xml')
            preview_padded = call('filter', operation='save', kind='person', definition=padded, store_path=padded_store)
            call('filter', operation='save', kind='person', definition=padded, store_path=padded_store,
                 apply=True, expected_revision=preview_padded['store_revision'])
            read_padded = call('filter', operation='get', kind='person', name=padded['name'], store_path=padded_store)['definition']
            check('filter_whitespace_roundtrip_preserved', read_padded['name'] == padded['name'] and
                  read_padded['comment'] == padded['comment'] and read_padded['rules'][0]['values'] == padded['rules'][0]['values'])
            reject('filter_argument_count_rejected', lambda: call('filter', operation='run', kind='person', store_path=store,
                   definition={'name': 'Bad', 'rules': [{'class': 'HasIdOf', 'values': []}]}))
            reject('filter_tag_namespace_rejected', lambda: call('filter', operation='rules', kind='tag'))
            deleted = call('filter', operation='delete', kind='person', name=spec['name'], store_path=store,
                           apply=True, expected_revision=saved['store_revision'])
            check('filter_native_delete_preserves_backup', not call('filter', operation='list', kind='person', store_path=store)['filters'] and
                  Path(deleted['backup_path']).is_file())
            report_id = 'summary'
            export_formats = call('export', operation='list')
            check('export_native_format_discovery', {item['format'] for item in export_formats['formats']} == {'gramps', 'xml', 'gedcom', 'gpkg'})
            import gzip
            import tarfile
            import xml.etree.ElementTree as ET
            for export_format, extension in (('gramps', 'gramps'), ('xml', 'xml'), ('gedcom', 'ged')):
                export_args = {'operation': 'run', 'format': export_format, 'output_path': str(folder / ('synthetic.' + extension))}
                export_preview = call('export', **export_args)
                check('export_' + export_format + '_preview_no_write', not Path(export_args['output_path']).exists() and not export_preview['applied'])
                exported = call('export', **export_args, apply=True, expected_plan=export_preview['plan_revision'])
                raw = Path(export_args['output_path']).read_bytes()
                if export_format == 'gramps':
                    raw = gzip.decompress(raw)
                check('export_' + export_format + '_native_output', exported['applied'] and bool(exported['sha256']) and
                      (b'0 TRLR' in raw if export_format == 'gedcom' else ET.fromstring(raw).tag.endswith('database')))
            package_args = {'operation': 'backup', 'include_media': True, 'output_path': str(folder / 'synthetic.gpkg')}
            package_preview = call('export', **package_args)
            packaged = call('export', **package_args, apply=True, expected_plan=package_preview['plan_revision'])
            with tarfile.open(package_args['output_path']) as archive:
                archive_names = archive.getnames()
                check('backup_native_xml_and_portable_media_members', 'data.gramps' in archive_names and
                      set(archive_names) == {'data.gramps'} | {item['archive_path'] for item in packaged['media']} and
                      all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive_names))
                xml = ET.fromstring(archive.extractfile('data.gramps').read())
                media_paths = [item.get('src') for item in xml.iter() if item.tag.endswith('file')]
                check('backup_xml_references_packaged_media', set(media_paths) == {item['archive_path'] for item in packaged['media']})
            check('backup_preserves_original_media', file.read_text(encoding='utf-8') == 'Synthetic media metadata fixture' and
                  read('media', media['handle'])['data']['path'] == str(file))
            from gramps.plugins.importer import importxml
            from gramps.cli.user import User
            restored_db = SQLite()
            restored_db.load(':memory:')
            try:
                restored_path = folder / 'restore-test.gramps'
                with tarfile.open(package_args['output_path']) as archive:
                    restored_path.write_bytes(archive.extractfile('data.gramps').read())
                restored = importxml.importData(restored_db, str(restored_path), User(quiet=True))
                check('backup_native_restore_roundtrip', restored and
                      restored_db.get_person_from_handle(person['handle']).get_gramps_id() == person['gramps_id'] and
                      restored_db.get_media_from_handle(media['handle']).get_path() == packaged['media'][0]['archive_path'])
            finally:
                restored_db.close()
            protected = folder / 'referenced.xml'
            protected.write_bytes(b'Referenced source must remain unchanged')
            create('media', {'path': str(protected)})
            reject('export_cannot_replace_referenced_source', lambda: call('export', operation='run', format='xml',
                   output_path=str(protected), overwrite=True))
            reject('report_cannot_replace_referenced_source', lambda: call('report', operation='run', report_id='summary',
                   format='txt', output_path=str(file), overwrite=True))
            overwrite_package = {**package_args, 'overwrite': True}
            overwrite_package_preview = call('export', **overwrite_package)
            file.write_text('Changed synthetic media', encoding='utf-8')
            reject('backup_changed_media_rejects_review', lambda: call('export', **overwrite_package,
                   apply=True, expected_plan=overwrite_package_preview['plan_revision']))
            file.unlink()
            missing_args = {**package_args, 'output_path': str(folder / 'missing.gpkg')}
            reject('backup_missing_media_rejected_by_default', lambda: call('export', **missing_args))
            missing_preview = call('export', **missing_args, allow_missing_media=True)
            check('backup_explicit_missing_media_receipt', not missing_preview['media_complete'] and missing_preview['missing_media'] == [media['handle']])
            call('export', **missing_args, allow_missing_media=True, apply=True, expected_plan=missing_preview['plan_revision'])
            with tarfile.open(missing_args['output_path']) as archive:
                incomplete_xml = ET.fromstring(archive.extractfile('data.gramps').read())
                check('backup_missing_media_path_retained', str(file).replace('\\', '/') in [item.get('src') for item in incomplete_xml.iter() if item.tag.endswith('file')])
            file.write_text('Synthetic media metadata fixture', encoding='utf-8')
            remote_media = create('media', {'path': 'https://example.invalid/synthetic-media.png'})
            remote_args = {**package_args, 'output_path': str(folder / 'remote.gpkg'), 'allow_missing_media': True}
            remote_preview = call('export', **remote_args)
            call('export', **remote_args, apply=True, expected_plan=remote_preview['plan_revision'])
            with tarfile.open(remote_args['output_path']) as archive:
                remote_xml = ET.fromstring(archive.extractfile('data.gramps').read())
                check('backup_remote_url_retained_without_download', remote_media['handle'] in remote_preview['missing_media'] and
                      'https://example.invalid/synthetic-media.png' in [item.get('src') for item in remote_xml.iter() if item.tag.endswith('file')])
            reject('export_requires_review_plan', lambda: call('export', operation='backup', output_path=str(folder / 'unchecked.gramps'), apply=True))
            reject('export_existing_output_rejected', lambda: call('export', operation='run', format='xml', output_path=str(folder / 'synthetic.xml')))
            from gramps.plugins.export import exportxml
            original_xml_write = exportxml.XmlWriter.write
            existing_export = folder / 'synthetic.xml'
            export_before = existing_export.read_bytes()
            overwrite_export = {'operation': 'run', 'format': 'xml', 'output_path': str(existing_export), 'overwrite': True}
            overwrite_export_preview = call('export', **overwrite_export)
            def failed_xml_write(writer, filename):
                original_xml_write(writer, filename)
                raise RuntimeError('Injected exporter failure after staged output')
            exportxml.XmlWriter.write = failed_xml_write
            try:
                reject('export_generation_failure_reported', lambda: call('export', **overwrite_export,
                       apply=True, expected_plan=overwrite_export_preview['plan_revision']))
            finally:
                exportxml.XmlWriter.write = original_xml_write
            check('export_failure_preserves_output_and_no_backup_sidefile', existing_export.read_bytes() == export_before and
                  not (folder / 'synthetic.xml.bak').exists() and not list(folder.glob('.synthetic.*')))
            options = call('report', operation='options', report_id=report_id)
            check('report_native_options_formats', any(item['format'] == 'txt' for item in options['formats']))
            output = folder / 'summary.txt'
            args = {'operation': 'run', 'report_id': report_id, 'format': 'txt', 'output_path': str(output)}
            preview = call('report', **args)
            check('report_preview_no_file', not output.exists() and not preview['applied'])
            generated = call('report', **args, apply=True, expected_plan=preview['plan_revision'])
            check('report_native_nonempty_output', generated['applied'] and output.stat().st_size > 0 and generated['sha256'])
            pdata = next(item for item in manager.get_reg_reports(gui=False) if item.id == report_id)
            report_module = manager.load_plugin(pdata)
            original_report = getattr(report_module, pdata.reportclass)
            class FailedReport(original_report):
                def write_report(self):
                    super().write_report()
                    raise RuntimeError('Injected report failure after writing staged output')
            overwrite_args = {**args, 'overwrite': True}
            before_bytes = output.read_bytes()
            overwrite_preview = call('report', **overwrite_args)
            setattr(report_module, pdata.reportclass, FailedReport)
            try:
                reject('report_generation_failure_rejected', lambda: call('report', **overwrite_args,
                       apply=True, expected_plan=overwrite_preview['plan_revision']))
            finally:
                setattr(report_module, pdata.reportclass, original_report)
            check('report_failure_preserves_existing_output', output.read_bytes() == before_bytes and
                  not list(folder.glob('.summary.*.txt')))
            original_options = getattr(report_module, pdata.optionclass)
            class DifferentOrientation(original_options):
                def load_previous_values(self):
                    super().load_previous_values()
                    self.handler.set_orientation(1 - self.handler.get_orientation())
            setattr(report_module, pdata.optionclass, DifferentOrientation)
            try:
                reject('report_changed_layout_invalidates_preview', lambda: call('report', **overwrite_args,
                       apply=True, expected_plan=overwrite_preview['plan_revision']))
            finally:
                setattr(report_module, pdata.optionclass, original_options)
            check('report_layout_rejection_preserves_output', output.read_bytes() == before_bytes)
            if any(item['format'] == 'pdf' for item in options['formats']):
                pdf_args = {**args, 'format': 'pdf', 'output_path': str(folder / 'summary.pdf')}
                pdf_preview = call('report', **pdf_args)
                call('report', **pdf_args, apply=True, expected_plan=pdf_preview['plan_revision'])
                check('report_native_pdf_output', (folder / 'summary.pdf').read_bytes().startswith(b'%PDF'))
            import zipfile
            for extension in ('rtf', 'odt'):
                format_args = {**args, 'format': extension, 'output_path': str(folder / ('summary.' + extension))}
                format_preview = call('report', **format_args)
                generated = call('report', **format_args, apply=True, expected_plan=format_preview['plan_revision'])
                contents = Path(format_args['output_path']).read_bytes()
                check('report_native_' + extension + '_output', generated['applied'] and
                      (contents.startswith(b'{\\rtf') if extension == 'rtf' else zipfile.is_zipfile(format_args['output_path'])))
            css = folder / 'custom.css'
            css.write_text('body { color: #123456; }', encoding='utf-8')
            html_args = {**args, 'format': 'html', 'output_path': str(folder / 'html-bundle/summary.html'), 'bundle': True,
                         'document': {'paper': 'A4', 'orientation': 'landscape',
                                      'margins_cm': {'left': 1, 'right': 1.5, 'top': 2, 'bottom': 2}, 'css_path': str(css)}}
            html_options = call('report', operation='options', report_id=report_id, format='html')
            html_args['document']['style'] = html_options['styles'][0]
            html_preview = call('report', **html_args)
            check('report_document_settings_bound', html_preview['document_settings']['papero'] == 1 and
                  html_preview['document_settings']['paperml'] == 1 and html_preview['document_settings']['effective_paper']['name'] == 'A4' and
                  html_preview['document_settings']['effective_style'] == html_args['document']['style'])
            html_result = call('report', **html_args, apply=True, expected_plan=html_preview['plan_revision'])
            html_path = Path(html_args['output_path'])
            artifacts = html_result['artifacts']
            check('report_html_manifest_covers_all_files', {item['path'] for item in artifacts} ==
                  {str(path.relative_to(html_path.parent)) for path in html_path.parent.rglob('*') if path.is_file()} and len(artifacts) > 1)
            from html.parser import HTMLParser
            from urllib.parse import urlparse, unquote
            class References(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.paths = []
                def handle_starttag(self, tag, attributes):
                    for name, value in attributes:
                        if name in ('href', 'src') and value and not urlparse(value).scheme and not value.startswith('#'):
                            self.paths.append(unquote(urlparse(value).path))
            references = References()
            references.feed(html_path.read_text(encoding='utf-8'))
            check('report_html_companion_references_resolve', bool(references.paths) and
                  all((html_path.parent / value).is_file() for value in references.paths))
            check('report_custom_css_published', any(path.read_bytes() == css.read_bytes() for path in html_path.parent.rglob('*.css')))
            html_before = {str(path): path.read_bytes() for path in html_path.parent.rglob('*') if path.is_file()}
            reject('report_existing_bundle_rejected', lambda: call('report', **{**html_args, 'overwrite': True}))
            check('report_existing_bundle_preserved', all(Path(path).read_bytes() == data for path, data in html_before.items()))
            svg_args = {'operation': 'run', 'report_id': 'ancestor_chart', 'format': 'svg',
                        'output_path': str(folder / 'svg-bundle/ancestors.svg'), 'bundle': True,
                        'options': {'pid': person['gramps_id']}, 'document_options': {'svg_background': 'white'}}
            svg_preview = call('report', **svg_args)
            svg_result = call('report', **svg_args, apply=True, expected_plan=svg_preview['plan_revision'])
            check('report_svg_native_option_and_output', svg_preview['document_options']['svg_background']['value'] == 'white' and
                  b'<svg' in Path(svg_args['output_path']).read_bytes() and bool(svg_result['artifacts']))
            for document in ({'paper': 'invalid'}, {'orientation': 'diagonal'}, {'margins_cm': {'left': float('nan')}},
                             {'margins_cm': {'left': 1000}}, {'style': 'missing'}, {'css_path': str(css)}):
                reject('report_invalid_document_' + str(len(checks)), lambda document=document: call('report',
                       operation='options', report_id=report_id, format='txt', document=document))
            reject('report_unknown_document_option_rejected', lambda: call('report', operation='options', report_id='ancestor_chart',
                   format='svg', document_options={'unknown': True}))
            reject('report_invalid_document_option_choice_rejected', lambda: call('report', operation='options', report_id='ancestor_chart',
                   format='svg', document_options={'svg_background': 'invisible'}))
            reject('report_html_requires_bundle', lambda: call('report', **{**html_args, 'bundle': False, 'output_path': str(folder / 'plain.html')}))
            reject('report_latex_requires_bundle', lambda: call('report', **{**args, 'format': 'tex', 'output_path': str(folder / 'summary.tex')}))
            from gi.repository import GdkPixbuf
            image_path = folder / 'synthetic-image.png'
            image_fixture = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 40)
            image_fixture.fill(0x0a141e64)
            image_fixture.savev(str(image_path), 'png', [], [])
            source_bytes = image_path.read_bytes()
            existing_jpeg = image_path.with_suffix('.jpg')
            existing_jpeg.write_bytes(b'Existing sibling must remain unchanged')
            image_media = create('media', {'path': str(image_path), 'mime': 'image/png'})
            current_person = read('person', person['handle'])
            call('attach', kind='person', handle=person['handle'], expected_revision=current_person['revision'],
                 target_kind='media', target_handle=image_media['handle'], apply=True)
            class ImageReport(original_report):
                def write_report(self):
                    super().write_report()
                    self.doc.add_media(str(image_path), 0, 4, 4, crop=(0, 0, 50, 100))
                    self.doc.add_media(str(image_path), 0, 4, 4, crop=(0, 0, 50, 100))
                    self.doc.add_media(str(image_path), 0, 4, 4)
            setattr(report_module, pdata.reportclass, ImageReport)
            latex_args = {**args, 'format': 'tex', 'output_path': str(folder / 'latex-bundle/summary.tex'), 'bundle': True}
            try:
                latex_preview = call('report', **latex_args)
                latex_result = call('report', **latex_args, apply=True, expected_plan=latex_preview['plan_revision'])
            finally:
                setattr(report_module, pdata.reportclass, original_report)
            tex = Path(latex_args['output_path']).read_text(encoding='utf-8')
            assets = list(Path(latex_args['output_path']).parent.glob('media/*.jpg'))
            check('report_native_latex_with_portable_image_assets', b'\\documentclass' in Path(latex_args['output_path']).read_bytes() and
                  len(assets) == 2 and all('media/' + image.name in tex for image in assets) and str(folder) not in tex and
                  len(latex_result['artifacts']) == 3)
            derived_images = [GdkPixbuf.Pixbuf.new_from_file(str(asset)) for asset in assets]
            check('report_rgba_and_crop_preserved_in_derived_assets', {(image.get_width(), image.get_height()) for image in derived_images} == {(20, 40), (10, 40)} and
                  all(not image.get_has_alpha() for image in derived_images))
            check('report_original_and_sibling_jpeg_preserved', image_path.read_bytes() == source_bytes and
                  existing_jpeg.read_bytes() == b'Existing sibling must remain unchanged')
            portrait_path = folder / 'synthetic-portrait.bmp'
            image_fixture.savev(str(portrait_path), 'bmp', [], [])
            portrait_bytes = portrait_path.read_bytes()
            create('media', {'path': str(portrait_path), 'mime': 'image/bmp'})
            class PortraitReport(original_report):
                def write_report(self):
                    super().write_report()
                    self.doc.add_media(str(portrait_path), 0, 4, 4, crop=(0, 0, 50, 100))
                    self.doc.add_media(str(portrait_path), 0, 4, 4, crop=(0, 0, 50, 100))
            setattr(report_module, pdata.reportclass, PortraitReport)
            portrait_args = {**latex_args, 'output_path': str(folder / 'portrait-bundle/summary.tex')}
            try:
                portrait_preview = call('report', **portrait_args)
                call('report', **portrait_args, apply=True, expected_plan=portrait_preview['plan_revision'])
            finally:
                setattr(report_module, pdata.reportclass, original_report)
            portrait_tex = Path(portrait_args['output_path']).read_text(encoding='utf-8')
            check('report_non_jpeg_portrait_sizing_and_cached_asset', portrait_tex.count('}{4}{8.0}{') == 2 and
                  len(list(Path(portrait_args['output_path']).parent.glob('media/*.jpg'))) == 1)
            check('report_non_jpeg_original_preserved', portrait_path.read_bytes() == portrait_bytes and
                  not portrait_path.with_suffix('.jpg').exists())
            stale_image_args = {**latex_args, 'output_path': str(folder / 'stale-image-bundle/summary.tex')}
            stale_image_preview = call('report', **stale_image_args)
            image_fixture.fill(0x323c4664)
            image_fixture.savev(str(image_path), 'png', [], [])
            reject('report_source_image_changes_invalidate_preview', lambda: call('report', **stale_image_args, apply=True,
                   expected_plan=stale_image_preview['plan_revision']))
            check('report_image_staleness_creates_no_bundle', not Path(stale_image_args['output_path']).parent.exists())
            image_path.write_bytes(source_bytes)
            pdf_image_args = {**args, 'format': 'pdf', 'output_path': str(folder / 'image-state.pdf')}
            pdf_image_preview = call('report', **pdf_image_args)
            image_fixture.fill(0x323c4664)
            image_fixture.savev(str(image_path), 'png', [], [])
            reject('report_pdf_source_image_changes_invalidate_preview', lambda: call('report', **pdf_image_args, apply=True,
                   expected_plan=pdf_image_preview['plan_revision']))
            image_path.write_bytes(source_bytes)
            import report_media
            from gramps.gen.plug.docgen import treedoc, PaperStyle
            from gramps.gen.plug.report._paper import paper_sizes
            from gramps.gen.plug.report import MenuReportOptions
            class TreeFixtureOptions(MenuReportOptions):
                def add_menu_options(self, menu):
                    pass
            tree_options = TreeFixtureOptions('synthetic-tree-generator', db)
            treedoc.TreeOptions().add_menu_options(tree_options.menu)
            paper = next(paper for paper in paper_sizes if paper.get_name() == 'A4')
            tree_bundle = folder / 'tree-bundle'
            tree_bundle.mkdir()
            tree_doc = treedoc.TreeTexDoc(tree_options, PaperStyle(paper, 0, 1, 1, 1, 1))
            report_media.configure(tree_doc, tree_bundle, report_media.inputs(db), category_tree=True)
            original_tree_write = tree_doc.write
            tree_doc.open(str(tree_bundle / 'tree.tex'))
            tree_doc.write_node(db, 1, 'g', db.get_person_from_handle(person['handle']), False)
            tree_doc.close()
            tree_text = (tree_bundle / 'tree.tex').read_text(encoding='utf-8')
            tree_assets = list(tree_bundle.glob('media/*.png'))
            check('report_tree_native_source_and_private_thumbnail', len(tree_assets) == 1 and
                  'media/' + tree_assets[0].name in tree_text and str(folder) not in tree_text and
                  max(GdkPixbuf.Pixbuf.new_from_file(str(tree_assets[0])).get_width(), GdkPixbuf.Pixbuf.new_from_file(str(tree_assets[0])).get_height()) == 96)
            check('report_tree_preserves_person_media_and_restores_hook', len(db.get_person_from_handle(person['handle']).get_media_list()) == 1 and
                  tree_doc.write == original_tree_write and image_path.read_bytes() == source_bytes)
            original_write = tree_doc.write
            def fail_tree_write(*arguments):
                raise RuntimeError('Injected tree rendering failure')
            tree_doc.write = fail_tree_write
            reject('report_tree_render_failure_propagates', lambda: tree_doc.write_node(db, 1, 'g', db.get_person_from_handle(person['handle']), False))
            check('report_tree_failure_restores_document_hook', tree_doc.write == fail_tree_write)
            tree_doc.write = original_write
            native_report_list, native_load_plugin = manager.get_reg_reports, manager.load_plugin
            from gramps.gen.plug.report import CATEGORY_TREE
            tree_plugin = SimpleNamespace(id='synthetic-tree-source', name='Synthetic native tree fixture', description='Synthetic only',
                                          category=CATEGORY_TREE,
                                          optionclass='Options', reportclass='Report')
            class TreeFixtureReport:
                def __init__(self, database, options, user):
                    self.db, self.doc = database, options.handler.doc
                    self.output = options.handler.output
                def begin_report(self):
                    self.doc.open(self.output)
                def write_report(self):
                    self.doc.write_node(self.db, 1, 'g', self.db.get_person_from_handle(person['handle']), False)
                def end_report(self):
                    self.doc.close()
            fixture_module = SimpleNamespace(Options=TreeFixtureOptions, Report=TreeFixtureReport)
            manager.get_reg_reports = lambda gui=False: native_report_list(gui=gui) + ([] if gui else [tree_plugin])
            manager.load_plugin = lambda plugin: fixture_module if plugin is tree_plugin else native_load_plugin(plugin)
            try:
                for tree_format in ('tex', 'graph'):
                    tree_args = {'operation': 'run', 'report_id': tree_plugin.id, 'format': tree_format, 'bundle': True,
                                 'output_path': str(folder / ('native-tree-' + tree_format) / ('tree.' + tree_format))}
                    preview_tree = call('report', **tree_args)
                    output_tree = call('report', **tree_args, apply=True, expected_plan=preview_tree['plan_revision'])
                    check('report_tree_' + tree_format + '_dispatch_bundle', output_tree['applied'] and
                          len(output_tree['artifacts']) == 2 and 'media/' in Path(tree_args['output_path']).read_text(encoding='utf-8'))
            finally:
                manager.get_reg_reports, manager.load_plugin = native_report_list, native_load_plugin
            import report_output
            stage = folder / 'publication-stage'
            stage.mkdir()
            (stage / 'asset.css').write_text('synthetic', encoding='utf-8')
            (stage / 'index.html').write_text('synthetic', encoding='utf-8')
            destination = folder / 'failed-publication'
            original_link = os.link
            def fail_main(source, target, *positional, **keywords):
                if Path(target).name == 'index.html':
                    raise RuntimeError('Injected bundle publication failure')
                return original_link(source, target, *positional, **keywords)
            os.link = fail_main
            try:
                reject('report_bundle_publication_failure_reported', lambda: report_output.publish_bundle(stage, destination, 'index.html'))
            finally:
                os.link = original_link
            check('report_bundle_failure_cleans_only_owned_output', not destination.exists() and (stage / 'asset.css').is_file())
            from gramps.gen.plug.report import CATEGORY_GRAPHVIZ, CATEGORY_TREE
            reports = call('report', operation='list')['reports']
            check('report_tree_source_automation_advertised', all(item['automatable'] for item in reports if item['category'] == CATEGORY_TREE))
            for category, name in ((CATEGORY_GRAPHVIZ, 'graphviz'), (CATEGORY_TREE, 'tree')):
                candidates = [item for item in reports if item['automatable'] and item['category'] == category]
                if candidates:
                    native = call('report', operation='options', report_id=candidates[0]['id'])
                    check('report_native_' + name + '_options_and_formats', bool(native['formats']) and bool(native['options']))
            reject('report_overwrite_rejected', lambda: call('report', **args))
            reject('report_unknown_option_rejected', lambda: call('report', operation='options', report_id=report_id, options={'not_an_option': 1}))
            reject('report_unsupported_format_rejected', lambda: call('report', **{**args, 'output_path': str(folder / 'unknown.xyz'), 'format': 'xyz'}))
            reject('report_missing_person_id_rejected', lambda: call('report', operation='options', report_id='ancestor_report', options={'pid': 'I_MISSING'}))
            current = read('person', person['handle'])
            data = copy.deepcopy(current['data'])
            data['gender'] = 1
            pull = [{'kind': 'person', 'handle': person['handle'], 'expected_revision': current['revision'], 'data': data}]
            preview = call('sync_apply', changes=pull)
            call('sync_apply', changes=pull, apply=True, expected_plan=preview['plan_revision'])
            check('web_pull_native_transaction', read('person', person['handle'])['data']['gender'] == 1)
            cyclic_person = create('person')
            cyclic_family = create('family')
            pstate = read('person', cyclic_person['handle'])
            fstate = read('family', cyclic_family['handle'])
            from gramps.gen import lib
            from gramps.gen.lib.json_utils import object_to_dict
            child = lib.ChildRef()
            child.ref = cyclic_person['handle']
            pdata = copy.deepcopy(pstate['data'])
            pdata['family_list'] = [cyclic_family['handle']]
            pdata['parent_family_list'] = [cyclic_family['handle']]
            fdata = copy.deepcopy(fstate['data'])
            fdata.update(father_handle=cyclic_person['handle'], child_ref_list=[object_to_dict(child)])
            cycle = [{'kind': 'person', 'handle': cyclic_person['handle'], 'expected_revision': pstate['revision'], 'data': pdata},
                     {'kind': 'family', 'handle': cyclic_family['handle'], 'expected_revision': fstate['revision'], 'data': fdata}]
            reject('web_pull_reciprocal_ancestry_cycle_rejected', lambda: call('sync_apply', changes=cycle))
            data['family_list'] = ['missing-family']
            reject('web_pull_dangling_relationship_rejected', lambda: call('sync_apply', changes=[{**pull[0],
                   'expected_revision': read('person', person['handle'])['revision'], 'data': data}]))
    finally:
        db.close()
    return {'passed': len(checks), 'checks': checks, 'synthetic_database': ':memory:', 'live_family_record_writes': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help='Run only in an isolated installed Gramps runtime')
    args = parser.parse_args()
    print(json.dumps(native() if args.native else offline(), indent=2))
