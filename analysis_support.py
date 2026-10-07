"""Bounded inspection of recorded links and candidates; never repair or infer identity."""
from collections import deque
import json
import unicodedata

KINDS = ('person', 'family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note', 'tag')

class InspectionLimit(Exception):
    pass

def bound(value, default, maximum, name):
    value = default if value is None else value
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('%s must be between 1 and %s' % (name, maximum))
    return value


class Analysis:
    def __init__(self, support):
        self.support = support
        self.cache = {}
        self.warnings = []
        self.warnings_truncated = False
        self.inspections = 0
        self.max_inspections = 10000

    def inspect(self):
        if self.inspections >= self.max_inspections:
            raise InspectionLimit()
        self.inspections += 1

    def get(self, kind, handle):
        key = (kind, handle)
        if key not in self.cache:
            self.inspect()
            self.cache[key] = (getattr(self.support.db, 'get_%s_from_handle' % kind)(handle)
                               if handle and self.support.exists(kind, handle) else None)
        return self.cache[key]

    def warn(self, code, **details):
        item = {'code': code, **details}
        if item not in self.warnings and len(self.warnings) < 200:
            self.warnings.append(item)
        elif item not in self.warnings:
            self.warnings_truncated = True

    def neighbours(self, person, direction, partners, relation_codes):
        families = person.get_parent_family_handle_list() if direction == 'ancestors' else person.get_family_handle_list()
        for family_handle in families:
            self.inspect()
            family = self.get('family', family_handle)
            if family is None:
                self.warn('missing_family', person_handle=person.handle, family_handle=family_handle)
                continue
            parents = [('father', family.get_father_handle()), ('mother', family.get_mother_handle())]
            children = family.get_child_ref_list()
            if direction == 'ancestors':
                references = []
                for i, ref in enumerate(children):
                    self.inspect()
                    if ref.ref == person.handle:
                        references.append((i, ref))
                if not references:
                    self.warn('nonreciprocal_child_link', person_handle=person.handle, family_handle=family_handle)
                    continue
                for index, ref in references:
                    for slot, handle in parents:
                        relation = getattr(ref, 'get_%s_relation' % slot)()
                        if handle and (relation_codes is None or int(relation) in relation_codes):
                            yield handle, self.edge(family, index, ref, slot, handle)
            else:
                slots = [slot for slot, handle in parents if handle == person.handle]
                if not slots:
                    self.warn('nonreciprocal_partner_link', person_handle=person.handle, family_handle=family_handle)
                    continue
                for slot in slots:
                    for index, ref in enumerate(children):
                        self.inspect()
                        relation = getattr(ref, 'get_%s_relation' % slot)()
                        if relation_codes is None or int(relation) in relation_codes:
                            yield ref.ref, self.edge(family, index, ref, slot, person.handle)
                if partners:
                    for slot, handle in parents:
                        if handle and handle != person.handle:
                            yield handle, {'type': 'partner', 'from_handle': person.handle, 'to_handle': handle,
                                           'family_handle': family.handle}

    @staticmethod
    def edge(family, index, ref, slot, parent):
        relation = getattr(ref, 'get_%s_relation' % slot)()
        code, custom = relation.serialize()
        return {'type': 'parent_child', 'parent_handle': parent, 'child_handle': ref.ref,
                'family_handle': family.handle, 'child_ref_index': index, 'parent_slot': slot,
                'relation': {'code': code, 'custom': custom, 'label': relation.xml_str()},
                'private': ref.get_privacy(), 'citation_handles': list(ref.get_citation_list()),
                'note_handles': list(ref.get_note_list())}

    def walk(self, *args, **kwargs):
        try:
            return self._walk(*args, **kwargs)
        except InspectionLimit:
            distances, paths, edges, reasons, stopped = self.walk_state
            reasons.add('max_inspections')
            return distances, paths, edges, reasons, stopped

    def _walk(self, root, direction, depth, max_nodes, max_edges, relation_codes=None, partners=False, target=None):
        distances, paths, edges, edge_keys = {root.handle: 0}, {root.handle: []}, [], set()
        todo, reasons = deque([root.handle]), set()
        stopped = root.handle == target
        self.walk_state = (distances, paths, edges, reasons, stopped)
        while todo and not stopped:
            handle = todo.popleft()
            person = self.get('person', handle)
            directions = ('ancestors', 'descendants') if direction == 'path' else (direction,)
            for side in directions:
                for other, edge in self.neighbours(person, side, partners, relation_codes):
                    if distances[handle] >= depth and other not in distances:
                        reasons.add('max_depth')
                        continue
                    if other not in distances and len(distances) >= max_nodes:
                        reasons.add('max_nodes')
                        continue
                    linked = self.get('person', other)
                    if linked is None:
                        self.warn('missing_person', person_handle=other, family_handle=edge['family_handle'])
                        continue
                    if edge['type'] == 'parent_child':
                        if side == 'ancestors' and edge['family_handle'] not in linked.get_family_handle_list():
                            self.warn('nonreciprocal_parent_link', person_handle=other, family_handle=edge['family_handle'])
                        if side == 'descendants' and edge['family_handle'] not in linked.get_parent_family_handle_list():
                            self.warn('nonreciprocal_child_link', person_handle=other, family_handle=edge['family_handle'])
                    edge = {**edge, 'from_handle': handle, 'to_handle': other}
                    key = json.dumps(edge, sort_keys=True)
                    if key not in edge_keys:
                        if len(edges) >= max_edges:
                            reasons.add('max_edges')
                            return distances, paths, edges, reasons, stopped
                        edge_keys.add(key)
                        edges.append(edge)
                    if other not in distances:
                        distances[other] = distances[handle] + 1
                        paths[other] = paths[handle] + [edge]
                        todo.append(other)
                    if other == target:
                        stopped = True
                        break
                if stopped:
                    break
        return distances, paths, edges, reasons, stopped

    def graph(self, a):
        operation = a.get('operation', 'ancestors')
        if operation not in ('ancestors', 'descendants', 'common_ancestors', 'path'):
            raise ValueError('Select ancestors, descendants, common_ancestors or path')
        root = self.support.get('person', a.get('handle'), a.get('gramps_id'))
        self.cache[('person', root.handle)] = root
        depth = bound(a.get('max_depth'), 10, 50, 'max_depth')
        max_nodes = bound(a.get('max_nodes'), 1000, 5000, 'max_nodes')
        max_edges = bound(a.get('max_edges'), 2000, 20000, 'max_edges')
        self.max_inspections = bound(a.get('max_inspections'), 10000, 50000, 'max_inspections')
        relations = a.get('relation_codes')
        if relations is not None and (not isinstance(relations, list) or any(type(n) is not int or not 0 <= n <= 7 for n in relations)):
            raise ValueError('relation_codes must contain native child relation codes 0–7')
        target = None
        if operation in ('common_ancestors', 'path'):
            target = self.support.get('person', a.get('target_handle'), a.get('target_gramps_id'))
            self.cache[('person', target.handle)] = target
        distances, paths, edges, reasons, stopped = self.walk(root, 'ancestors' if operation == 'common_ancestors' else operation,
            depth, max_nodes, max_edges, relations, a.get('include_partners', True) if operation == 'path' else False,
            target.handle if operation == 'path' else None)
        common = []
        second = {}
        if operation == 'common_ancestors':
            second, _, second_edges, other_reasons, _ = self.walk(target, 'ancestors', depth, max_nodes, max_edges, relations)
            reasons |= other_reasons
            unique = {}
            for origin, found_edges in ((root.handle, edges), (target.handle, second_edges)):
                for edge in found_edges:
                    key = json.dumps(edge, sort_keys=True)
                    if key not in unique:
                        unique[key] = {**edge, 'root_handles': []}
                    if origin not in unique[key]['root_handles']:
                        unique[key]['root_handles'].append(origin)
            edges = list(unique.values())
            if len(edges) > max_edges:
                reasons.add('max_edges')
                edges = edges[:max_edges]
            for handle in sorted(set(distances) & set(second), key=lambda h: (distances[h] + second[h], h)):
                if a.get('include_self', False) or (distances[handle] and second[handle]):
                    common.append({'handle': handle, 'root_depth': distances[handle], 'target_depth': second[handle]})
        handles = sorted(set(distances) | set(second))
        result = {'operation': operation, 'root_handle': root.handle, 'target_handle': target.handle if target else None,
                  'nodes': [{**self.support.summary('person', self.get('person', h)),
                             'revision': self.support.snapshot('person', self.get('person', h))['revision'],
                             'root_depth': distances.get(h), 'target_depth': second.get(h)} for h in handles],
                  'edges': edges, 'common_ancestors': common,
                  'limits': {'max_depth': depth, 'max_nodes_per_root': max_nodes, 'max_edges': max_edges,
                             'max_inspections': self.max_inspections}, 'inspections': self.inspections,
                  'truncated': bool(reasons), 'truncation_reasons': sorted(reasons), 'warnings': self.warnings,
                  'warnings_truncated': self.warnings_truncated,
                  'recorded_links_only': True, 'records_changed': False}
        if operation == 'path':
            result.update(found=stopped, path=paths.get(target.handle),
                          path_status='found' if stopped else ('bounded_search_incomplete' if reasons else 'no_recorded_path'))
        return result

    def audit(self, a):
        self.max_inspections = bound(a.get('max_inspections'), 10000, 50000, 'max_inspections')
        self.audit_state = {'candidate_only': True, 'records_changed': False, 'truncated': False,
                            'truncation_reasons': [], 'checked_candidates': 0, 'candidates': []}
        try:
            result = self._audit(a)
        except InspectionLimit:
            result = self.audit_state
            result['truncated'] = True
            result['truncation_reasons'] = ['max_inspections']
        result.update(inspections=self.inspections, max_inspections=self.max_inspections,
                      warnings=self.warnings, warnings_truncated=self.warnings_truncated)
        return result

    def _audit(self, a):
        if a.get('operation', 'references') == 'duplicates':
            return self.duplicates(a)
        if a.get('operation', 'references') != 'references':
            raise ValueError('Select references or duplicates')
        records = a.get('records', [])
        if not isinstance(records, list) or not 1 <= len(records) <= 200:
            raise ValueError('Supply 1–200 explicit records for scoped reference inspection')
        issues, checked, seen = [], [], set()
        result = {'checked': checked, 'issues': issues, 'issues_truncated': False,
                  'candidate_warnings_only': True, 'records_changed': False, 'repairs_run': False,
                  'truncated': False, 'truncation_reasons': []}
        self.audit_state = result
        for item in records:
            self.inspect()
            kind = self.support.kind(item['kind'])
            obj = self.support.get(kind, handle=item['handle'])
            key = (kind, obj.handle)
            if key in seen:
                raise ValueError('Inspect each record once')
            seen.add(key)
            checked.append({**self.support.summary(kind, obj), 'revision': self.support.snapshot(kind, obj)['revision']})
            def issue(code, **details):
                if len(issues) < 1000:
                    issues.append({'code': code, 'kind': kind, 'handle': obj.handle, **details})
                else:
                    result['issues_truncated'] = True
            for cls, handle in obj.get_referenced_handles_recursively():
                self.inspect()
                target_kind = cls.lower()
                if target_kind not in KINDS or self.get(target_kind, handle) is None:
                    issue('missing_target', target_kind=target_kind, target_handle=handle)
            if kind == 'person':
                for role, families in (('partner', obj.get_family_handle_list()), ('child', obj.get_parent_family_handle_list())):
                    if len(set(families)) != len(families):
                        issue('duplicate_family_handle', role=role)
                    for handle in families:
                        family = self.get('family', handle)
                        if family:
                            members = (family.get_father_handle(), family.get_mother_handle()) if role == 'partner' else [r.ref for r in family.get_child_ref_list()]
                            if obj.handle not in members:
                                issue('nonreciprocal_family_link', family_handle=handle, role=role)
            if kind == 'family':
                parents = [h for h in (obj.get_father_handle(), obj.get_mother_handle()) if h]
                children = [ref.ref for ref in obj.get_child_ref_list()]
                if len(set(children)) != len(children):
                    issue('duplicate_child_ref')
                for role, handles in (('partner', parents), ('child', children)):
                    for handle in handles:
                        person = self.get('person', handle)
                        if person:
                            families = person.get_family_handle_list() if role == 'partner' else person.get_parent_family_handle_list()
                            if obj.handle not in families:
                                issue('nonreciprocal_family_link', person_handle=handle, role=role)
                for handle in sorted(set(parents) & set(children)):
                    issue('self_parent', person_handle=handle)
                for parent in sorted(set(parents)):
                    person = self.get('person', parent)
                    if person:
                        distances, paths, _, reasons, _ = self.walk(person, 'ancestors', 50, 1000, 2000)
                        for child in sorted(set(children) & set(distances)):
                            if child != parent:
                                issue('recorded_parent_cycle', parent_handle=parent, child_handle=child,
                                      path=paths[child])
                        if reasons:
                            result['truncated'] = True
                            result['truncation_reasons'] = sorted(set(result['truncation_reasons']) | reasons)
                            issue('ancestry_check_truncated', person_handle=parent, reasons=sorted(reasons))
        return result

    @staticmethod
    def normalise(value):
        if isinstance(value, str):
            return ' '.join(unicodedata.normalize('NFC', value).casefold().split())
        if isinstance(value, (tuple, list)):
            return [Analysis.normalise(v) for v in value]
        if isinstance(value, dict):
            return {key: Analysis.normalise(v) for key, v in value.items()}
        return value

    def fields(self, kind, obj):
        data = self.support.snapshot(kind, obj)['data']
        if kind == 'note':
            return {'text': self.normalise(obj.get()), 'format': data['format']}
        if kind == 'place':
            return {'name': self.normalise(obj.get_name().get_value()), 'title': self.normalise(obj.get_title()),
                    'code': self.normalise(obj.get_code()), 'type': obj.get_type().serialize()}
        if kind == 'family':
            return {'father_handle': obj.get_father_handle(), 'mother_handle': obj.get_mother_handle(),
                    'child_handles': sorted(r.ref for r in obj.get_child_ref_list()),
                    'relationship': obj.get_relationship().serialize()}
        if kind == 'citation':
            source, page = obj.get_reference_handle(), self.normalise(obj.get_page())
            return {'source_handle': source, 'page': page,
                    'source_page': [source, page] if source and page else None,
                    'date': obj.get_date_object().serialize() if not obj.get_date_object().is_empty() else None,
                    'confidence': obj.get_confidence_level()}
        if kind != 'person':
            return {k: self.normalise(data[k]) for k in ('name', 'title', 'author', 'pubinfo', 'abbrev',
                    'page', 'description', 'path', 'mime', 'text') if k in data}
        name = obj.get_primary_name()
        fields = {'name': self.normalise([name.get_first_name(), name.get_surname()]),
                  'gender': obj.get_gender() if obj.get_gender() != 2 else None,
                  'partner_families': sorted(obj.get_family_handle_list()),
                  'parent_families': sorted(obj.get_parent_family_handle_list())}
        for role in ('birth', 'death'):
            ref = getattr(obj, 'get_%s_ref' % role)()
            event = self.get('event', ref.ref) if ref else None
            date = event.get_date_object() if event else None
            fields[role + '_date'] = date.serialize() if date and not date.is_empty() else None
            fields[role + '_place'] = event.get_place_handle() if event else None
        return fields

    def duplicates(self, a):
        kind = self.support.kind(a.get('kind', 'person'))
        seed = self.support.get(kind, a.get('handle'), a.get('gramps_id'))
        pool = a.get('candidate_handles', [])
        if not isinstance(pool, list) or not 1 <= len(pool) <= 500 or len(set(pool)) != len(pool):
            raise ValueError('Supply 1–500 distinct explicit candidate handles')
        basis = self.fields(kind, seed)
        candidates = []
        result = {'seed': self.support.summary(kind, seed), 'seed_revision': self.support.snapshot(kind, seed)['revision'],
                  'candidates': candidates, 'checked_candidates': 0,
                  'comparison_basis': 'Exact normalised named fields; native dates and recorded handles compared exactly',
                  'candidate_only': True, 'records_changed': False, 'merges_run': False,
                  'truncated': False, 'truncation_reasons': []}
        self.audit_state = result
        def missing(value):
            return value is None or value == '' or value == [] or value == {} or value == ['', '']
        for handle in pool:
            self.inspect()
            result['checked_candidates'] += 1
            if handle == seed.handle:
                continue
            obj = self.support.get(kind, handle=handle)
            values = self.fields(kind, obj)
            matched, different, absent = [], [], []
            for field in sorted(set(basis) | set(values)):
                if missing(basis.get(field)) or missing(values.get(field)):
                    absent.append(field)
                elif basis[field] == values[field]:
                    matched.append(field)
                else:
                    different.append(field)
            anchor_fields = (('father_handle', 'mother_handle', 'child_handles') if kind == 'family' else
                             ('source_page',) if kind == 'citation' else
                             ('name', 'title', 'description', 'path', 'text'))
            if not set(matched) & set(anchor_fields):
                continue
            candidates.append({'record': self.support.summary(kind, obj),
                               'revision': self.support.snapshot(kind, obj)['revision'],
                               'matched_fields': matched, 'different_fields': different, 'missing_fields': absent,
                               'candidate_only': True})
        candidates.sort(key=lambda c: (-len(c['matched_fields']), c['record']['handle']))
        return result
