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
"""Native detail/date/layout regression checks using synthetic data only."""

# ------------------------
# Python modules
# ------------------------
import copy
from collections.abc import Sequence
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest

# ------------------------
# Gramps modules
# ------------------------
from gi.repository import Gtk
from gramps.gen.lib import Date
from gramps.gen.plug import BasePluginManager
from gramps.gen.utils.callback import Callback
from gramps.gui.widgets.grampletpane import GrampletPane
from gramps.plugins.db.dbapi.sqlite import SQLite

# ------------------------
# Gramps specific
# ------------------------
import date_support
import navigation_support
import support


# ------------------------------------------------------------
# DateOffsets
# ------------------------------------------------------------
class DateOffsets(unittest.TestCase):
    """Exercise explicit calendar arithmetic and native precision guards."""

    def test_calendar_targets(self) -> None:
        """Check rollover, leap years, BCE boundaries and combined offsets."""
        cases = [
            ((2023, 1, 31), 0, 13, (2024, 2, 29)),
            ((2023, 1, 31), 0, 23, (2024, 12, 31)),
            ((2024, 1, 31), 0, -13, (2022, 12, 31)),
            ((1, 1, 1), 0, -1, (-1, 12, 1)),
            ((-1, 12, 1), 0, 1, (1, 1, 1)),
            ((1, 6, 1), -1, 0, (-1, 6, 1)),
            ((-1, 6, 1), 1, 0, (1, 6, 1)),
            ((-1, 1, 31), 0, 1, (-1, 2, 29)),
            ((-2, 1, 31), 0, 1, (-2, 2, 28)),
            ((-1, 2, 29), 1, 0, (1, 2, 28)),
            ((2020, 2, 29), 1, 1, (2021, 3, 29)),
        ]
        for source, years, months, target in cases:
            with self.subTest(source=source, years=years, months=months):
                date = Date(*source)
                date.set_text_value("synthetic original")
                before = date.serialize()
                result, _ = date_support.calendar_offset(date, years, months, "clamp")
                self.assertEqual(result.get_ymd(), target)
                self.assertEqual(date.serialize(), before)
                self.assertEqual(result.get_text(), date.get_text())
                self.assertTrue(result.is_valid())

    def test_calendar_leap_rules(self) -> None:
        """Retain Julian/Gregorian leap rules including civil BCE years."""
        for calendar, expected_day in ((Date.CAL_GREGORIAN, 28), (Date.CAL_JULIAN, 29)):
            for year in (1900, -101):
                with self.subTest(calendar=calendar, year=year):
                    date = Date()
                    date.set(calendar=calendar, value=(31, 7, year, False))
                    result, clamped = date_support.calendar_offset(date, 0, -5, "clamp")
                    self.assertEqual(result.get_ymd(), (year, 2, expected_day))
                    self.assertEqual(result.get_calendar(), calendar)
                    self.assertTrue(clamped)

    def test_invalid_offsets_preserve_source(self) -> None:
        """Reject nonexistent days, coercion, unsupported calendars and policy."""
        date = Date(2023, 1, 31)
        before = date.serialize()
        for args in (
            (0, 1, "reject"),
            (True, 1, "clamp"),
            (0, 1.5, "clamp"),
            (10001, 0, "clamp"),
            (0, 1, "rollover"),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                date_support.calendar_offset(date, *args)
        self.assertEqual(date.serialize(), before)
        for calendar in (Date.CAL_HEBREW, Date.CAL_FRENCH):
            other = copy.deepcopy(date)
            other.convert_calendar(calendar)
            with self.assertRaises(ValueError):
                date_support.calendar_offset(other, 1, 0)

    def test_dispatch_precision_and_units(self) -> None:
        """Keep legacy day offsets and reject ambiguous or partial inputs."""
        result = date_support.dispatch(
            {
                "operation": "offset",
                "text": "31 January 2023",
                "months": 1,
                "nonexistent_day": "clamp",
            }
        )
        self.assertEqual(result["result"]["data"]["dateval"][:3], [28, 2, 2023])
        self.assertTrue(result["day_clamped"])
        self.assertFalse(result["records_changed"])
        for text in (
            "1900",
            "about 1 January 1900",
            "1 January 1700/1",
            "undated manuscript",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                date_support.dispatch(
                    {"operation": "offset", "text": text, "months": 1}
                )
        with self.assertRaises(ValueError):
            date_support.dispatch(
                {"operation": "offset", "text": "1 January 1900", "days": 1, "years": 1}
            )
        self.assertEqual(
            date_support.dispatch(
                {"operation": "offset", "text": "28 February 2000", "days": 1}
            )["result"]["data"]["dateval"][:3],
            [29, 2, 2000],
        )


# ------------------------------------------------------------
# DetailLifecycle
# ------------------------------------------------------------
class DetailLifecycle(unittest.TestCase):
    """Exercise real SQLite memory commits, previews and native reloads."""

    def setUp(self) -> None:
        """Create only a disposable native memory database."""
        self.db = SQLite()
        self.db.load(":memory:")
        self.state = SimpleNamespace(
            db=self.db, connect=lambda *args: None, is_open=lambda: False
        )
        bridge = SimpleNamespace(dbstate=self.state, windows=lambda: [])
        self.service = support.GrampsSupport(bridge, self.state)
        self.person = self.create("person")

    def tearDown(self) -> None:
        """Close the disposable database."""
        self.db.close()

    def create(self, kind: str) -> str:
        """Return one synthetic primary handle.

        :param kind: Native primary kind.
        :returns: Synthetic handle.
        """
        return self.service.dispatch(
            "mutate", {"kind": kind, "operation": "create", "apply": True}
        )["after"][0]["handle"]

    def args(
        self,
        operation: str,
        path: Sequence[str | int],
        kind: str = "person",
        handle: str | None = None,
    ) -> dict[str, Any]:
        """Build arguments from the current owner and selected collection/item.

        :param operation: Detail lifecycle operation.
        :param path: Observed path.
        :param kind: Primary owner kind.
        :param handle: Synthetic owner handle.
        :returns: Current revision-bound operation.
        """
        owner = self.service.dispatch(
            "object", {"kind": kind, "handle": handle or self.person}
        )
        value = owner["data"]
        for part in path:
            value = value[part]
        return {
            "kind": kind,
            "handle": handle or self.person,
            "operation": operation,
            "path": list(path),
            "expected_revision": owner["revision"],
            "expected_secondary_revision": support.revision(value),
        }

    def call(self, args: dict[str, Any], **extra: Any) -> dict[str, Any]:
        """Dispatch one native detail request.

        :param args: Revision-bound arguments.
        :param extra: Overrides for the request.
        :returns: Preview or change receipt.
        """
        return self.service.dispatch("secondary", {**args, **extra})

    def test_all_detail_classes_reload(self) -> None:
        """Add each supported native class to an observed empty/default list."""
        cases = [
            ("person", ["alternate_names"], "Name"),
            ("person", ["primary_name", "surname_list"], "Surname"),
            ("person", ["address_list"], "Address"),
            ("person", ["attribute_list"], "Attribute"),
            ("source", ["attribute_list"], "SrcAttribute"),
            ("repository", ["urls"], "Url"),
            ("place", ["alt_names"], "PlaceName"),
            ("place", ["alt_loc"], "Location"),
        ]
        for kind, path, name in cases:
            with self.subTest(kind=kind, name=name):
                handle = self.create(kind)
                listing = self.service.dispatch(
                    "secondary",
                    {"kind": kind, "handle": handle, "operation": "collections"},
                )
                item = next(
                    item for item in listing["collections"] if item["path"] == path
                )
                self.assertEqual(item["class_name"], name)
                args = self.args("add", path, kind, handle)
                preview = self.call(args)
                self.assertFalse(preview["applied"])
                self.assertEqual(
                    self.service.dispatch("object", {"kind": kind, "handle": handle})[
                        "revision"
                    ],
                    args["expected_revision"],
                )
                saved = self.call(args, apply=True)
                self.assertTrue(saved["applied"])
                self.assertTrue(self.service.get(kind, handle=handle).serialize())

    def test_order_remove_and_undo(self) -> None:
        """Reorder/remove chosen details and restore with native undo."""
        path = ["alternate_names"]
        for first in ("First", "Second", "Third"):
            self.call(self.args("add", path), patch={"first_name": first}, apply=True)
        ordered = self.call(self.args("reorder", path), order=[2, 0, 1], apply=True)
        self.assertEqual(
            [n["first_name"] for n in ordered["after"][0]["data"]["alternate_names"]],
            ["Third", "First", "Second"],
        )
        self.call(self.args("remove", path + [1]), apply=True)
        self.assertEqual(
            [
                n.get_first_name()
                for n in self.db.get_person_from_handle(
                    self.person
                ).get_alternate_names()
            ],
            ["Third", "Second"],
        )
        self.db.undo()
        self.assertEqual(
            [
                n.get_first_name()
                for n in self.db.get_person_from_handle(
                    self.person
                ).get_alternate_names()
            ],
            ["Third", "First", "Second"],
        )

    def test_staleness_and_noop(self) -> None:
        """Reject stale array/owner revisions and avoid no-op commits."""
        args = self.args("add", ["urls"])
        with self.assertRaises(ValueError):
            self.call(args, expected_secondary_revision="stale", apply=True)
        self.call(args, apply=True)
        with self.assertRaises(ValueError):
            self.call(args, apply=True)
        current = self.args("reorder", ["urls"])
        result = self.call(current, order=[0], apply=True)
        self.assertFalse(result["records_changed"])
        self.assertEqual(
            self.args("reorder", ["urls"])["expected_revision"],
            current["expected_revision"],
        )

    def test_rejected_shapes_and_protected_arrays(self) -> None:
        """Reject malformed orders, reference arrays, singleton fields and links."""
        args = self.args("add", ["alternate_names"])
        with self.assertRaises(ValueError):
            self.call(args, apply="false")
        for patch in (
            {"first_name": 123},
            {"citation_list": ["missing"]},
            {"surname_list": [{"_class": "Date"}]},
            {"_class": "Address"},
            {"date": {"calendar": 999}},
            {"date": {"quality": 999}},
            {"date": {"modifier": 999}},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.call(args, patch=patch, apply=True)
        for index in (-1, True, 1.5, 1):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.call(args, index=index, apply=True)
        for path in (["event_ref_list"], ["primary_name"], ["person_ref_list"]):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call({**args, "path": path}, apply=True)
        self.call(args, patch={"first_name": "First"}, apply=True)
        reorder = self.args("reorder", ["alternate_names"])
        for order in ([], [True], [1], [0, 0]):
            with self.subTest(order=order), self.assertRaises(ValueError):
                self.call(reorder, order=order, apply=True)

    def test_native_surname_defaults(self) -> None:
        """Keep one primary surname and restore the native blank placeholder."""
        path = ["primary_name", "surname_list"]
        self.call(self.args("add", path), patch={"surname": "First"}, apply=True)
        self.call(self.args("add", path), patch={"surname": "Second"}, apply=True)
        names = (
            self.db.get_person_from_handle(self.person)
            .get_primary_name()
            .get_surname_list()
        )
        self.assertEqual(sum(n.get_primary() for n in names), 1)
        with self.assertRaises(ValueError):
            self.call(self.args("add", path), patch={"primary": True}, apply=True)
        while (
            len(
                self.db.get_person_from_handle(self.person)
                .get_primary_name()
                .get_surname_list()
            )
            > 1
        ):
            self.call(self.args("remove", path + [0]), apply=True)
        self.call(self.args("remove", path + [0]), apply=True)
        names = (
            self.db.get_person_from_handle(self.person)
            .get_primary_name()
            .get_surname_list()
        )
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0].get_surname(), "")

    def test_nested_reference_attributes(self) -> None:
        """Edit a typed attribute list without changing its event reference."""
        event = self.create("event")
        current = self.service.dispatch(
            "object", {"kind": "person", "handle": self.person}
        )
        self.service.dispatch(
            "attach",
            {
                "kind": "person",
                "handle": self.person,
                "expected_revision": current["revision"],
                "target_kind": "event",
                "target_handle": event,
                "apply": True,
            },
        )
        path: list[str | int] = ["event_ref_list", 0, "attribute_list"]
        self.call(
            self.args("add", path), patch={"value": "Synthetic detail"}, apply=True
        )
        person = self.db.get_person_from_handle(self.person)
        self.assertEqual(person.get_event_ref_list()[0].ref, event)
        self.assertEqual(
            person.get_event_ref_list()[0].get_attribute_list()[0].get_value(),
            "Synthetic detail",
        )
        remove = self.args("remove", path + [0])
        with self.assertRaises(ValueError):
            self.call(remove, expected_secondary_revision="stale", apply=True)
        self.call(remove, apply=True)
        self.assertEqual(
            self.db.get_person_from_handle(self.person).get_event_ref_list()[0].ref,
            event,
        )

    def test_empty_compound_date_and_metadata(self) -> None:
        """Preserve native blank range/span tuples while checking date codes."""
        for modifier in (Date.MOD_RANGE, Date.MOD_SPAN):
            with self.subTest(modifier=modifier):
                result = self.call(
                    self.args("add", ["alternate_names"]),
                    patch={
                        "date": {
                            "modifier": modifier,
                            "dateval": [0, 0, 0, False, 0, 0, 0, False],
                        }
                    },
                    apply=True,
                )
                date = result["after"][0]["data"]["alternate_names"][-1]["date"]
                self.assertEqual(date["modifier"], modifier)
                self.assertEqual(date["dateval"], [0, 0, 0, False, 0, 0, 0, False])
                self.assertEqual(date["sortval"], 0)


# ------------------------------------------------------------
# FixtureState
# ------------------------------------------------------------
class FixtureState(Callback):
    """Expose real native callback bookkeeping without a live tree."""

    __signals__ = {"database-changed": (object,), "no-database": ()}

    def __init__(self, db: Any) -> None:
        """Use the supplied synthetic database.

        :param db: Memory-only native database.
        """
        super().__init__()
        self.db = db

    def is_open(self) -> bool:
        """Return native synthetic database state.

        :returns: Whether the synthetic database is open.
        """
        return self.db.is_open()


# ------------------------------------------------------------
# DashboardLayout
# ------------------------------------------------------------
class DashboardLayout(unittest.TestCase):
    """Use a real GTK Dashboard with a synthetic local Gramplet only."""

    def setUp(self) -> None:
        """Create an isolated Dashboard and temporary fixture registration."""
        self.db = SQLite()
        self.db.load(":memory:")
        self.state = FixtureState(self.db)
        self.temp = tempfile.TemporaryDirectory(prefix="gramps-dashboard-fixture-")
        folder = Path(self.temp.name)
        (folder / "fixture.py").write_text(
            "from gramps.gen.plug import Gramplet\nclass DashboardFixture(Gramplet):\n    def init(self):\n        self.append_text('Synthetic fixture')\n",
            encoding="utf-8",
        )
        (folder / "fixture.gpr.py").write_text(
            "register(GRAMPLET, id='synthetic-dashboard-fixture', name='Synthetic Dashboard Fixture', description='Synthetic only', version='1.0', gramps_target_version='6.1', status=STABLE, fname='fixture.py', gramplet='DashboardFixture', gramplet_title='Synthetic Dashboard Fixture', navtypes=['Dashboard'])",
            encoding="utf-8",
        )
        self.ui = SimpleNamespace(
            connect=lambda *args: None,
            emit=lambda *args: None,
            get_active=lambda *args: None,
            register=lambda *args: None,
            get_history=lambda *args: None,
            window=Gtk.ApplicationWindow(),
        )
        self.page = SimpleNamespace(navigation_type=lambda: "Dashboard", active=True)
        manager = BasePluginManager.get_instance()
        manager.reg_plugins(str(folder), self.state, self.ui, load_on_reg=False)
        self.pane = GrampletPane(
            "synthetic-dashboard-layout",
            self.page,
            self.state,
            self.ui,
            default_gramplets=[],
        )
        self.page.widget = self.pane
        self.ui.viewmanager = SimpleNamespace(active_page=self.page)
        self.context = SimpleNamespace(
            bridge=SimpleNamespace(
                uistate=self.ui,
                session="synthetic-layout",
                identify=lambda obj: str(id(obj)),
            )
        )

    def tearDown(self) -> None:
        """Destroy fixture widgets and temporary source without saving layouts."""
        self.pane.destroy()
        self.ui.window.destroy()
        self.temp.cleanup()
        self.db.close()

    def layout(self, operation: str = "list", **args: Any) -> dict[str, Any]:
        """Return or change the synthetic Dashboard layout.

        :param operation: Native layout operation.
        :param args: Observed operation fields.
        :returns: Layout response.
        """
        current = navigation_support.dashboard(self.context, {}, support.revision)
        return navigation_support.dashboard(
            self.context,
            {"operation": operation, "expected_revision": current["revision"], **args},
            support.revision,
        )

    def test_dashboard_lifecycle(self) -> None:
        """Add duplicate instances, move/collapse/close/restore by instance ID."""
        result = self.layout("add", name="synthetic-dashboard-fixture")
        ident = result["items"][0]["instance_id"]
        result = self.layout("add", name="synthetic-dashboard-fixture")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(len({item["title"] for item in result["items"]}), 2)
        self.layout("columns", columns=3)
        result = self.layout("move", instance_id=ident, column=2, row=0)
        self.assertEqual(
            next(item for item in result["items"] if item["instance_id"] == ident)[
                "column"
            ],
            2,
        )
        result = self.layout("state", instance_id=ident, state="minimized")
        self.assertEqual(
            next(item for item in result["items"] if item["instance_id"] == ident)[
                "state"
            ],
            "minimized",
        )
        result = self.layout("remove", instance_id=ident)
        self.assertEqual(
            next(item for item in result["items"] if item["instance_id"] == ident)[
                "state"
            ],
            "closed",
        )
        result = self.layout("restore", instance_id=ident)
        self.assertEqual(
            next(item for item in result["items"] if item["instance_id"] == ident)[
                "state"
            ],
            "maximized",
        )

    def test_dashboard_rejections_preserve_layout(self) -> None:
        """Reject stale layouts, hidden IDs and invalid columns/state before writes."""
        before = self.layout()
        invalid: list[tuple[str, dict[str, Any]]] = [
            ("columns", {"columns": True}),
            ("columns", {"columns": 0}),
            ("add", {"name": "missing-plugin"}),
            ("remove", {"instance_id": "missing"}),
        ]
        for operation, args in invalid:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.layout(operation, **args)
        with self.assertRaises(ValueError):
            self.layout("columns", columns=2, expected_revision="stale")
        self.assertEqual(self.layout()["revision"], before["revision"])

    def test_dashboard_rows_after_native_close(self) -> None:
        """Use visible rows after a native close leaves cached row values stale."""
        for _ in range(3):
            self.layout("add", name="synthetic-dashboard-fixture")
        children = list(self.pane.columns[0].get_children())
        first = self.pane.frame_map[str(children[0])]
        second = self.pane.frame_map[str(children[1])]
        ident = self.context.bridge.identify(second.mainframe)
        first.close()  # Reproduce an external native UI close, not this wrapper.
        listed = self.layout()
        self.assertEqual(
            next(item for item in listed["items"] if item["instance_id"] == ident)[
                "row"
            ],
            0,
        )
        moved = self.layout("move", instance_id=ident, column=0, row=1)
        self.assertTrue(moved["layout_changed"])
        self.assertIs(self.pane.columns[0].get_children()[1], second.mainframe)

    def test_failed_content_constructor_cleanup(self) -> None:
        """Remove callbacks and GUI shells when native post-init raises."""
        manager = BasePluginManager.get_instance()
        module = manager.load_plugin(manager.get_plugin("synthetic-dashboard-fixture"))
        cls = module.DashboardFixture
        original = cls.post_init

        def cb_fail(_content: Any) -> None:
            """Raise after native state callbacks have been registered."""
            raise RuntimeError("Synthetic constructor failure")

        before = self.layout()["revision"]
        callbacks = {
            key: list(values)
            for key, values in getattr(self.state, "_Callback__callback_map").items()
        }
        cls.post_init = cb_fail
        try:
            with self.assertRaises(RuntimeError):
                self.layout("add", name="synthetic-dashboard-fixture")
        finally:
            cls.post_init = original
        self.assertEqual(self.layout()["revision"], before)
        self.assertFalse(self.pane.frame_map)
        self.assertEqual(
            sum(
                len(values)
                for values in getattr(self.state, "_Callback__callback_map").values()
            ),
            sum(len(values) for values in callbacks.values()),
        )


def run_checks() -> list[str]:
    """Run focused unittest cases inside the isolated native runtime.

    :returns: Successful unittest identifiers for the existing verification receipt.
    """
    suite = unittest.defaultTestLoader.loadTestsFromModule(
        __import__(__name__, fromlist=["*"])
    )
    names = []

    def cb_names(tests: Any) -> None:
        """Collect individual test identifiers before unittest consumes the suite."""
        for case in tests:
            if isinstance(case, unittest.TestSuite):
                cb_names(case)
            else:
                names.append(case.id())

    cb_names(suite)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(output.getvalue())
    return names


if __name__ == "__main__":
    unittest.main()
