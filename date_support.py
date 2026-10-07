"""Detached native date operations that preserve precision and UI preferences."""
import copy
from gramps.gen.lib import Date
from gramps.gen.lib.json_utils import object_to_dict
from gramps.gen.datehandler import parser, displayer


def read(a, key='text'):
    text = a.get(key)
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('Supply a date string of up to 2,000 characters')
    return parser.parse(text)


def describe(date, display=displayer):
    return {'data': object_to_dict(date), 'display': display.display(date), 'valid': date.is_valid(),
            'empty': date.is_empty(), 'text_only': date.get_modifier() == Date.MOD_TEXTONLY,
            'precision': {'start': {'year': bool(date.get_year()), 'month': bool(date.get_month()),
                                    'day': bool(date.get_day())},
                          'stop': {'year': bool(date.get_stop_year()), 'month': bool(date.get_stop_month()),
                                   'day': bool(date.get_stop_day())} if date.is_compound() else None},
            'calendar': date.get_calendar(), 'modifier': date.get_modifier(), 'quality': date.get_quality()}


def dispatch(a):
    operation = a.get('operation', 'parse')
    if operation == 'options':
        return {'calendars': [{'code': c, 'name': Date.calendar_names[c]} for c in Date.CALENDARS],
                'formats': [{'index': i, 'label': label} for i, label in enumerate(displayer.formats)],
                'comparisons': ['identity', '=', '<', '>', '<=', '>=', '<<', '>>'],
                'offset_units': ['days'], 'records_changed': False}
    date = read(a)
    if operation == 'parse':
        # The legacy text-only call retains its exact response contract.
        return {'data': object_to_dict(date), 'display': displayer.display(date), 'valid': date.is_valid()}
    original = describe(date)
    if operation == 'format':
        index = a.get('format_index', displayer.format)
        if type(index) is not int or not 0 <= index < len(displayer.formats):
            raise ValueError('Select an observed native format index')
        local = type(displayer)(index, blocale=displayer._locale)
        return {**describe(date, local), 'format_index': index, 'records_changed': False}
    if operation == 'compare':
        other = read(a, 'other_text')
        comparison = a.get('comparison', '=')
        if comparison == 'identity':
            matched = date.serialize() == other.serialize()
        elif comparison in ('=', '<', '>', '<=', '>=', '<<', '>>'):
            if date.is_empty() or other.is_empty() or not date.is_valid() or not other.is_valid():
                raise ValueError('Native interval matching requires two valid non-empty dates')
            matched = date.match(other, comparison)
        else:
            raise ValueError('Select an observed comparison; identity compares the complete native representation')
        from gramps.gen.config import config
        return {'original': original, 'other': describe(other), 'comparison': comparison, 'matched': bool(matched),
                'matching_convention': 'Native finite date intervals; identity uses complete serialisation',
                'date_range_preferences': {key: config.get('behavior.' + key) for key in
                                          ('date-before-range', 'date-after-range', 'date-about-range')},
                'records_changed': False}
    if operation not in ('calendar', 'offset'):
        raise ValueError('Select parse, format, compare, calendar, offset or options')
    if not date.is_regular() or date.get_slash() or date.get_new_year() != Date.NEWYEAR_JAN1:
        raise ValueError('Conversion/offset requires an exact complete date, January-1 new year and no slash year')
    result = copy.deepcopy(date)
    if operation == 'calendar':
        calendar = a.get('calendar')
        if isinstance(calendar, str):
            names = [name.casefold() for name in Date.calendar_names]
            if calendar.casefold() not in names:
                raise ValueError('Select an observed calendar name/code')
            calendar = names.index(calendar.casefold())
        if type(calendar) is not int or calendar not in Date.CALENDARS:
            raise ValueError('Select an observed calendar name/code')
        result.convert_calendar(calendar)
    else:
        days = a.get('days')
        if type(days) is not int or not -365000 <= days <= 365000:
            raise ValueError('days must be a signed integer within 365,000 days')
        result = date.offset_date(days)
        result.convert_calendar(date.get_calendar())
        result.set_text_value(date.get_text())
    return {'original': original, 'result': describe(result), 'records_changed': False,
            'precision_preserved': True, 'operation': operation}
