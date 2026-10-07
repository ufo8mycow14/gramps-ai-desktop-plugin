"""Report discovery and native generation with explicit validated destinations."""
import hashlib
import math
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


def configure_document(support, clr, document, doc_options):
    from gramps.gen.plug.report._paper import paper_sizes
    allowed = {'paper', 'orientation', 'margins_cm', 'style', 'css_path'}
    if not isinstance(document, dict) or set(document) - allowed:
        raise ValueError('Unknown document settings; use observed paper/orientation/margins/style/CSS fields')
    papers = [paper for paper in paper_sizes if paper.get_width() > 0 and paper.get_height() > 0]
    if 'paper' in document:
        matching = [paper for paper in papers if paper.get_name() == document['paper']]
        if not matching:
            raise ValueError('Select an observed paper with positive dimensions')
        clr.paper = matching[0]
        clr.options_dict['papers'] = clr.paper.get_name()
        clr.option_class.handler.set_paper(clr.paper)
    if 'orientation' in document:
        if document['orientation'] not in ('portrait', 'landscape'):
            raise ValueError('Orientation must be portrait or landscape')
        clr.orien = int(document['orientation'] == 'landscape')
        clr.options_dict['papero'] = clr.orien
    margins = document.get('margins_cm', {})
    if not isinstance(margins, dict) or set(margins) - {'left', 'right', 'top', 'bottom'}:
        raise ValueError('Margins use left/right/top/bottom centimetres')
    for name, suffix in (('left', 'l'), ('right', 'r'), ('top', 't'), ('bottom', 'b')):
        if name in margins:
            value = margins[name]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Margins must be finite non-negative numbers')
            setattr(clr, 'margin' + suffix, value)
            clr.options_dict['paperm' + suffix] = value
    width, height = clr.paper.get_width(), clr.paper.get_height()
    if clr.orien:
        width, height = height, width
    if width <= clr.marginl + clr.marginr or height <= clr.margint + clr.marginb:
        raise ValueError('Paper margins leave no usable page area')
    clr.option_class.handler.set_orientation(clr.orien)
    clr.option_class.handler.set_margins([clr.marginl, clr.marginr, clr.margint, clr.marginb])
    styles = clr.style_list.get_style_names() if clr.style_list else []
    if 'style' in document:
        if document['style'] not in styles:
            raise ValueError('Select an observed native stylesheet')
        clr.option_class.handler.set_default_stylesheet_name(document['style'])
        clr.selected_style = clr.style_list.get_style_sheet(document['style'])
        clr.options_dict['style'] = document['style']
    if 'css_path' in document:
        path = Path(document['css_path'])
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() != '.css':
            raise ValueError('CSS must be an explicit existing absolute .css path')
        if clr.options_dict['off'] != 'html':
            raise ValueError('CSS is available only for HTML')
        clr.css_filename = str(path)
        clr.options_dict['css'] = str(path)
    menu = clr.doc_options.menu if clr.doc_options else None
    names = menu.get_all_option_names() if menu else []
    if not isinstance(doc_options, dict) or set(doc_options) - set(names):
        raise ValueError('Use observed options for the selected document generator')
    for name in names:
        option = menu.get_option_by_name(name)
        if name in doc_options:
            validate_option(support, option, doc_options[name])
            option.set_value(doc_options[name])
            clr.doc_options.options_dict[name] = doc_options[name]
            clr.options_dict[name] = doc_options[name]
    return {'papers': [{'name': p.get_name(), 'width_cm': p.get_width(), 'height_cm': p.get_height()} for p in papers],
            'orientations': ['portrait', 'landscape'], 'styles': styles,
            'document_options': {name: describe_option(menu.get_option_by_name(name)) for name in names}}


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
    if not isinstance(supplied, dict):
        raise ValueError('Report options must be an object')
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
    if op not in ('run', 'options'):
        raise ValueError('Unknown report operation')
    format_name = a.get('format') or (available_formats[0]['format'] if op == 'options' and available_formats else None)
    if format_name not in [item['format'] for item in available_formats]:
        raise ValueError('Select an observed installed output format')
    bundle = a.get('bundle', False)
    if op == 'run' and not a.get('output_path'):
        raise ValueError('Report generation requires an explicit output path')
    output = Path(a.get('output_path', str(Path(tempfile.gettempdir()) / ('gramps-options.' + format_name))))
    if op == 'run' and (not output.is_absolute() or output.is_dir() or
            (not bundle and not output.parent.is_dir()) or
            (bundle and (output.parent.exists() or not output.parent.parent.is_dir()))):
        raise ValueError('Use an existing output directory, or bundle=true with a new bundle directory under an existing parent')
    if op == 'run' and output.suffix.lower() != '.' + format_name.lower():
        raise ValueError('Output extension must match the selected format')
    if op == 'run' and output.exists() and not a.get('overwrite', False):
        raise ValueError('Output exists; choose another path or explicitly request overwrite')
    if op == 'run':
        from importlib.util import spec_from_file_location, module_from_spec
        protection_spec = spec_from_file_location('gramps_output_protection', Path(__file__).with_name('native_exports.py'))
        protection = module_from_spec(protection_spec)
        protection_spec.loader.exec_module(protection)
        protection.protect_destination(support.db, output)
    requires_bundle = format_name in ('html', 'svg', 'tex', 'graph')
    if op == 'run' and requires_bundle and not bundle:
        raise ValueError('HTML/SVG/LaTeX/tree sources need companions; use bundle=true and a new bundle directory')
    generation_supported = not (pdata.category == CATEGORY_TREE and format_name == 'pdf')
    if op == 'run' and not generation_supported:
        raise ValueError('Tree PDF requires a confined external compiler; export graph/tex or use its native dialog')
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
    document_description = configure_document(support, clr, a.get('document', {}), a.get('document_options', {}))
    for name, value in list(values.items()):
        actual = clr.option_class.menu.get_option_by_name(name).get_value()
        if actual != value:
            if name in supplied:
                raise ValueError('Native report option changed during preparation: ' + name)
            values[name] = actual  # Bind native canonical/default resolution too.
        description['options'][name] = describe_option(clr.option_class.menu.get_option_by_name(name))
    if op == 'options':
        return {**description, **document_description, 'format': format_name,
                'document_settings': {name: value for name, value in clr.options_dict.items() if name != 'of'},
                'bundle_required': requires_bundle,
                'generation_supported': generation_supported}
    settings = {name: value for name, value in clr.options_dict.items() if name != 'of'}
    settings['effective_paper'] = {'name': clr.paper.get_name(), 'width': clr.paper.get_width(),
                                   'height': clr.paper.get_height()}
    settings['effective_style'] = clr.option_class.handler.get_default_stylesheet_name()
    if pdata.category == CATEGORY_TREE and values.get('nodecolor') == 'preferences':
        from gramps.gen.config import config
        settings['tree_colours'] = {name: config.get(name) for name in
                                   ('colors.scheme', 'colors.male-dead', 'colors.female-dead', 'colors.unknown-dead')}
    input_paths = [clr.option_class.handler.filename, clr.option_class.handler.get_stylesheet_savefile(),
                   clr.css_filename, PAPERSIZE, CUSTOM_FILTERS]
    if clr.css_filename:
        input_paths.append(Path(DATA_DIR) / clr.css_filename)
    if clr.doc_options:
        input_paths.append(clr.doc_options.handler.filename)
    inputs = file_states(input_paths)
    from importlib.util import spec_from_file_location, module_from_spec
    media_spec = spec_from_file_location('gramps_report_media', Path(__file__).with_name('report_media.py'))
    media_module = module_from_spec(media_spec)
    media_spec.loader.exec_module(media_module)
    media_inputs = media_module.inputs(support.db)
    state = [{'kind': kind, 'handle': handle, 'revision': support.snapshot(kind, support.get(kind, handle=handle))['revision']}
             for kind in ('person', 'family', 'event', 'place', 'source', 'citation', 'repository', 'media', 'note', 'tag')
             for handle in getattr(support.db, 'get_%s_handles' % kind)()]
    existing = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
    plan = workflow.revision({'report': pdata.id, 'format': format_name, 'output': str(output),
                              'options': values, 'document_settings': settings, 'input_files': inputs, 'bundle': bundle,
                              'records': state, 'existing_output': existing, 'source_images': media_inputs})
    result = {**description, 'output_path': str(output), 'format': format_name,
              'applied': False, 'plan_revision': plan, 'record_count': len(state),
              'document_settings': settings, 'input_files': inputs, 'bundle': bundle, 'source_images': media_inputs, **document_description}
    if not a.get('apply', False):
        return result
    if a.get('expected_plan') != plan:
        raise ValueError('Report preview changed or missing; preview again')
    stage_folder = tempfile.TemporaryDirectory(prefix='.' + output.stem + '.', dir=output.parent.parent if bundle else output.parent)
    staging = Path(stage_folder.name) / output.name
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
        if format_name == 'tex' or pdata.category == CATEGORY_TREE:
            media_module.configure(doc, Path(stage_folder.name), media_inputs, pdata.category == CATEGORY_TREE)
        if clr.css_filename is not None and hasattr(doc, 'set_css_filename'):
            doc.set_css_filename(clr.css_filename)
        report = getattr(module, pdata.reportclass)(support.db, clr.option_class, User())
        report.doc.init()
        report.begin_report()
        report.write_report()
        report.end_report()
        if not staging.is_file() or not staging.stat().st_size:
            raise RuntimeError('Report returned without a nonempty staged file')
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('gramps_report_output', Path(__file__).with_name('report_output.py'))
        generated = module_from_spec(spec)
        spec.loader.exec_module(generated)
        artifacts = generated.manifest(Path(stage_folder.name))
        if not bundle and len(artifacts) != 1:
            raise ValueError('This format produced companions; preview a new output bundle')
        if file_states(input_paths) != inputs:
            raise ValueError('Report settings changed during generation; destination preserved')
        if media_inputs and media_module.inputs(support.db) != media_inputs:
            raise ValueError('Source images changed during generation; destination preserved')
        current_output = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
        if current_output != existing:
            raise ValueError('Output changed during report generation; original destination preserved')
        if bundle:
            generated.publish_bundle(Path(stage_folder.name), output.parent, output.name)
        elif existing is None:
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
        stage_folder.cleanup()
    return {**result, 'applied': True, 'file_size': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'artifacts': artifacts}
