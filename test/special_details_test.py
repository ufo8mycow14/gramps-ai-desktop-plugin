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
"""Verify specialised details on synthetic native owners."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from types import SimpleNamespace
from typing import Any
import unittest

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import support


# -------------------------------------------------------------------------
# SpecialDetails
# -------------------------------------------------------------------------
class SpecialDetails(unittest.TestCase):
    """Check native schemas, guarded targets and preserving name/format details."""

    def setUp(self) -> None:
        """Open only a disposable database."""
        self.db = SQLite()
        self.db.load(":memory:")
        state = SimpleNamespace(db=self.db)
        self.service = support.GrampsSupport(
            SimpleNamespace(dbstate=state, windows=lambda: []), state
        )

    def tearDown(self) -> None:
        """Release the disposable tree."""
        self.db.close(update=False)

    def create(self, kind: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Seed one record using native allocation."""
        return self.service.dispatch(
            "mutate",
            {"kind": kind, "operation": "create", "patch": patch or {}, "apply": True},
        )["after"][0]

    def args(
        self,
        owner: dict[str, Any],
        field: str,
        operation: str = "add",
        index: int | None = None,
    ) -> dict[str, Any]:
        """Build current owner and native collection/item guards."""
        current = self.service.dispatch(
            "object", {"kind": owner["kind"], "handle": owner["handle"]}
        )
        base = {"kind": owner["kind"], "handle": owner["handle"], "field": field}
        listing = self.service.dispatch("details", base)
        revision = (
            listing["collection_revision"]
            if operation in ("add", "reorder")
            else listing["items"][index]["revision"]
        )
        return {
            **base,
            "operation": operation,
            "expected_revision": current["revision"],
            "expected_detail_revision": revision,
            **({"index": index} if index is not None else {}),
        }

    def apply(self, args: dict[str, Any]) -> dict[str, Any]:
        """Commit only the exact current preview."""
        preview = self.service.dispatch("details", args)
        return self.service.dispatch(
            "details",
            {**args, "apply": True, "expected_plan": preview["plan_revision"]},
        )

    def test_association_retarget_and_place_cycle(self) -> None:
        """Require reference target revisions and reject enclosing-place cycles."""
        person, other = self.create("person"), self.create("person")
        args = {
            **self.args(person, "association"),
            "patch": {"ref": other["handle"], "rel": "Synthetic association"},
        }
        with self.assertRaises(ValueError):
            self.service.dispatch("details", args)
        self.apply(
            {
                **args,
                "target_revisions": {"person:" + other["handle"]: other["revision"]},
            }
        )
        self.assertEqual(
            self.db.get_person_from_handle(person["handle"]).person_ref_list[0].ref,
            other["handle"],
        )
        place, enclosing = self.create("place"), self.create("place")
        self.apply(
            {
                **self.args(place, "enclosing_place"),
                "patch": {"ref": enclosing["handle"]},
                "target_revisions": {
                    "place:" + enclosing["handle"]: enclosing["revision"]
                },
            }
        )
        current = self.service.dispatch(
            "object", {"kind": "place", "handle": place["handle"]}
        )
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.service.dispatch(
                "details",
                {
                    **self.args(enclosing, "enclosing_place"),
                    "patch": {"ref": place["handle"]},
                    "target_revisions": {
                        "place:" + place["handle"]: current["revision"]
                    },
                },
            )

    def test_lds_native_owner_types_and_targets(self) -> None:
        """Use native integer status/type fields and guarded place/family links."""
        person, family, place = (
            self.create("person"),
            self.create("family"),
            self.create("place"),
        )
        self.apply(
            {
                **self.args(person, "lds_ordinance"),
                "patch": {
                    "type": 2,
                    "status": 1,
                    "place": place["handle"],
                    "famc": family["handle"],
                },
                "target_revisions": {
                    "place:" + place["handle"]: place["revision"],
                    "family:" + family["handle"]: family["revision"],
                },
            }
        )
        self.apply(self.args(family, "lds_ordinance"))
        self.assertEqual(
            self.db.get_family_from_handle(family["handle"]).lds_ord_list[0].type, 3
        )
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "details", {**self.args(family, "lds_ordinance"), "patch": {"type": 0}}
            )

    def test_styled_ranges_internal_links_and_reload(self) -> None:
        """Keep exact formatting/link ranges and reject out-of-range tags."""
        note = self.create("note", {"text": {"string": "Synthetic note"}})
        person = self.create("person")
        self.apply(
            {
                **self.args(note, "styled_tag"),
                "patch": {"name": {"value": 0}, "ranges": [[0, 9]], "value": None},
            }
        )
        self.apply(
            {
                **self.args(note, "styled_tag"),
                "patch": {
                    "name": {"value": 8},
                    "ranges": [[0, 9]],
                    "value": "gramps://Person/handle/" + person["handle"],
                },
                "target_revisions": {"person:" + person["handle"]: person["revision"]},
            }
        )
        saved = self.db.get_note_from_handle(note["handle"])
        self.assertEqual(saved.get(), "Synthetic note")
        self.assertIn(
            ("Person", person["handle"]), saved.get_referenced_handles_recursively()
        )
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "details",
                {
                    **self.args(note, "styled_tag"),
                    "patch": {"name": {"value": 0}, "ranges": [[0, 500]]},
                },
            )

    def test_promote_preserves_duplicate_names(self) -> None:
        """Remove only the selected alternate and append the full old primary."""
        person = self.create("person", {"primary_name": {"first_name": "Primary"}})
        original = person["data"]["primary_name"]
        alternate = {**original, "first_name": "Alternate"}
        current = self.service.dispatch(
            "mutate",
            {
                "kind": "person",
                "operation": "update",
                "handle": person["handle"],
                "expected_revision": person["revision"],
                "patch": {"alternate_names": [alternate, alternate]},
                "apply": True,
            },
        )["after"][0]
        self.apply(self.args(current, "alternate_name", "promote", 0))
        saved = self.db.get_person_from_handle(person["handle"])
        self.assertEqual(saved.primary_name.first_name, "Alternate")
        self.assertEqual(
            [name.first_name for name in saved.alternate_names],
            ["Alternate", "Primary"],
        )


if __name__ == "__main__":
    unittest.main()
