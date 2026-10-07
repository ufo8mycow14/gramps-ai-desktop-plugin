"""Explicit local JSON plans and receipt archives; never execute embedded code."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

MAX_FILE = 16 * 1024 * 1024


def file_revision(path):
    if not path.exists():
        return hashlib.sha256(b'absent-batch-file').hexdigest()
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError('Batch files must be regular JSON files no larger than 16 MiB')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def envelope(batches, kind, payload):
    return {'format': 'gramps-desktop-batch', 'schema_version': 1, 'type': kind,
            'payload': payload, 'payload_revision': batches.revision(payload)}


def validate_payload(kind, payload):
    if not isinstance(payload, dict):
        raise ValueError('Batch document payload must be an object')
    if kind == 'receipt':
        if not isinstance(payload.get('receipt_id'), str) or not payload['receipt_id']:
            raise ValueError('Receipt archive requires its receipt ID')
        before, after = payload.get('before'), payload.get('after')
        if not isinstance(before, list) or not isinstance(after, list) or not before or len(before) != len(after):
            raise ValueError('Receipt requires complete matching before/after snapshots')
        for snapshot in before + after:
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('data'), dict) or any(
                    not isinstance(snapshot.get(field), str) for field in ('kind', 'handle', 'revision')):
                raise ValueError('Invalid receipt record snapshot')
        return
    if set(payload) != {'batch_type', 'arguments', 'tree', 'preview'} or not isinstance(payload['tree'], dict):
        raise ValueError('Invalid saved plan payload')
    if payload['batch_type'] not in ('update', 'attachments'):
        raise ValueError('Saved plan batch type must be update or attachments')
    arguments = payload['arguments']
    if not isinstance(arguments, dict) or set(arguments) - {'changes', 'label'} or 'changes' not in arguments:
        raise ValueError('Saved plans contain only changes and an optional label')
    if not isinstance(arguments['changes'], list) or ('label' in arguments and not isinstance(arguments['label'], str)):
        raise ValueError('Saved plan changes must be an array and label must be a string')
    if not isinstance(payload['preview'], dict) or not isinstance(payload['preview'].get('plan_revision'), str):
        raise ValueError('Saved plan preview must contain a plan revision')


def dispatch(batches, a):
    path = Path(a.get('file_path', ''))
    if not path.is_absolute() or path.suffix.lower() != '.json' or not path.parent.is_dir():
        raise ValueError('Supply an absolute .json file in an existing directory')
    current = file_revision(path)
    op = a.get('operation')
    if op in ('inspect', 'run_plan'):
        if not path.is_file():
            raise ValueError('Saved batch file not found')
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict) or set(document) != {'format', 'schema_version', 'type', 'payload', 'payload_revision'}:
            raise ValueError('Invalid batch document fields')
        if document['format'] != 'gramps-desktop-batch' or document['schema_version'] != 1:
            raise ValueError('Unsupported batch document format/version')
        if document['type'] not in ('plan', 'receipt') or batches.revision(document['payload']) != document['payload_revision']:
            raise ValueError('Batch document integrity check failed')
        validate_payload(document['type'], document['payload'])
        if op == 'inspect':
            return {'file_path': str(path), 'file_revision': current, 'document': document,
                    'applied': False, 'receipt_import_supported': False}
        if document['type'] != 'plan':
            raise ValueError('Receipt archives are evidence; they cannot be applied as plans')
        payload = document['payload']
        if payload['tree'] != batches.context(durable=True):
            raise ValueError('Saved plan belongs to another tree/location or Gramps release')
        arguments = payload['arguments']
        method = {'update': batches.batch, 'attachments': batches.attachments}.get(payload['batch_type'])
        if method is None:
            raise ValueError('Unknown saved plan batch type')
        preview = method({**arguments, 'apply': False})
        if preview != payload['preview']:
            raise ValueError('Saved plan is stale or its instructions changed; save a fresh preview')
        result = {'file_path': str(path), 'file_revision': current, 'preview': preview, 'applied': False,
                  'plan_revision': preview['plan_revision']}
        if not a.get('apply', False):
            return result
        if a.get('expected_file_revision') != current or a.get('expected_plan') != preview['plan_revision']:
            raise ValueError('Reviewed file or plan revision missing/changed')
        if file_revision(path) != current:
            raise ValueError('Saved plan file changed during preparation')
        return {**result, 'applied': True, 'receipt': method({**arguments, 'apply': True,
                'expected_plan': preview['plan_revision']})}
    if op == 'save_plan':
        batch_type = a.get('batch_type', 'update')
        method = {'update': batches.batch, 'attachments': batches.attachments}.get(batch_type)
        if method is None:
            raise ValueError('Plan batch type must be update or attachments')
        tree = batches.context(durable=True)
        arguments = {'changes': a.get('changes', [])}
        if 'label' in a:
            arguments['label'] = a['label']
        preview = method({**arguments, 'apply': False})
        document = envelope(batches, 'plan', {'batch_type': batch_type, 'arguments': arguments,
                                            'tree': tree, 'preview': preview})
    elif op == 'export_receipt':
        document = envelope(batches, 'receipt', batches.receipt(a.get('receipt_id')))
    else:
        raise ValueError('Unknown batch file operation')
    if path.exists() and not a.get('overwrite', False):
        raise ValueError('Output exists; choose another file or explicitly request overwrite')
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(raw) > MAX_FILE:
        raise ValueError('Batch archive exceeds 16 MiB; choose a smaller batch')
    plan = batches.revision({'operation': op, 'path': str(path), 'old': current, 'document': document})
    result = {'file_path': str(path), 'file_revision': current, 'document': document,
              'plan_revision': plan, 'applied': False, 'tree_records_changed': False}
    if not a.get('apply', False):
        return result
    if a.get('expected_file_revision') != current or a.get('expected_plan') != plan:
        raise ValueError('File export preview changed or missing; preview again')
    fd, name = tempfile.mkstemp(prefix='.' + path.stem + '.', suffix='.json', dir=path.parent)
    staging = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if file_revision(path) != current:
            raise ValueError('Destination changed during preparation')
        if path.exists():
            os.replace(staging, path)
        else:
            os.link(staging, path)
    finally:
        if staging.exists():
            staging.unlink()
    return {**result, 'applied': True, 'file_revision': file_revision(path), 'file_size': len(raw)}
