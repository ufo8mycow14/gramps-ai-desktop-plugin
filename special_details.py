#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2026  Gramps Codex Desktop Plugin contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
"""Guarded directed references, ordinances, styled tags and name promotion."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import copy
from pathlib import Path
from typing import Any

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.gen import lib
from gramps.gen.lib.json_utils import object_to_dict

FIELDS = {
    "association": ("person", "person_ref_list", "PersonRef"),
    "enclosing_place": ("place", "placeref_list", "PlaceRef"),
    "lds_ordinance": ("person family", "lds_ord_list", "LdsOrd"),
    "styled_tag": ("note", "tags", "StyledTextTag"),
    "alternate_name": ("person", "alternate_names", "Name"),
}
STATUS = {
    0: {0, 3, 4, 5, 7, 8, 9, 11, 12, 13},
    1: {0, 3, 4, 5, 7, 8, 9, 11, 12, 13},
    2: {0, 1, 4, 5, 6, 8, 9, 11, 12, 13},
    3: {0, 2, 4, 5, 6, 8, 9, 10, 12, 13},
    4: {0, 3, 4, 5, 7, 8, 9, 11, 12, 13},
    5: {0, 3, 4, 5, 7, 8, 9, 11, 12, 13},
}


def target_keys(field: str, data: dict[str, Any]) -> list[tuple[str, str]]:
    """Resolve only the dedicated native target fields."""
    if field == "association":
        return [("person", data["ref"])]
    if field == "enclosing_place":
        return [("place", data["ref"])]
    if field == "lds_ordinance":
        return [
            (kind, data[key])
            for kind, key in (("place", "place"), ("family", "famc"))
            if data[key]
        ]
    if field == "styled_tag" and data["name"]["value"] == 8:
        value = data["value"]
        if value.startswith("gramps:"):
            parts = value.split("/")
            if (
                len(parts) != 5
                or parts[0:2] != ["gramps:", ""]
                or parts[3] != "handle"
                or not parts[4]
            ):
                raise ValueError(
                    "Internal styled links use gramps://Class/handle/HANDLE"
                )
            return [(parts[2].lower(), parts[4])]
    return []


def validate_style(data: dict[str, Any], text: str) -> None:
    """Enforce native style values and exact character-range bounds."""
    name, value, ranges = data["name"]["value"], data["value"], data["ranges"]
    if type(name) is not int or name not in range(11):
        raise ValueError("Select a native styled tag code 0–10")
    if (
        (name in (0, 1, 2, 7, 9, 10) and value is not None)
        or (name == 4 and (type(value) is not int or value <= 0))
        or (name in (3, 5, 6, 8) and not isinstance(value, str))
    ):
        raise ValueError("Styled tag value does not match its native style")
    if (
        not isinstance(ranges, list)
        or not ranges
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(index) is not int for index in pair)
            or not 0 <= pair[0] < pair[1] <= len(text)
            for pair in ranges
        )
    ):
        raise ValueError(
            "Styled ranges need exact start/end integers within the note text"
        )


def place_guards(support: Any, owner: str, target: str) -> list[dict[str, Any]]:
    """Reject enclosing-place cycles and bind every inspected graph node."""
    pending = [target]
    seen: set[str] = set()
    guards: list[dict[str, Any]] = []
    while pending:
        handle = pending.pop()
        if handle == owner:
            raise ValueError("Enclosing-place references cannot create a cycle")
        if handle in seen:
            continue
        if len(seen) >= 10000:
            raise ValueError("Enclosing-place traversal exceeds its inspection bound")
        seen.add(handle)
        obj = support.get("place", handle=handle)
        snapshot = support.snapshot("place", obj)
        guards.append({"handle": handle, "revision": snapshot["revision"]})
        pending.extend(ref.ref for ref in obj.placeref_list)
    return sorted(guards, key=lambda item: item["handle"])


def dispatch(
    support: Any, a: dict[str, Any], decode: Any, merge_patch: Any, revision: Any
) -> dict[str, Any]:
    """Read or preview/apply specialised native details and primary-name promotion."""
    kind, field = support.kind(a["kind"]), a["field"]
    if field not in FIELDS or kind not in FIELDS[field][0].split():
        raise ValueError("The specialised field is not supported by this owner kind")
    owner = support.get(kind, handle=a.get("handle"), gramps_id=a.get("gramps_id"))
    snapshot = support.snapshot(kind, owner)
    data = copy.deepcopy(snapshot["data"])
    parent = data["text"] if field == "styled_tag" else data
    values = parent[FIELDS[field][1]]
    cls = getattr(lib, FIELDS[field][2])
    template = object_to_dict(cls())
    if field == "lds_ordinance" and kind == "family":
        template["type"] = 3
    operation = a.get("operation", "list")
    if operation == "list":
        return {
            "owner_revision": snapshot["revision"],
            "collection_revision": revision(values),
            "template": template,
            "items": [
                {"index": index, "revision": revision(item), "data": item}
                for index, item in enumerate(values)
            ],
            "records_changed": False,
        }
    support.ensure_revision(owner, a.get("expected_revision"))
    index = a.get("index")
    selected = None
    if operation in ("update", "retarget", "remove", "promote"):
        if type(index) is not int or not 0 <= index < len(values):
            raise ValueError("Select an observed specialised detail index")
        selected = values[index]
        current = revision(selected)
    else:
        current = revision(values)
    if a.get("expected_detail_revision") != current:
        raise ValueError("The specialised item/collection changed")
    guards, targets = [], []
    if operation == "promote":
        if field != "alternate_name":
            raise ValueError("Promotion requires one alternate Name")
        previous = data["primary_name"]
        data["primary_name"] = values.pop(index)
        values.append(previous)
    elif operation in ("add", "update", "retarget"):
        if field == "alternate_name":
            raise ValueError(
                "Use secondary for Name metadata and details for promotion"
            )
        prior = selected or template
        proposed = merge_patch(prior, a.get("patch", {}))
        if field in ("association", "enclosing_place") and (
            not isinstance(proposed["ref"], str) or not proposed["ref"]
        ):
            raise ValueError("Supply an existing reference target handle")
        if field == "lds_ordinance":
            value_type, status = proposed["type"], proposed["status"]
            permitted = {3} if kind == "family" else {0, 1, 2, 4, 5}
            if (
                type(value_type) is not int
                or value_type not in permitted
                or type(status) is not int
                or status not in STATUS[value_type]
            ):
                raise ValueError("LDS type/status must match the native owner choices")
            if not isinstance(proposed["place"], str) or (
                proposed["famc"] is not None and not isinstance(proposed["famc"], str)
            ):
                raise ValueError(
                    "LDS place/family targets must use native handle shapes"
                )
        if field == "styled_tag":
            validate_style(proposed, parent["string"])
        for target_kind, handle in target_keys(field, proposed):
            obj = support.get(target_kind, handle=handle)
            expected = a.get("target_revisions", {}).get(target_kind + ":" + handle)
            support.ensure_revision(obj, expected)
            targets.append(support.snapshot(target_kind, obj))
        if field == "enclosing_place":
            guards = place_guards(support, owner.handle, proposed["ref"])
        from importlib.util import spec_from_file_location, module_from_spec

        spec = spec_from_file_location(
            "gramps_special_secondary", Path(__file__).with_name("secondary_support.py")
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("The native secondary support module could not load")
        secondary = module_from_spec(spec)
        spec.loader.exec_module(secondary)
        preparation_prior = copy.deepcopy(prior)
        for key in ("ref", "place", "famc", "name", "value", "ranges"):
            if key in proposed:
                preparation_prior[key] = copy.deepcopy(proposed[key])
        proposed = secondary.prepare(proposed, preparation_prior, decode)
        native_schema = copy.deepcopy(cls.get_schema())
        if field == "lds_ordinance":
            for key in ("type", "status"):
                native_schema["properties"][key] = {"type": "integer"}
        secondary.validate(proposed, native_schema, prior)
        if operation == "add":
            position = index if index is not None else len(values)
            if type(position) is not int or not 0 <= position <= len(values):
                raise ValueError("Insertion index is outside the observed collection")
            values.insert(position, proposed)
        else:
            values[index] = proposed
    elif operation == "remove":
        del values[index]
    elif operation == "reorder":
        if field == "styled_tag":
            raise ValueError(
                "Native styled tags have canonical order; explicit reordering cannot persist"
            )
        order = a.get("order")
        if (
            not isinstance(order, list)
            or any(type(item) is not int for item in order)
            or sorted(order) != list(range(len(values)))
        ):
            raise ValueError("Specify every detail index exactly once")
        values[:] = [values[item] for item in order]
    else:
        raise ValueError(
            "Select list, add, update, retarget, remove, reorder or promote"
        )
    candidate = decode(data)
    restored = type(owner)()
    restored.unserialize(candidate.serialize())
    if restored.serialize() != candidate.serialize():
        raise ValueError(
            "Specialised details cannot round-trip through the native owner"
        )
    candidate = restored
    preview = support.changed(
        [(kind, candidate)], "Edit specialised Gramps details", False
    )
    plan_data = {
        **preview,
        "field": field,
        "operation": operation,
        "targets": targets,
        "place_guards": guards,
    }
    plan = revision(plan_data)
    if not a.get("apply", False):
        return {**plan_data, "plan_revision": plan}
    if a.get("expected_plan") != plan:
        raise ValueError("Specialised detail preview missing or changed")
    return {
        **support.changed(
            [(kind, candidate)], a.get("label", "Edit specialised Gramps details"), True
        ),
        "plan_revision": plan,
        "field": field,
    }
