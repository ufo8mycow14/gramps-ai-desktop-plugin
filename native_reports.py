"""Report discovery and native generation with explicit validated destinations."""
import hashlib
import os
from pathlib import Path
import tempfile

from gramps.gen.plug import BasePluginManager
from gramps.gen.plug.report import CATEGORY_TEXT, CATEGORY_DRAW, CATEGORY_GRAPHVIZ, CATEGORY_TREE


def formats(manager, category):
    if category in (CATEGORY_TEXT, CATEGORY_DRAW):
        return [{'format': p.get_extension(), 'description': p.get_description()} for p in manager.get_docgen_plugins()
                if p.get_extension() and (p.get_text_support() if category == CATEGORY_TEXT else p.get_draw_support())]
    from gramps.gen.plug.docgen import graphdoc, treedoc
    source = graphdoc.FORMATS if category == CATEGORY_GRAPHVIZ else treedoc.FORMATS if category == CATEGORY_TREE else []
    return [{'format': item['type'], 'description': item['descr']} for item in source]


def describe_option(option):
    value = option.get_value()
    result = {'type': type(option).__name__, 'label': option.get_label(), 'value': value,
              'help': option.get_help(), 'available': bool(option.get_available())}
    for attr in ('items', 'min', 'max', 'step'):
        method = getattr(option, 'get_' + attr, None)
        if method:
            result[attr] = method()
    return result


def validate_option(support, option, value):
    from gramps.gen.plug import menu
    if not option.get_available():
        raise ValueError('Report option is unavailable: ' + option.get_label())
    default = option.get_value()
    if type(value) is not type(default):
        raise ValueError('Report option must have its observed native value type')
    if isinstance(option, menu.NumberOption) and not option.get_min() <= value <= option.get_max():
        raise ValueError('Report number is outside its native bounds')
    if isinstance(option, menu.EnumeratedListOption) and value not in [item[0] for item in option.get_items()]:
        raise ValueError('Report choice must be an observed enumerated value')
    for cls_name, kind in (('PersonOption', 'person'), ('FamilyOption', 'family'),
                           ('NoteOption', 'note'), ('MediaOption', 'media')):
        if isinstance(option, getattr(menu, cls_name)):
            support.get(kind, gramps_id=value)
    if isinstance(option, menu.PersonListOption):
        for ident in value.split():
            support.get('person', gramps_id=ident)


def file_states(paths):
    return [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
            for path in sorted({Path(value) for value in paths if value}, key=str)]


def dispatch(workflow, a):
    support = workflow.support
    manager = BasePluginManager.get_instance()
    cli_reports = {p.id: p for p in manager.get_reg_reports(gui=False)}
    all_reports = {**cli_reports, **{p.id: p for p in manager.get_reg_reports(gui=True)}}
    op = a.get('operation', 'list')
    if op == 'list':
        return {'reports': [{'id': p.id, 'name': p.name, 'description': p.description,
                              'category': p.category, 'automatable': p.id in cli_reports and
                              p.category in (CATEGORY_TEXT, CATEGORY_DRAW, CATEGORY_GRAPHVIZ, CATEGORY_TREE)}
                             for p in all_reports.values()]}
    pdata = all_reports.get(a.get('report_id'))
    if not pdata:
        raise ValueError('Select an installed report ID from gramps_report list')
    if op == 'open':
        return support.dispatch('workflow', {'operation': 'report', 'action_name': pdata.id})
    if pdata.id not in cli_reports or pdata.category not in (CATEGORY_TEXT, CATEGORY_DRAW, CATEGORY_GRAPHVIZ, CATEGORY_TREE):
        raise ValueError('This report uses its native dialog; use operation=open')
    module = manager.load_plugin(pdata)
    if not module:
        raise ValueError('Report module could not load; check its dependencies')
    option_class = getattr(module, pdata.optionclass)
    native_options = option_class(pdata.id, support.db)
    if pdata.category in (CATEGORY_GRAPHVIZ, CATEGORY_TREE):
        from gramps.gen.plug.docgen import graphdoc, treedoc
        extra = graphdoc.GVOptions() if pdata.category == CATEGORY_GRAPHVIZ else treedoc.TreeOptions()
        extra.add_menu_options(native_options.menu)
        for name in native_options.menu.get_all_option_names():
            native_options.options_dict.setdefault(name, native_options.menu.get_option_by_name(name).get_value())
    native_options.load_previous_values()
    available_formats = formats(manager, pdata.category)
    supplied = a.get('options', {})
    # Apply controlling options in native order so dependent filter choices refresh.
    option_names = native_options.menu.get_all_option_names()
    if set(supplied) - set(option_names):
        raise ValueError('Unknown native report options: ' + str(sorted(set(supplied) - set(option_names))))
    for name in option_names:
        if name in supplied:
            option = native_options.menu.get_option_by_name(name)
            validate_option(support, option, supplied[name])
            option.set_value(supplied[name])
    description = {'report_id': pdata.id, 'formats': available_formats, 'options': {
        name: describe_option(native_options.menu.get_option_by_name(name)) for name in option_names}}
    if op == 'options':
        return description
    if op != 'run':
        raise ValueError('Unknown report operation')
    output = Path(a.get('output_path', ''))
    if not output.is_absolute() or not output.parent.is_dir() or output.is_dir():
        raise ValueError('Supply an absolute output file in an existing directory')
    format_name = a.get('format')
    if format_name not in [item['format'] for item in available_formats]:
        raise ValueError('Select an observed installed output format')
    if output.suffix.lower() != '.' + format_name.lower():
        raise ValueError('Output extension must match the selected format')
    if output.exists() and not a.get('overwrite', False):
        raise ValueError('Output exists; choose another path or explicitly request overwrite')
    # Bind all option values to the preview, including implied/default values.
    values = {name: native_options.menu.get_option_by_name(name).get_value() for name in option_names}
    for name in option_names:
        option = native_options.menu.get_option_by_name(name)
        from gramps.gen.plug.menu import PersonOption, FamilyOption
        if isinstance(option, (PersonOption, FamilyOption)):
            kind = 'person' if isinstance(option, PersonOption) else 'family'
            if values[name] and getattr(support.db, 'get_%s_from_gramps_id' % kind)(values[name]):
                continue
            if option.get_available():
                raise ValueError('Supply a valid explicit centre record ID: ' + name)
            default = support.db.get_default_person() if kind == 'person' else None
            if default is None:
                handles = getattr(support.db, 'get_%s_handles' % kind)()
                default = support.get(kind, handle=handles[0]) if handles else None
            if default is not None:
                option.set_value(default.get_gramps_id())
                values[name] = default.get_gramps_id()
    # Prepare the actual native document settings before preview; reuse this
    # exact preparation during apply rather than loading a second set of defaults.
    from gramps.cli.plug import CommandLineReport
    from gramps.cli.user import User
    from gramps.gen.plug.docgen import PaperStyle
    from gramps.gen.const import PAPERSIZE, CUSTOM_FILTERS, DATA_DIR
    options_str = {name: str(value) if not isinstance(value, list) else repr(value) for name, value in values.items()}
    options_str.update(of=str(output), off=format_name)
    clr = CommandLineReport(support.db, pdata.id, pdata.category, option_class, options_str)
    for name, value in values.items():
        if clr.option_class.menu.get_option_by_name(name).get_value() != value:
            raise ValueError('Native report option changed during preparation: ' + name)
    settings = {name: value for name, value in clr.options_dict.items() if name != 'of'}
    settings['effective_paper'] = {'name': clr.paper.get_name(), 'width': clr.paper.get_width(),
                                   'height': clr.paper.get_height()}
    settings['effective_style'] = clr.option_class.handler.get_default_stylesheet_name()
    input_paths = [clr.option_class.handler.filename, clr.option_class.handler.get_stylesheet_savefile(),
                   clr.css_filename, PAPERSIZE, CUSTOM_FILTERS]
    if clr.css_filename:
        input_paths.append(Path(DATA_DIR) / clr.css_filename)
    if clr.doc_options:
        input_paths.append(clr.doc_options.handler.filename)
    inputs = file_states(input_paths)
    state = [{'kind': kind, 'handle': handle, 'revision': support.snapshot(kind, support.get(kind, handle=handle))['revision']}
             for kind in ('person', 'family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note', 'tag')
             for handle in getattr(support.db, 'get_%s_handles' % kind)()]
    existing = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
    plan = workflow.revision({'report': pdata.id, 'format': format_name, 'output': str(output),
                              'options': values, 'document_settings': settings, 'input_files': inputs,
                              'records': state, 'existing_output': existing})
    result = {**description, 'output_path': str(output), 'format': format_name,
              'applied': False, 'plan_revision': plan, 'record_count': len(state),
              'document_settings': settings, 'input_files': inputs}
    if not a.get('apply', False):
        return result
    if a.get('expected_plan') != plan:
        raise ValueError('Report preview changed or missing; preview again')
    fd, staging_name = tempfile.mkstemp(prefix='.' + output.stem + '.', suffix=output.suffix, dir=output.parent)
    os.close(fd)
    staging = Path(staging_name)
    report = None
    try:
        clr.option_class.handler.output = str(staging)
        clr.options_dict['of'] = str(staging)
        paper = PaperStyle(clr.paper, clr.orien, clr.marginl, clr.marginr, clr.margint, clr.marginb)
        if pdata.category in (CATEGORY_GRAPHVIZ, CATEGORY_TREE):
            doc = clr.format(clr.option_class, paper)
        elif clr.doc_options:
            doc = clr.format(clr.selected_style, paper, clr.doc_options)
        else:
            doc = clr.format(clr.selected_style, paper)
        clr.option_class.handler.doc = doc
        if clr.css_filename is not None and hasattr(doc, 'set_css_filename'):
            doc.set_css_filename(clr.css_filename)
        report = getattr(module, pdata.reportclass)(support.db, clr.option_class, User())
        report.doc.init()
        report.begin_report()
        report.write_report()
        report.end_report()
        if not staging.is_file() or not staging.stat().st_size:
            raise RuntimeError('Report returned without a nonempty staged file')
        if file_states(input_paths) != inputs:
            raise ValueError('Report settings changed during generation; destination preserved')
        current_output = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
        if current_output != existing:
            raise ValueError('Output changed during report generation; original destination preserved')
        if existing is None:
            os.link(staging, output)  # Atomic create; never overwrite an intervening file.
        else:
            os.replace(staging, output)
    except Exception:
        try:
            if report is not None:
                report.doc.close()
        except Exception:
            pass
        raise
    finally:
        if staging.exists():
            staging.unlink()
    return {**result, 'applied': True, 'file_size': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
