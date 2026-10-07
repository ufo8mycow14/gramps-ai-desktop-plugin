"""Revision-bound edits to a precisely selected embedded native record."""
import copy
from gramps.gen import lib
from gramps.gen.lib.json_utils import object_to_dict


def prepare(value, original, decode):
    if isinstance(value, dict):
        prior = original if isinstance(original, dict) else {}
        for key, item in value.items():
            if key in ('ref', 'handle', 'gramps_id', 'change', 'sortval', '_class') and key in prior and item != prior[key]:
                raise ValueError('Embedded identity/derived fields cannot change: ' + key)
            if key == 'ref' and key not in prior:
                raise ValueError('New references use native attachment/relationship tools')
            if key in ('note_list', 'citation_list') and item != prior.get(key, []):
                raise ValueError('Embedded note/citation lists use revision-checked attach operations')
            value[key] = prepare(item, prior.get(key), decode)
        if value.get('_class') == 'Date':
            expected = 8 if value['modifier'] in (lib.Date.MOD_RANGE, lib.Date.MOD_SPAN) else 4
            components = value['dateval']
            if (not isinstance(components, (list, tuple)) or len(components) != expected or
                    any(type(item) is not int for index, item in enumerate(components) if index % 4 != 3) or
                    any(type(components[index]) not in (bool, int) or components[index] not in (False, True) for index in range(3, expected, 4))):
                raise ValueError('Native dates require exactly four/eight typed components')
            date = decode(value)
            date.set(quality=date.get_quality(), modifier=date.get_modifier(), calendar=date.get_calendar(),
                     value=tuple(value['dateval']), text=date.get_text(), newyear=date.get_new_year())
            return object_to_dict(date)
    elif isinstance(value, list):
        prior = original if isinstance(original, list) else []
        value = [prepare(item, prior[index] if index < len(prior) else None, decode) for index, item in enumerate(value)]
    return value


def validate(value, schema, prior=None):
    """Enforce native embedded class/type contracts before generic decoding."""
    if value is None and prior is None:
        return  # Native optional/default fields may be null despite older schemas.
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    allowed = {'object': isinstance(value, dict), 'array': isinstance(value, (list, tuple)),
               'string': isinstance(value, str), 'integer': type(value) is int,
               'number': type(value) in (int, float), 'boolean': type(value) is bool, 'null': value is None}
    if types and not any(allowed.get(name, False) for name in types):
        raise ValueError('Embedded field does not match its native schema type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Embedded field/class does not match its native schema choices')
    if isinstance(value, dict):
        for key, field in schema.get('properties', {}).items():
            if key in value:
                validate(value[key], field, prior.get(key) if isinstance(prior, dict) else None)
    elif isinstance(value, (list, tuple)):
        items = schema.get('items', {})
        for index, item in enumerate(value):
            field = items[index] if isinstance(items, list) and index < len(items) else items if isinstance(items, dict) else {}
            validate(item, field, prior[index] if isinstance(prior, (list, tuple)) and index < len(prior) else None)


def schema(support, a):
    kind = a.get('kind')
    name = a.get('class_name')
    if name is None:
        name = support.kind(kind).capitalize()
    cls = getattr(lib, name, None)
    if not isinstance(cls, type) or not hasattr(cls, 'get_object_state') or not hasattr(cls, 'get_schema'):
        raise ValueError('Use an observed native record class')
    obj = cls()
    from gramps.gen.lib.grampstype import GrampsType
    choices = {}
    def visit(data):
        if isinstance(data, dict):
            native = getattr(lib, data.get('_class', ''), None)
            if isinstance(native, type) and issubclass(native, GrampsType):
                value = native()
                choices[native.__name__] = {'custom_code': value.get_custom(), 'standard': [
                    {'code': code, 'label': label, 'xml': native(code).xml_str()}
                    for code, label in sorted(value.get_map().items()) if code != value.get_custom()]}
            for item in data.values():
                visit(item)
        elif isinstance(data, list):
            for item in data:
                visit(item)
    template = object_to_dict(obj)
    visit(template)
    # Empty default arrays still contain typed schemas for secondary objects.
    def visit_schema(data):
        if isinstance(data, dict):
            names = data.get('properties', {}).get('_class', {}).get('enum', [])
            for nested in names:
                value = getattr(lib, nested, None)
                if isinstance(value, type) and hasattr(value, 'get_object_state'):
                    visit(object_to_dict(value()))
            for item in data.values():
                visit_schema(item)
        elif isinstance(data, list):
            for item in data:
                visit_schema(item)
    native_schema = cls.get_schema()
    visit_schema(native_schema)
    custom = {}
    for group in ('child_reference', 'event', 'event_attribute', 'family_attribute', 'media_attribute',
                  'person_attribute', 'source_attribute', 'family_event', 'family_relation', 'name', 'note',
                  'origin', 'place', 'repository', 'source_media', 'url', 'person_event'):
        getter = getattr(support.db, 'get_' + group + '_types', None)
        if getter:
            custom[group] = sorted(getter())
    custom['event_role'] = sorted(support.db.get_event_roles())
    return {'kind': kind, 'class_name': name, 'template': template, 'json_schema': native_schema,
            'type_choices': choices, 'custom_types': custom,
            'immutable_on_update': ['handle', 'gramps_id', '_class', 'change'],
            'methods': [method for method in dir(obj) if method.startswith(('get_', 'set_', 'add_', 'remove_'))]}


def dispatch(support, a, decode, merge_patch, revision):
    kind = support.kind(a['kind'])
    owner = support.get(kind, a.get('handle'), a.get('gramps_id'))
    snapshot = support.snapshot(kind, owner)
    data = copy.deepcopy(snapshot['data'])
    operation = a.get('operation', 'list')
    if operation == 'list':
        items = []
        def visit(value, path):
            if isinstance(value, dict):
                if path and '_class' in value:
                    items.append({'path': path, 'class_name': value['_class'], 'revision': revision(value), 'data': value})
                for key, child in value.items():
                    visit(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, path + [index])
        visit(data, [])
        return {'owner_revision': snapshot['revision'], 'items': items, 'records_changed': False}
    path = a.get('path')
    if not isinstance(path, list) or not 1 <= len(path) <= 10:
        raise ValueError('Supply an observed path of 1–10 field names/list indexes')
    selected = data
    for part in path:
        if isinstance(selected, dict) and isinstance(part, str) and part in selected:
            selected = selected[part]
        elif isinstance(selected, list) and type(part) is int and 0 <= part < len(selected):
            selected = selected[part]
        else:
            raise ValueError('Embedded record path changed or is invalid')
    if not isinstance(selected, dict) or '_class' not in selected:
        raise ValueError('Select one embedded native object')
    current = revision(selected)
    original_selected = copy.deepcopy(selected)
    if operation == 'get':
        return {'owner_revision': snapshot['revision'], 'path': path, 'revision': current,
                'data': selected, 'records_changed': False}
    support.ensure_revision(owner, a.get('expected_revision'))
    if a.get('expected_secondary_revision') != current:
        raise ValueError('Embedded record changed; inspect it again')
    if operation == 'update':
        patch = a.get('patch', {})
        if not isinstance(patch, dict) or any(key in patch for key in ('ref', 'handle', 'gramps_id', 'change', '_class', 'sortval')):
            raise ValueError('Embedded identity/target fields cannot change; use native relationship/attachment tools')
        proposed = merge_patch(selected, patch)
        proposed = prepare(proposed, selected, decode)
    elif operation == 'attach':
        target_kind = a.get('target_kind')
        if target_kind not in ('note', 'citation'):
            raise ValueError('Embedded attachments support native notes/citations')
        target = support.get(target_kind, handle=a.get('target_handle'))
        support.ensure_revision(target, a.get('target_revision'))
        if target_kind == 'citation' and not support.exists('source', target.get_reference_handle()):
            raise ValueError('Attach only citations with an existing source')
        field = target_kind + '_list'
        if field not in selected:
            raise ValueError('This embedded native object does not support that attachment')
        action = a.get('action', 'add')
        if action not in ('add', 'remove'):
            raise ValueError('Select add or remove')
        proposed = copy.deepcopy(selected)
        values = proposed[field]
        if action == 'add' and target.handle not in values:
            values.append(target.handle)
        elif action == 'remove':
            proposed[field] = [handle for handle in values if handle != target.handle]
    else:
        raise ValueError('Select list, get, update or attach')
    selected.clear()
    selected.update(proposed)
    if data == snapshot['data']:
        return {'applied': bool(a.get('apply', False)), 'records_changed': False, 'before': snapshot, 'proposed': snapshot}
    validate(proposed, getattr(lib, proposed['_class']).get_schema(), original_selected)
    candidate = decode(data)
    restored = type(owner)()
    restored.unserialize(candidate.serialize())
    if restored.serialize() != candidate.serialize():
        raise ValueError('Embedded update cannot round-trip through the native owner class')
    return support.changed([(kind, candidate)], a.get('label', 'Edit embedded Gramps record'), a.get('apply', False))
