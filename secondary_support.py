"""Revision-bound edits to a precisely selected embedded native record."""

# ------------------------
# Python modules
# ------------------------
import copy
from typing import Any, Callable

# ------------------------
# Gramps modules
# ------------------------
from gramps.gen import lib
from gramps.gen.const import GRAMPS_LOCALE as glocale
from gramps.gen.errors import DateError
from gramps.gen.lib.json_utils import object_to_dict

_ = glocale.translation.gettext

# Arrays whose ordering has no reciprocal-link or primary-event index semantics.
DETAIL_CLASSES = frozenset(
    {
        "Name",
        "Surname",
        "Address",
        "Attribute",
        "SrcAttribute",
        "Url",
        "PlaceName",
        "Location",
    }
)


def collection(
    data: dict[str, Any], path: list[str | int]
) -> tuple[list[Any], type[Any]]:
    """Resolve a typed detail array using its native parent schema.

    :param data: Detached owner data.
    :param path: Observed collection path.
    :returns: The mutable array and its native item class.
    """
    if not isinstance(path, list) or not 1 <= len(path) <= 10:
        raise ValueError(_("Supply an observed collection path of 1–10 components"))
    parent: Any = data
    for part in path[:-1]:
        if isinstance(parent, dict) and isinstance(part, str) and part in parent:
            parent = parent[part]
        elif isinstance(parent, list) and type(part) is int and 0 <= part < len(parent):
            parent = parent[part]
        else:
            raise ValueError(_("The embedded collection path changed"))
    key = path[-1]
    if (
        not isinstance(parent, dict)
        or not isinstance(key, str)
        or not isinstance(parent.get(key), list)
    ):
        raise ValueError(_("Select a native detail array"))
    native = getattr(lib, parent.get("_class", ""), None)
    field = native.get_schema().get("properties", {}).get(key, {}) if native else {}
    names = (
        field.get("items", {}).get("properties", {}).get("_class", {}).get("enum", [])
    )
    if len(names) != 1 or names[0] not in DETAIL_CLASSES:
        raise ValueError(
            _(
                "Reference arrays and primary fields use native attachment or relationship operations"
            )
        )
    return parent[key], getattr(lib, names[0])


def lifecycle(
    support: Any,
    a: dict[str, Any],
    snapshot: dict[str, Any],
    data: dict[str, Any],
    owner: Any,
    decode: Callable,
    merge_patch: Callable,
    revision: Callable,
) -> dict[str, Any]:
    """Preview or transact one revision-bound detail lifecycle operation.

    :param support: The current database service.
    :param a: Operation arguments.
    :param snapshot: Current owner snapshot.
    :param data: Detached editable owner data.
    :param owner: Current native owner.
    :param decode: Native JSON decoder.
    :param merge_patch: Named-field patch helper.
    :param revision: Revision digest helper.
    :returns: Review or committed change receipt.
    """
    operation = a["operation"]
    if "apply" in a and type(a["apply"]) is not bool:
        raise ValueError(_("apply must be a boolean"))
    if operation == "collections":
        items = []

        def cb_visit(value: Any, path: list[str | int]) -> None:
            """Collect supported arrays, including empty native defaults."""
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, list):
                        try:
                            values, cls = collection(data, path + [key])
                        except ValueError:
                            pass
                        else:
                            items.append(
                                {
                                    "path": path + [key],
                                    "class_name": cls.__name__,
                                    "revision": revision(values),
                                    "count": len(values),
                                    "template": object_to_dict(cls()),
                                }
                            )
                    cb_visit(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    cb_visit(child, path + [index])

        cb_visit(data, [])
        return {
            "owner_revision": snapshot["revision"],
            "collections": items,
            "records_changed": False,
        }
    path = a.get("path")
    if not isinstance(path, list):
        raise ValueError(_("Supply an observed detail collection path"))
    remove = operation == "remove"
    if remove and (
        not isinstance(path, list) or len(path) < 2 or type(path[-1]) is not int
    ):
        raise ValueError(
            _("Removal requires the observed path to one detail array item")
        )
    values, cls = collection(data, path[:-1] if remove else path)
    support.ensure_revision(owner, a.get("expected_revision"))
    if remove:
        index = path[-1]
        if not 0 <= index < len(values):
            raise ValueError(_("The selected detail index changed"))
        selected_revision = revision(values[index])
    else:
        selected_revision = revision(values)
    if a.get("expected_secondary_revision") != selected_revision:
        raise ValueError(_("The detail or collection changed; inspect it again"))
    if operation == "add":
        index = a.get("index", len(values))
        if type(index) is not int or not 0 <= index <= len(values):
            raise ValueError(
                _("Insertion index must be within the observed collection")
            )
        template = object_to_dict(cls())
        if cls.__name__ == "Surname" and values:
            template["primary"] = False
        proposed = merge_patch(template, a.get("patch", {}))
        validate(proposed, cls.get_schema(), template)
        proposed = prepare(proposed, template, decode)
        validate(proposed, cls.get_schema(), template)
        values.insert(index, proposed)
    elif remove:
        del values[index]
        if cls.__name__ == "Surname" and not values:
            values.append(
                object_to_dict(cls())
            )  # Native lookup restores this blank placeholder.
    elif operation == "reorder":
        order = a.get("order")
        if (
            not isinstance(order, list)
            or len(order) != len(values)
            or any(type(index) is not int for index in order)
            or sorted(order) != list(range(len(values)))
        ):
            raise ValueError(
                _("Supply every current detail index exactly once in the desired order")
            )
        values[:] = [values[index] for index in order]
    else:
        raise ValueError(_("Select collections, add, remove or reorder"))
    if (
        cls.__name__ == "Surname"
        and sum(bool(item.get("primary")) for item in values) > 1
    ):
        raise ValueError(_("Only one surname may be marked primary"))
    if data == snapshot["data"]:
        return {
            "applied": bool(a.get("apply", False)),
            "records_changed": False,
            "before": snapshot,
            "proposed": snapshot,
        }
    validate(data, type(owner).get_schema(), snapshot["data"])
    candidate = decode(data)
    restored = type(owner)()
    restored.unserialize(candidate.serialize())
    if restored.serialize() != candidate.serialize():
        raise ValueError(
            _("The detail change cannot round-trip through its native owner")
        )
    return support.changed(
        [(snapshot["kind"], candidate)],
        a.get("label", _("Edit Gramps record details")),
        a.get("apply", False),
    )


def prepare(value: Any, original: Any, decode: Callable) -> Any:
    """Normalise native dates while protecting identities and attachments.

    :param value: Proposed detached value.
    :param original: Previous value or default template.
    :param decode: Native object decoder.
    :returns: Prepared value.
    """
    prior: Any
    if isinstance(value, dict):
        prior = original if isinstance(original, dict) else {}
        for key, item in value.items():
            if (
                key in ("ref", "handle", "gramps_id", "change", "sortval", "_class")
                and key in prior
                and item != prior[key]
            ):
                raise ValueError(
                    "Embedded identity/derived fields cannot change: " + key
                )
            if key == "ref" and key not in prior:
                raise ValueError(
                    "New references use native attachment/relationship tools"
                )
            if key in ("note_list", "citation_list") and item != prior.get(key, []):
                raise ValueError(
                    "Embedded note/citation lists use revision-checked attach operations"
                )
            value[key] = prepare(item, prior.get(key), decode)
        if value.get("_class") == "Date":
            if not {
                "modifier",
                "dateval",
                "quality",
                "calendar",
                "newyear",
                "text",
            } <= set(value):
                raise ValueError(_("Supply all native date fields from its template"))
            expected = (
                8 if value["modifier"] in (lib.Date.MOD_RANGE, lib.Date.MOD_SPAN) else 4
            )
            components = value["dateval"]
            if (
                not isinstance(components, (list, tuple))
                or len(components) != expected
                or any(
                    type(item) is not int
                    for index, item in enumerate(components)
                    if index % 4 != 3
                )
                or any(
                    type(components[index]) not in (bool, int)
                    or components[index] not in (False, True)
                    for index in range(3, expected, 4)
                )
            ):
                raise ValueError(
                    "Native dates require exactly four/eight typed components"
                )
            date = decode(value)
            if date.is_empty():
                # Validate metadata using an actual day; native set() cannot
                # round-trip its all-zero empty tuple. Preserve that tuple.
                probe = copy.deepcopy(date)
                try:
                    probe_components: tuple[int | bool, ...] = (1, 1, 2000, False)
                    if date.get_modifier() in (lib.Date.MOD_RANGE, lib.Date.MOD_SPAN):
                        probe_components += (2, 1, 2000, False)
                    probe.set(
                        quality=date.get_quality(),
                        modifier=date.get_modifier(),
                        calendar=date.get_calendar(),
                        value=probe_components,
                        text=date.get_text(),
                        newyear=date.get_new_year(),
                    )
                except DateError as error:
                    raise ValueError(_("The empty date metadata is invalid")) from error
                date.sortval = 0
                return object_to_dict(date)
            try:
                date.set(
                    quality=date.get_quality(),
                    modifier=date.get_modifier(),
                    calendar=date.get_calendar(),
                    value=tuple(value["dateval"]),
                    text=date.get_text(),
                    newyear=date.get_new_year(),
                )
            except DateError as error:
                raise ValueError(_("The embedded date is invalid")) from error
            return object_to_dict(date)
    elif isinstance(value, list):
        prior = original if isinstance(original, list) else []
        value = [
            prepare(item, prior[index] if index < len(prior) else None, decode)
            for index, item in enumerate(value)
        ]
    return value


def validate(value: Any, schema: dict[str, Any], prior: Any = None) -> None:
    """Enforce native embedded class/type contracts before generic decoding."""
    if value is None and prior is None:
        return  # Native optional/default fields may be null despite older schemas.
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    allowed = {
        "object": isinstance(value, dict),
        "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if types and not any(allowed.get(name, False) for name in types):
        raise ValueError("Embedded field does not match its native schema type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(
            "Embedded field/class does not match its native schema choices"
        )
    if isinstance(value, dict):
        for key, field in schema.get("properties", {}).items():
            if key in value:
                validate(
                    value[key],
                    field,
                    prior.get(key) if isinstance(prior, dict) else None,
                )
    elif isinstance(value, (list, tuple)):
        items = schema.get("items", {})
        for index, item in enumerate(value):
            field = (
                items[index]
                if isinstance(items, list) and index < len(items)
                else items if isinstance(items, dict) else {}
            )
            validate(
                item,
                field,
                (
                    prior[index]
                    if isinstance(prior, (list, tuple)) and index < len(prior)
                    else None
                ),
            )


def schema(support: Any, a: dict[str, Any]) -> dict[str, Any]:
    """Describe observed native schemas and type choices.

    :param support: Database service.
    :param a: Kind or native class selector.
    :returns: Native schema and templates.
    """
    kind = a.get("kind")
    name = a.get("class_name")
    if name is None:
        name = support.kind(kind).capitalize()
    cls = getattr(lib, name, None)
    if (
        not isinstance(cls, type)
        or not hasattr(cls, "get_object_state")
        or not hasattr(cls, "get_schema")
    ):
        raise ValueError("Use an observed native record class")
    obj = cls()
    from gramps.gen.lib.grampstype import GrampsType

    choices = {}

    def cb_visit(data: Any) -> None:
        """Collect native type choices from a template."""
        if isinstance(data, dict):
            native = getattr(lib, data.get("_class", ""), None)
            if isinstance(native, type) and issubclass(native, GrampsType):
                value = native()
                choices[native.__name__] = {
                    "custom_code": value.get_custom(),
                    "standard": [
                        {"code": code, "label": label, "xml": native(code).xml_str()}
                        for code, label in sorted(value.get_map().items())
                        if code != value.get_custom()
                    ],
                }
            for item in data.values():
                cb_visit(item)
        elif isinstance(data, list):
            for item in data:
                cb_visit(item)

    template = object_to_dict(obj)
    cb_visit(template)

    # Empty default arrays still contain typed schemas for secondary objects.
    def cb_visit_schema(data: Any) -> None:
        """Collect nested native classes from an observed schema."""
        if isinstance(data, dict):
            names = data.get("properties", {}).get("_class", {}).get("enum", [])
            for nested in names:
                value = getattr(lib, nested, None)
                if isinstance(value, type) and hasattr(value, "get_object_state"):
                    cb_visit(object_to_dict(value()))
            for item in data.values():
                cb_visit_schema(item)
        elif isinstance(data, list):
            for item in data:
                cb_visit_schema(item)

    native_schema = cls.get_schema()
    cb_visit_schema(native_schema)
    custom = {}
    for group in (
        "child_reference",
        "event",
        "event_attribute",
        "family_attribute",
        "media_attribute",
        "person_attribute",
        "source_attribute",
        "family_event",
        "family_relation",
        "name",
        "note",
        "origin",
        "place",
        "repository",
        "source_media",
        "url",
        "person_event",
    ):
        getter = getattr(support.db, "get_" + group + "_types", None)
        if getter:
            custom[group] = sorted(getter())
    custom["event_role"] = sorted(support.db.get_event_roles())
    return {
        "kind": kind,
        "class_name": name,
        "template": template,
        "json_schema": native_schema,
        "type_choices": choices,
        "custom_types": custom,
        "immutable_on_update": ["handle", "gramps_id", "_class", "change"],
        "methods": [
            method
            for method in dir(obj)
            if method.startswith(("get_", "set_", "add_", "remove_"))
        ],
    }


def dispatch(
    support: Any,
    a: dict[str, Any],
    decode: Callable,
    merge_patch: Callable,
    revision: Callable,
) -> dict[str, Any]:
    """Read or edit one revision-bound embedded object or detail collection.

    :param support: Database service.
    :param a: Operation arguments.
    :param decode: Native decoder.
    :param merge_patch: Named-field patch helper.
    :param revision: Revision digest helper.
    :returns: Native object or review/change receipt.
    """
    kind = support.kind(a["kind"])
    owner = support.get(kind, a.get("handle"), a.get("gramps_id"))
    snapshot = support.snapshot(kind, owner)
    data = copy.deepcopy(snapshot["data"])
    operation = a.get("operation", "list")
    if operation in ("collections", "add", "remove", "reorder"):
        return lifecycle(
            support, a, snapshot, data, owner, decode, merge_patch, revision
        )
    if operation == "list":
        items = []

        def cb_visit(value: Any, path: list[str | int]) -> None:
            """Collect precisely addressable embedded objects."""
            if isinstance(value, dict):
                if path and "_class" in value:
                    items.append(
                        {
                            "path": path,
                            "class_name": value["_class"],
                            "revision": revision(value),
                            "data": value,
                        }
                    )
                for key, child in value.items():
                    cb_visit(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    cb_visit(child, path + [index])

        cb_visit(data, [])
        return {
            "owner_revision": snapshot["revision"],
            "items": items,
            "records_changed": False,
        }
    path = a.get("path")
    if not isinstance(path, list) or not 1 <= len(path) <= 10:
        raise ValueError("Supply an observed path of 1–10 field names/list indexes")
    selected = data
    for part in path:
        if isinstance(selected, dict) and isinstance(part, str) and part in selected:
            selected = selected[part]
        elif (
            isinstance(selected, list)
            and type(part) is int
            and 0 <= part < len(selected)
        ):
            selected = selected[part]
        else:
            raise ValueError("Embedded record path changed or is invalid")
    if not isinstance(selected, dict) or "_class" not in selected:
        raise ValueError("Select one embedded native object")
    current = revision(selected)
    original_selected = copy.deepcopy(selected)
    if operation == "get":
        return {
            "owner_revision": snapshot["revision"],
            "path": path,
            "revision": current,
            "data": selected,
            "records_changed": False,
        }
    support.ensure_revision(owner, a.get("expected_revision"))
    if a.get("expected_secondary_revision") != current:
        raise ValueError("Embedded record changed; inspect it again")
    if operation == "update":
        patch = a.get("patch", {})
        if not isinstance(patch, dict) or any(
            key in patch
            for key in ("ref", "handle", "gramps_id", "change", "_class", "sortval")
        ):
            raise ValueError(
                "Embedded identity/target fields cannot change; use native relationship/attachment tools"
            )
        proposed = merge_patch(selected, patch)
        proposed = prepare(proposed, selected, decode)
    elif operation == "attach":
        target_kind = a.get("target_kind")
        if target_kind not in ("note", "citation"):
            raise ValueError("Embedded attachments support native notes/citations")
        target = support.get(target_kind, handle=a.get("target_handle"))
        support.ensure_revision(target, a.get("target_revision"))
        if target_kind == "citation" and not support.exists(
            "source", target.get_reference_handle()
        ):
            raise ValueError("Attach only citations with an existing source")
        field = target_kind + "_list"
        if field not in selected:
            raise ValueError(
                "This embedded native object does not support that attachment"
            )
        action = a.get("action", "add")
        if action not in ("add", "remove"):
            raise ValueError("Select add or remove")
        proposed = copy.deepcopy(selected)
        values = proposed[field]
        if action == "add" and target.handle not in values:
            values.append(target.handle)
        elif action == "remove":
            proposed[field] = [handle for handle in values if handle != target.handle]
    else:
        raise ValueError("Select list, get, update or attach")
    selected.clear()
    selected.update(proposed)
    if data == snapshot["data"]:
        return {
            "applied": bool(a.get("apply", False)),
            "records_changed": False,
            "before": snapshot,
            "proposed": snapshot,
        }
    validate(proposed, getattr(lib, proposed["_class"]).get_schema(), original_selected)
    candidate = decode(data)
    restored = type(owner)()
    restored.unserialize(candidate.serialize())
    if restored.serialize() != candidate.serialize():
        raise ValueError(
            "Embedded update cannot round-trip through the native owner class"
        )
    return support.changed(
        [(kind, candidate)],
        a.get("label", "Edit embedded Gramps record"),
        a.get("apply", False),
    )
