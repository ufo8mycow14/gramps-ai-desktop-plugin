"""Authenticated Gramps Web access and scoped, optimistic two-way synchronisation."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

KINDS = {'person': ('people', 'Person'), 'family': ('families', 'Family'),
         'event': ('events', 'Event'), 'place': ('places', 'Place'), 'source': ('sources', 'Source'),
         'citation': ('citations', 'Citation'), 'repository': ('repositories', 'Repository'),
         'media': ('media', 'Media'), 'note': ('notes', 'Note'), 'tag': ('tags', 'Tag')}


def revision(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class WebClient:
    def __init__(self, env=None):
        self.env = os.environ if env is None else env
        self.base = self.env.get('GRAMPS_WEB_URL', '').rstrip('/')
        url = urllib.parse.urlsplit(self.base)
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Set GRAMPS_WEB_URL to the explicit server URL, without credentials or query parameters')
        if url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Remote Gramps Web connections require HTTPS')
        if self.base.endswith('/api'):
            self.base = self.base[:-4]
        self.token = self.env.get('GRAMPS_WEB_TOKEN', '')
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method='GET', data=None, params=None, authenticate=True):
        if not path.startswith('/') or '..' in path.split('/') or path.startswith('//'):
            raise ValueError('Invalid API-relative path')
        if authenticate and not self.token:
            self.login()
        url = self.base + '/api' + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        headers = {'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if authenticate:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError('API response exceeds 16 MiB; request a smaller scope')
                return {'status': response.status, 'data': json.loads(raw) if raw else None,
                        'total': response.headers.get('X-Total-Count'), 'etag': response.headers.get('ETag')}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {'status': 404, 'data': None, 'total': None, 'etag': None}
            # Avoid reflecting token/login response bodies or credentials in errors.
            raise ValueError('Gramps Web HTTP %d for %s; check access, compatibility and current revisions' %
                             (exc.code, path)) from None
        except (TimeoutError, urllib.error.URLError):
            if method != 'GET':
                raise RuntimeError('Web write outcome is unknown; inspect task/history before retrying') from None
            raise RuntimeError('Gramps Web could not be reached within the request timeout') from None

    def login(self):
        sync_token = self.env.get('GRAMPS_WEB_SYNC_TOKEN')
        if sync_token:
            response = self.request('/token/sync/', 'POST', {'token': sync_token}, authenticate=False)
        else:
            username, password = self.env.get('GRAMPS_WEB_USERNAME'), self.env.get('GRAMPS_WEB_PASSWORD')
            if not username or not password:
                raise ValueError('Set GRAMPS_WEB_TOKEN, GRAMPS_WEB_SYNC_TOKEN or local username/password environment variables')
            response = self.request('/token/', 'POST', {'username': username, 'password': password}, authenticate=False)
        self.token = (response.get('data') or {}).get('access_token', '')
        if not self.token:
            raise ValueError('Gramps Web authentication did not return an access token')

    def status(self):
        metadata = self.request('/metadata/')['data']
        tree = self.request('/trees/-')['data']
        if not isinstance(metadata, dict) or not isinstance(tree, dict) or not tree.get('id'):
            raise ValueError('Server metadata or authenticated tree identity is unavailable')
        return {'url': self.base, 'tree_id': tree['id'], 'metadata': metadata}

    def get(self, kind, handle, backlinks=False):
        if kind not in KINDS or not handle or '/' in handle:
            raise ValueError('Supply a supported kind and native record handle')
        return self.request('/%s/%s' % (KINDS[kind][0], urllib.parse.quote(handle, safe='')),
                            params={'backlinks': 1} if backlinks else None)


def web(a, client=None):
    client = client or WebClient()
    op = a.get('operation', 'status')
    if op == 'status':
        return client.status()
    if op == 'schema':
        return client.request('/openapi.json')['data']
    if op == 'task':
        ident = a.get('task_id', '')
        if not ident or not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
            raise ValueError('Supply the returned task ID')
        response = client.request('/tasks/' + ident)
        if response['status'] == 404:
            raise ValueError('Web task not found; do not assume the write failed')
        return response['data']
    if op == 'history':
        params = {'old': 1, 'new': 1, 'page': max(1, a.get('page', 1)), 'pagesize': max(1, min(a.get('limit', 20), 100))}
        if a.get('after_id'):
            params['after_id'] = a['after_id']
        return client.request('/transactions/history/', params=params)
    kind = a.get('kind', 'person')
    if kind not in KINDS:
        raise ValueError('Unsupported Web record kind')
    if op == 'get':
        return client.get(kind, a.get('handle'), a.get('backlinks', False))
    params = {'page': max(1, a.get('page', 1)), 'pagesize': max(1, min(a.get('limit', 20), 100))}
    if op == 'search':
        params.update(query=a.get('query', ''), type=kind)
        response = client.request('/search/', params=params)
    elif op == 'list':
        response = client.request('/' + KINDS[kind][0] + '/', params=params)
    else:
        raise ValueError('Unknown Web operation')
    return {'records': response['data'], 'total': response['total'], 'page': params['page'],
            'next_page': params['page'] + 1 if len(response['data'] or []) == params['pagesize'] else None}


def release_line(value):
    match = re.match(r'^(\d+\.\d+)', str(value))
    return match[1] if match else None


def raw_record(data, kind, handle):
    if not isinstance(data, dict) or data.get('_class') != KINDS[kind][1] or data.get('handle') != handle:
        raise ValueError('Remote record class/handle differs; identity reconciliation is required')
    if 'backlinks' in data or 'extended' in data:
        raise ValueError('Sync requires undecorated native JSON records')
    return data


def check_reciprocal(records, lookup):
    for (kind, handle), obj in records.items():
        if kind == 'person':
            for fam_handle in obj.get('family_list', []):
                fam = lookup('family', fam_handle)
                if handle not in (fam.get('father_handle'), fam.get('mother_handle')):
                    raise ValueError('Web sync would break reciprocal partner links')
            for fam_handle in obj.get('parent_family_list', []):
                if handle not in [ref['ref'] for ref in lookup('family', fam_handle).get('child_ref_list', [])]:
                    raise ValueError('Web sync would break reciprocal child links')
        if kind == 'family':
            for person in (obj.get('father_handle'), obj.get('mother_handle')):
                if person and handle not in lookup('person', person).get('family_list', []):
                    raise ValueError('Include the reciprocal Web person update')
            for child in obj.get('child_ref_list', []):
                if handle not in lookup('person', child['ref']).get('parent_family_list', []):
                    raise ValueError('Include the reciprocal Web child update')


def check_ancestry(records, lookup):
    families = {handle: obj for (kind, handle), obj in records.items() if kind == 'family'}
    for (kind, handle), obj in records.items():
        if kind == 'person':
            for family in obj.get('family_list', []) + obj.get('parent_family_list', []):
                families[family] = lookup('family', family)
    def ancestor_reaches(start, target):
        todo, seen = [start], set()
        while todo:
            handle = todo.pop()
            if handle == target:
                return True
            if handle in seen:
                continue
            seen.add(handle)
            if len(seen) > 2000:
                raise ValueError('Remote ancestry exceeds the bounded scoped-sync guard')
            person = lookup('person', handle)
            for family in person.get('parent_family_list', []):
                fam = lookup('family', family)
                todo.extend(h for h in (fam.get('father_handle'), fam.get('mother_handle')) if h)
        return False
    for family in families.values():
        for parent in (family.get('father_handle'), family.get('mother_handle')):
            for child in family.get('child_ref_list', []):
                if parent and ancestor_reaches(parent, child['ref']):
                    raise ValueError('Remote sync would introduce an ancestry cycle')


def web_sync(a, bridge_call, client=None):
    if a.get('operation', 'preview') == 'open_native':
        return bridge_call('workflow', {'operation': 'tool', 'action_name': 'gramps_web_sync'})
    client = client or WebClient()
    status = client.status()
    capabilities = bridge_call('capabilities', {})
    if capabilities.get('error') or capabilities.get('state') != 'done':
        raise ValueError('Read the connected desktop capabilities before synchronisation')
    local_version = capabilities['result'].get('gramps_version')
    remote_version = status['metadata'].get('gramps', {}).get('version')
    if not release_line(local_version) or release_line(local_version) != release_line(remote_version):
        raise ValueError('Web sync requires matching Gramps major/minor releases (desktop %s, server %s)' %
                         (local_version, remote_version))
    requested = a.get('records', [])
    if not 1 <= len(requested) <= 200 or len({(r['kind'], r['handle']) for r in requested}) != len(requested):
        raise ValueError('Supply 1–200 distinct existing records by kind and shared native handle')
    direction = a.get('direction', 'push')
    if direction not in ('push', 'pull'):
        raise ValueError('Direction must be push or pull')
    local, remote, snapshots, transactions, updates = {}, {}, {}, [], []
    for item in requested:
        kind, handle = item['kind'], item['handle']
        response = bridge_call('object', {'kind': kind, 'handle': handle})
        if response.get('error') or response.get('state') != 'done':
            raise ValueError('Local sync record could not be read')
        snap = response['result']
        other = client.get(kind, handle)
        if other['status'] == 404:
            raise ValueError('Scoped sync requires records already shared by handle; use native whole-tree sync for adds/deletes')
        raw = raw_record(other['data'], kind, handle)
        if snap['data'].get('gramps_id') != raw.get('gramps_id'):
            raise ValueError('Record Gramps IDs differ; reconcile identity before synchronising')
        local[(kind, handle)], remote[(kind, handle)], snapshots[(kind, handle)] = snap['data'], raw, snap
        # Change timestamps differ across backends without representing a field edit.
        equivalent = lambda obj: {key: value for key, value in obj.items() if key != 'change'}
        if equivalent(snap['data']) != equivalent(raw):
            transactions.append({'type': 'update', '_class': KINDS[kind][1], 'handle': handle,
                                 'old': raw, 'new': snap['data']})
            updates.append({'kind': kind, 'handle': handle, 'expected_revision': snap['revision'], 'data': raw})
    changes = [{'kind': item['kind'], 'handle': item['handle'], 'local': local[(item['kind'], item['handle'])],
                'remote': remote[(item['kind'], item['handle'])]} for item in requested]
    guards = {}
    local_preview = None
    if direction == 'pull' and updates:
        local_preview = bridge_call('sync_apply', {'changes': updates, 'apply': False})
        if local_preview.get('error') or local_preview.get('state') != 'done':
            raise ValueError('Local sync validation failed: ' + str(local_preview.get('error', 'desktop unavailable')))
    if direction == 'push':
        overlay = dict(local)
        changed_keys = {(item['_class'], item['handle']) for item in transactions}
        def lookup(kind, handle):
            key = (kind, handle)
            if key not in overlay:
                response = client.get(kind, handle)
                if response['status'] == 404:
                    raise ValueError('Remote referenced record missing; include it using native whole-tree sync')
                overlay[key] = raw_record(response['data'], kind, handle)
            # Selected unchanged dependencies also need an atomic old-state
            # guard; their presence in the overlay does not put them in the write.
            if (KINDS[kind][1], handle) not in changed_keys:
                guards[key] = remote.get(key, overlay[key])
                if len(guards) > 2000:
                    raise ValueError('Reference guards exceed scoped sync; use the native whole-tree workflow')
            return overlay[key]
        # Validate both new and removed reciprocal memberships. Include old linked
        # objects in the check so a removal cannot leave a remote backlink behind.
        related = dict(local)
        for (kind, handle), obj in remote.items():
            if kind == 'person':
                for family in obj.get('family_list', []) + obj.get('parent_family_list', []):
                    related[('family', family)] = lookup('family', family)
            if kind == 'family':
                people = [obj.get('father_handle'), obj.get('mother_handle')] + [ref['ref'] for ref in obj.get('child_ref_list', [])]
                for person in people:
                    if person:
                        related[('person', person)] = lookup('person', person)
        check_reciprocal(related, lookup)
        check_ancestry(related, lookup)
        references = bridge_call('sync_refs', {'records': [
            {'kind': kind, 'data': obj} for (kind, handle), obj in local.items()]})
        if references.get('state') != 'done' or references.get('error'):
            raise ValueError('Native reference discovery failed; do not push unvalidated JSON')
        for item in references['result']:
            for ref in item['references']:
                lookup(ref['kind'], ref['handle'])
    guard_list = [{'kind': kind, 'handle': handle, 'revision': revision(obj)}
                  for (kind, handle), obj in sorted(guards.items())]
    plan = revision({'tree_id': status['tree_id'], 'server': status['url'], 'direction': direction,
                     'versions': [local_version, remote_version], 'records': changes,
                     'guard_states': [{'kind': kind, 'handle': handle, 'data': obj}
                                      for (kind, handle), obj in sorted(guards.items())]})
    result = {'applied': False, 'direction': direction, 'tree_id': status['tree_id'],
              'plan_revision': plan, 'changes': changes, 'changed_records': len(updates),
              'files_transferred': False, 'scope': 'Explicit existing shared handles; no identity inference',
              'guard_updates': guard_list,
              'guard_effect': 'Remote dependencies receive no-op updates in the same guarded transaction; native change timestamps/history may advance'}
    if not a.get('apply', False):
        return result
    if a.get('expected_tree_id') != status['tree_id'] or a.get('expected_plan') != plan:
        raise ValueError('Sync target or either record state changed; preview again before applying')
    if not updates:
        return {**result, 'applied': True, 'changed_records': 0}
    if direction == 'pull':
        outcome = bridge_call('sync_apply', {'changes': updates, 'apply': True,
                             'expected_plan': local_preview['result']['plan_revision']})
        if outcome.get('state') != 'done' or outcome.get('error'):
            return {**result, 'state': outcome.get('state'), 'desktop_operation': outcome, 'applied': False}
        return {**result, 'applied': True, 'receipt': outcome['result']}
    queue = bool(status['metadata'].get('server', {}).get('task_queue'))
    transactions.extend({'type': 'update', '_class': KINDS[kind][1], 'handle': handle, 'old': obj, 'new': obj}
                        for (kind, handle), obj in sorted(guards.items()))
    response = client.request('/transactions/', 'POST', transactions,
                              {'force': 'false', 'background': '1' if queue else '0'})
    if response['status'] == 202:
        task = (response['data'] or {}).get('task', {})
        task_id = task.get('id')
        if not task_id:
            raise RuntimeError('Web transaction accepted without a task ID; inspect history before retrying')
        return {**result, 'state': 'pending', 'task_id': task_id, 'accepted': True,
                'message': 'Query gramps_web operation=task; accepted is not completed synchronisation'}
    if response['status'] not in (200, 201):
        raise RuntimeError('Unexpected transaction outcome; inspect Web history before retrying')
    verified = []
    for item in requested:
        kind, handle = item['kind'], item['handle']
        saved = client.get(kind, handle)['data']
        if {k: v for k, v in saved.items() if k != 'change'} != {k: v for k, v in local[(kind, handle)].items() if k != 'change'}:
            raise RuntimeError('Web write readback differs; inspect the tree before retrying')
        verified.append({'kind': kind, 'handle': handle, 'revision': revision(saved)})
    return {**result, 'applied': True, 'after': verified}
