#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2026  Gramps Desktop plugin contributors
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
"""Exercise additional native exporters with a synthetic family and temporary output."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

# -------------------------------------------------------------------------
# Gramps modules
# -------------------------------------------------------------------------
from gramps.gen import lib
from gramps.gen.db import DbTxn
from gramps.gen.lib.json_utils import object_to_dict
from gramps.plugins.db.dbapi.sqlite import SQLite

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import support


# -------------------------------------------------------------------------
# AdditionalExports
# -------------------------------------------------------------------------
class AdditionalExports(unittest.TestCase):
    """Generate real native formats without accessing family data."""

    def setUp(self) -> None:
        """Create two parents, a child and a complete Gregorian birth event."""
        self.db = SQLite()
        self.db.load(":memory:")
        self.state = SimpleNamespace(db=self.db)
        self.service = support.GrampsSupport(
            SimpleNamespace(dbstate=self.state, windows=lambda: []), self.state
        )
        with DbTxn("Synthetic exporters", self.db) as txn:
            self.people = []
            for name, gender in (
                ("SyntheticFather", 1),
                ("SyntheticMother", 0),
                ("SyntheticChild", 1),
            ):
                person = lib.Person()
                person.gender = gender
                person.primary_name.first_name = name
                person.primary_name.set_surname_list([lib.Surname()])
                person.primary_name.surname_list[0].surname = "Fixture"
                self.db.add_person(person, txn)
                self.people.append(person)
            event = lib.Event()
            event.set_type(lib.EventType.BIRTH)
            event.date.set_yr_mon_day(1900, 2, 3)
            self.db.add_event(event, txn)
            ref = lib.EventRef()
            ref.ref = event.handle
            ref.set_role(lib.EventRoleType.PRIMARY)
            self.people[2].add_event_ref(ref)
            self.people[2].birth_ref_index = 0
            family = lib.Family()
            family.father_handle, family.mother_handle = (
                self.people[0].handle,
                self.people[1].handle,
            )
            child = lib.ChildRef()
            child.ref = self.people[2].handle
            family.add_child_ref(child)
            self.db.add_family(family, txn)
            for index, person in enumerate(self.people):
                if index < 2:
                    person.add_family_handle(family.handle)
                else:
                    person.add_parent_family_handle(family.handle)
                self.db.commit_person(person, txn)
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Close the synthetic database and remove its disposable output."""
        self.db.close(update=False)
        self.temporary.cleanup()

    def test_five_native_formats_and_loss_receipts(self) -> None:
        """Verify representative emitted content and unchanged source records."""
        before = [
            object_to_dict(self.db.get_person_from_handle(person.handle))
            for person in self.people
        ]
        for name, suffix, marker in (
            ("csv", ".csv", "SyntheticChild"),
            ("web_family_tree", ".wft", "SyntheticChild"),
            ("geneweb", ".gw", "SyntheticFather"),
            ("vcalendar", ".vcs", "BEGIN:VCALENDAR"),
            ("vcard", ".vcf", "BEGIN:VCARD"),
        ):
            with self.subTest(format=name):
                args = {
                    "operation": "run",
                    "format": name,
                    "output_path": str(self.folder / (name + suffix)),
                }
                plan = self.service.dispatch("export", args)
                self.assertFalse(plan["lossless_native"])
                receipt = self.service.dispatch(
                    "export",
                    {**args, "apply": True, "expected_plan": plan["plan_revision"]},
                )
                self.assertGreater(receipt["file_size"], 0)
                self.assertIn(
                    marker, Path(args["output_path"]).read_text(encoding="utf-8-sig")
                )
        self.assertEqual(
            before,
            [
                object_to_dict(self.db.get_person_from_handle(person.handle))
                for person in self.people
            ],
        )

    def test_csv_options_cycle_and_stale_destination(self) -> None:
        """Reject native place cycles and preserve changed output files."""
        args = {
            "operation": "run",
            "format": "csv",
            "output_path": str(self.folder / "fixture.csv"),
        }
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "export",
                {
                    **args,
                    "options": {
                        key: False
                        for key in (
                            "include_individuals",
                            "include_marriages",
                            "include_children",
                            "include_places",
                        )
                    },
                },
            )
        plan = self.service.dispatch("export", {**args, "overwrite": True})
        Path(args["output_path"]).write_text("later file")
        with self.assertRaises(ValueError):
            self.service.dispatch(
                "export",
                {
                    **args,
                    "overwrite": True,
                    "apply": True,
                    "expected_plan": plan["plan_revision"],
                },
            )
        self.assertEqual(Path(args["output_path"]).read_text(), "later file")
        place = lib.Place()
        with DbTxn("Synthetic place cycle", self.db) as txn:
            self.db.add_place(place, txn)
            ref = lib.PlaceRef()
            ref.ref = place.handle
            place.add_placeref(ref)
            self.db.commit_place(place, txn)
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.service.dispatch(
                "export", {**args, "output_path": str(self.folder / "cycle.csv")}
            )


if __name__ == "__main__":
    unittest.main()
