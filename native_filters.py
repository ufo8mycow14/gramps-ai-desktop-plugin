"""Native rule discovery, filtering and revision-checked profile filter storage."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
from xml.sax import make_parser

import gramps.gen.filters as filters
from gramps.gen.filters import GenericFilterFactory, FilterList, rules
from gramps.gen.filters.rules._matchesfilterbase import MatchesFilterBase
from gramps.gen.filters._filterparser import FilterParser
from gramps.gen.const import CUSTOM_FILTERS

NAMESPACES = {'person': 'Person', 'family': 'Family', 'event': 'Event', 'place': 'Place',
              'source': 'Source', 'citation': 'Citation', 'repository': 'Repository',
              'media': 'Media', 'note': 'Note'}


def definition(filt):
    return {'name': filt.get_name(), 'comment': filt.get_comment(),
            'logical_op': filt.get_logical_op(), 'invert': filt.get_invert(),
            'rules': [{'class': rule.__class__.__name__, 'values': list(rule.values()),
                       'use_regex': bool(rule.use_regex), 'use_case': bool(rule.use_case)}
                      for rule in filt.get_rules()]}


def store_revision(path):
    return hashlib.sha256(path.read_bytes() if path.exists() else b'absent-filter-store').hexdigest()


def load_store(path):
    store = FilterList(str(path))
    if path.exists():
        root = ET.parse(path).getroot()
        if root.tag != 'filters':
            raise ValueError('Invalid native filter store')
        class StrictParser(FilterParser):
            def endElement(self, tag):
                if tag != 'rule':
                    return super().endElement(tag)
                if self.r is None:
                    raise ValueError('Native filter rule unavailable; existing store preserved')
                count = len(self.f.get_rules())
                super().endElement(tag)
                parsed = self.f.get_rules()
                # Accept native legacy aliases/upgrades, but reject skipped rules,
                # invalid counts or silently discarded arguments.
                if len(parsed) != count + 1 or not parsed[-1].check() or list(parsed[-1].values()) != self.a:
                    raise ValueError('Native filter rule would lose arguments; existing store preserved')
        parser = make_parser()
        parser.setContentHandler(StrictParser(store))
        with path.open(encoding='utf-8') as stream:
            parser.parse(stream)
    return store


def dispatch(workflow, a):
    support = workflow.support
    kind = a.get('kind', 'person')
    if kind not in NAMESPACES:
        raise ValueError('Native filters support nine record kinds; tags use gramps_find')
    namespace = NAMESPACES[kind]
    catalogue = getattr(rules, kind).editor_rule_list
    classes = {cls.__name__: cls for cls in catalogue}
    op = a.get('operation', 'list')
    if op == 'rules':
        return {'kind': kind, 'namespace': namespace, 'rules': [
            {'class': cls.__name__, 'name': cls.name, 'category': cls.category,
             'description': cls.description,
             'parameters': [{'label': label[0] if isinstance(label, tuple) else label,
                             'type': 'string', 'custom_widget': isinstance(label, tuple)} for label in cls.labels],
             'allow_regex': bool(cls.allow_regex)} for cls in catalogue]}
    path = Path(a.get('store_path', CUSTOM_FILTERS))
    if not path.is_absolute():
        raise ValueError('Filter store must be an absolute path')
    store = load_store(path)
    current = store_revision(path)
    persisted = store.filter_namespaces.get(namespace, [])
    if op == 'list':
        return {'kind': kind, 'filters': [definition(f) for f in persisted],
                'store_revision': current, 'store_path': str(path), 'scope': 'Gramps profile/version'}
    spec = a.get('definition')
    name = a.get('name') or (spec or {}).get('name')
    matches = [f for f in persisted if f.get_name() == name]
    if len(matches) > 1:
        raise ValueError('Duplicate native filter names; resolve in Gramps first')
    if op in ('get', 'delete') or (op == 'run' and not spec):
        if not matches:
            raise ValueError('Named persisted filter not found')
        filt = matches[0]
    else:
        if not isinstance(spec, dict) or set(spec) - {'name', 'comment', 'logical_op', 'invert', 'rules'}:
            raise ValueError('Supply a native filter definition with known fields')
        if not isinstance(spec.get('name'), str) or not spec['name'].strip():
            raise ValueError('Filter name is required')
        if spec.get('logical_op', 'and') not in ('and', 'or', 'one'):
            raise ValueError('Filter logical_op must be and, or or one')
        if type(spec.get('invert', False)) is not bool:
            raise ValueError('Filter invert must be boolean')
        if not isinstance(spec.get('rules'), list) or not 1 <= len(spec['rules']) <= 100:
            raise ValueError('Supply between 1 and 100 native rules')
        filt = GenericFilterFactory(namespace)()
        filt.set_name(spec['name'])
        filt.set_comment(spec.get('comment', ''))
        filt.set_logical_op(spec.get('logical_op', 'and'))
        filt.set_invert(spec.get('invert', False))
        for rule_spec in spec['rules']:
            if set(rule_spec) - {'class', 'values', 'use_regex', 'use_case'}:
                raise ValueError('Unknown filter rule field')
            cls = classes.get(rule_spec.get('class'))
            values = rule_spec.get('values', [])
            if cls is None or len(values) != len(cls.labels) or any(not isinstance(v, str) for v in values):
                raise ValueError('Use an observed native rule class and its ordered string arguments')
            regex, case = rule_spec.get('use_regex', False), rule_spec.get('use_case', False)
            if type(regex) is not bool or type(case) is not bool or (regex and not cls.allow_regex):
                raise ValueError('Invalid native rule flags')
            if regex:
                for value in values:
                    re.compile(value)
            filt.add_rule(cls(values, use_regex=regex, use_case=case))
    if op == 'get':
        return {'definition': definition(filt), 'store_revision': current}
    if op == 'run':
        handles = a.get('handles')
        if handles is not None:
            for handle in handles:
                support.get(kind, handle=handle)
        old_store = filters.CustomFilters
        try:
            filters.set_custom_filters(store)
            found = sorted(filt.apply(support.db, id_list=handles, user=None))
        finally:
            filters.set_custom_filters(old_store)
        offset, limit = max(0, a.get('offset', 0)), max(1, min(a.get('limit', 50), 200))
        return {'definition': definition(filt), 'total': len(found),
                'records': [support.summary(kind, support.get(kind, handle=h)) for h in found[offset:offset + limit]],
                'next_offset': offset + limit if offset + limit < len(found) else None,
                'store_revision': current, 'records_changed': False}
    if op not in ('save', 'delete'):
        raise ValueError('Unknown filter operation')
    dependencies = []
    if op == 'delete':
        for ns, items in store.filter_namespaces.items():
            for item in items:
                for rule in item.get_rules():
                    if isinstance(rule, MatchesFilterBase) and rule.namespace == namespace and rule.values()[0] == name:
                        dependencies.append({'namespace': ns, 'name': item.get_name()})
        if dependencies:
            raise ValueError('Filter has dependent filters: ' + str(dependencies))
    else:
        # Check missing references and cycles across all namespaces before save.
        proposed = {ns: list(items) for ns, items in store.filter_namespaces.items()}
        proposed[namespace] = [item for item in persisted if item.get_name() != filt.get_name()] + [filt]
        graph = {}
        for ns, items in proposed.items():
            for item in items:
                graph[(ns, item.get_name())] = [(r.namespace, r.values()[0]) for r in item.get_rules()
                                               if isinstance(r, MatchesFilterBase)]
        def walk(key, active):
            if key in active:
                raise ValueError('Cyclic named filter dependency')
            if key not in graph:
                raise ValueError('Missing named filter dependency: ' + str(key))
            for other in graph[key]:
                walk(other, active | {key})
        walk((namespace, filt.get_name()), set())
    result = {'applied': False, 'operation': op, 'definition': definition(filt),
              'store_revision': current, 'store_path': str(path), 'scope': 'Gramps profile/version'}
    if not a.get('apply', False):
        return result
    if a.get('expected_revision') != current or store_revision(path) != current:
        raise ValueError('Filter store changed or revision missing; list/preview again')
    store.filter_namespaces[namespace] = [item for item in persisted if item.get_name() != filt.get_name()]
    if op == 'save':
        store.add(namespace, filt)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + '.backup-' + current[:16]) if path.exists() else None
    if backup and not backup.exists():
        with backup.open('xb') as stream:
            stream.write(path.read_bytes())
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    os.close(fd)
    try:
        root = ET.Element('filters')
        for ns, items in store.filter_namespaces.items():
            group = ET.SubElement(root, 'object', {'type': ns})
            for item in items:
                spec = definition(item)
                attrs = {'name': spec['name'], 'comment': spec['comment'], 'function': spec['logical_op']}
                if spec['invert']:
                    attrs['invert'] = '1'
                node = ET.SubElement(group, 'filter', attrs)
                for rule_spec in spec['rules']:
                    rule = ET.SubElement(node, 'rule', {'class': rule_spec['class'],
                           'use_regex': str(rule_spec['use_regex']), 'use_case': str(rule_spec['use_case'])})
                    for value in rule_spec['values']:
                        ET.SubElement(rule, 'arg', {'value': value})
        ET.ElementTree(root).write(temp, encoding='utf-8', xml_declaration=True)
        prepared = load_store(Path(temp))
        if {ns: [definition(f) for f in items] for ns, items in prepared.filter_namespaces.items() if items} != {
                ns: [definition(f) for f in items] for ns, items in store.filter_namespaces.items() if items}:
            raise ValueError('Prepared filter store differs from the accepted preview')
        if store_revision(path) != current:
            raise ValueError('Filter store changed during preparation')
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()
    if path == Path(CUSTOM_FILTERS):
        filters.reload_custom_filters()
        uistate = getattr(support.bridge, 'uistate', None)
        if uistate and hasattr(uistate, 'emit'):
            uistate.emit('filters-changed', (namespace,))
    return {**result, 'applied': True, 'store_revision': store_revision(path),
            'backup_path': str(backup) if backup else None}
