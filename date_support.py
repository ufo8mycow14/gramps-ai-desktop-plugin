"""Detached native date operations that preserve precision and UI preferences."""

# ------------------------
# Python modules
# ------------------------
import copy
from typing import Any

# ------------------------
# Gramps modules
# ------------------------
from gramps.gen.const import GRAMPS_LOCALE as glocale
from gramps.gen.errors import DateError
from gramps.gen.lib import Date
from gramps.gen.lib.json_utils import object_to_dict
from gramps.gen.datehandler import parser, displayer

_ = glocale.translation.gettext


def calendar_offset(
    date: Date, years: int, months: int, policy: str = "reject"
) -> tuple[Date, bool]:
    """Shift civil calendar components without native rollover or year zero.

    :param date: Exact Gregorian or Julian date.
    :param years: Signed calendar years.
    :param months: Signed calendar months, applied with years as one shift.
    :param policy: Reject or clamp a nonexistent target day.
    :returns: Detached resulting date and whether the day was clamped.
    """
    if (
        not date.is_valid()
        or not date.is_regular()
        or date.get_slash()
        or date.get_new_year() != Date.NEWYEAR_JAN1
        or date.get_calendar() not in (Date.CAL_GREGORIAN, Date.CAL_JULIAN)
    ):
        raise ValueError(
            _("Month/year offsets require an exact Gregorian or Julian date")
        )
    if (
        type(years) is not int
        or type(months) is not int
        or not -10000 <= years <= 10000
        or not -120000 <= months <= 120000
    ):
        raise ValueError(
            _("Calendar offsets are bounded to 10,000 years or 120,000 months")
        )
    if policy not in ("reject", "clamp"):
        raise ValueError(_("Select reject or clamp for nonexistent target days"))
    # Gramps civil BCE numbering skips zero; convert solely for arithmetic.
    civil_year = date.get_year()
    continuous_year = civil_year if civil_year > 0 else civil_year + 1
    target_year, target_month = divmod(
        continuous_year * 12 + date.get_month() - 1 + years * 12 + months, 12
    )
    target_year = target_year if target_year > 0 else target_year - 1
    target_month += 1
    try:
        copy.deepcopy(date).set(
            calendar=date.get_calendar(),
            value=(1, target_month, target_year, False),
            newyear=date.get_new_year(),
        )
    except DateError as error:
        raise ValueError(_("Invalid target calendar month or year")) from error
    for day in range(date.get_day(), 0, -1):
        result = copy.deepcopy(date)
        try:
            result.set(
                quality=date.get_quality(),
                modifier=date.get_modifier(),
                calendar=date.get_calendar(),
                value=(day, target_month, target_year, False),
                text=date.get_text(),
                newyear=date.get_new_year(),
            )
        except DateError as error:
            if policy == "reject":
                raise ValueError(
                    _(
                        "The target day does not exist; select clamp to use the last valid day"
                    )
                ) from error
        else:
            if not result.is_regular() or not result.is_valid():
                raise ValueError(_("The target must remain an exact complete date"))
            return result, day != date.get_day()
    raise ValueError(_("No valid target day exists in the requested month"))


def read(a: dict[str, Any], key: str = "text") -> Date:
    """Parse detached native input.

    :param a: Request arguments.
    :param key: Date text field.
    :returns: Native parsed date.
    """
    text = a.get(key)
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError("Supply a date string of up to 2,000 characters")
    return parser.parse(text)


def describe(date: Date, display: Any = displayer) -> dict[str, Any]:
    """Describe native date precision.

    :param date: Detached date.
    :param display: Call-local date displayer.
    :returns: Native representation and precision.
    """
    return {
        "data": object_to_dict(date),
        "display": display.display(date),
        "valid": date.is_valid(),
        "empty": date.is_empty(),
        "text_only": date.get_modifier() == Date.MOD_TEXTONLY,
        "precision": {
            "start": {
                "year": bool(date.get_year()),
                "month": bool(date.get_month()),
                "day": bool(date.get_day()),
            },
            "stop": (
                {
                    "year": bool(date.get_stop_year()),
                    "month": bool(date.get_stop_month()),
                    "day": bool(date.get_stop_day()),
                }
                if date.is_compound()
                else None
            ),
        },
        "calendar": date.get_calendar(),
        "modifier": date.get_modifier(),
        "quality": date.get_quality(),
    }


def dispatch(a: dict[str, Any]) -> dict[str, Any]:
    """Run one detached native date operation.

    :param a: Operation arguments.
    :returns: Native date result.
    """
    operation = a.get("operation", "parse")
    if operation == "options":
        return {
            "calendars": [
                {"code": c, "name": Date.calendar_names[c]} for c in Date.CALENDARS
            ],
            "formats": [
                {"index": i, "label": label}
                for i, label in enumerate(displayer.formats)
            ],
            "comparisons": ["identity", "=", "<", ">", "<=", ">=", "<<", ">>"],
            "offset_units": ["days", "months", "years"],
            "calendar_offset_calendars": [Date.CAL_GREGORIAN, Date.CAL_JULIAN],
            "nonexistent_day_policies": ["reject", "clamp"],
            "records_changed": False,
        }
    date = read(a)
    if operation == "parse":
        # The legacy text-only call retains its exact response contract.
        return {
            "data": object_to_dict(date),
            "display": displayer.display(date),
            "valid": date.is_valid(),
        }
    original = describe(date)
    if operation == "format":
        index = a.get("format_index", displayer.format)
        if type(index) is not int or not 0 <= index < len(displayer.formats):
            raise ValueError("Select an observed native format index")
        local = type(displayer)(index, blocale=displayer._locale)
        return {
            **describe(date, local),
            "format_index": index,
            "records_changed": False,
        }
    if operation == "compare":
        other = read(a, "other_text")
        comparison = a.get("comparison", "=")
        if comparison == "identity":
            matched = date.serialize() == other.serialize()
        elif comparison in ("=", "<", ">", "<=", ">=", "<<", ">>"):
            if (
                date.is_empty()
                or other.is_empty()
                or not date.is_valid()
                or not other.is_valid()
            ):
                raise ValueError(
                    "Native interval matching requires two valid non-empty dates"
                )
            matched = date.match(other, comparison)
        else:
            raise ValueError(
                "Select an observed comparison; identity compares the complete native representation"
            )
        from gramps.gen.config import config

        return {
            "original": original,
            "other": describe(other),
            "comparison": comparison,
            "matched": bool(matched),
            "matching_convention": "Native finite date intervals; identity uses complete serialisation",
            "date_range_preferences": {
                key: config.get("behavior." + key)
                for key in ("date-before-range", "date-after-range", "date-about-range")
            },
            "records_changed": False,
        }
    if operation not in ("calendar", "offset"):
        raise ValueError("Select parse, format, compare, calendar, offset or options")
    if (
        not date.is_regular()
        or date.get_slash()
        or date.get_new_year() != Date.NEWYEAR_JAN1
    ):
        raise ValueError(
            "Conversion/offset requires an exact complete date, January-1 new year and no slash year"
        )
    result = copy.deepcopy(date)
    if operation == "calendar":
        calendar = a.get("calendar")
        if isinstance(calendar, str):
            names = [name.casefold() for name in Date.calendar_names]
            if calendar.casefold() not in names:
                raise ValueError("Select an observed calendar name/code")
            calendar = names.index(calendar.casefold())
        if type(calendar) is not int or calendar not in Date.CALENDARS:
            raise ValueError("Select an observed calendar name/code")
        result.convert_calendar(calendar)
    else:
        if "years" in a or "months" in a:
            if "days" in a:
                raise ValueError(
                    _("Select either day offsets or calendar month/year offsets")
                )
            result, clamped = calendar_offset(
                date,
                a.get("years", 0),
                a.get("months", 0),
                a.get("nonexistent_day", "reject"),
            )
            return {
                "original": original,
                "result": describe(result),
                "records_changed": False,
                "precision_preserved": True,
                "operation": operation,
                "day_clamped": clamped,
                "nonexistent_day": a.get("nonexistent_day", "reject"),
                "arithmetic": "civil calendar; no year zero",
            }
        days = a.get("days")
        if type(days) is not int or not -365000 <= days <= 365000:
            raise ValueError("days must be a signed integer within 365,000 days")
        result = date.offset_date(days)
        result.convert_calendar(date.get_calendar())
        result.set_text_value(date.get_text())
    return {
        "original": original,
        "result": describe(result),
        "records_changed": False,
        "precision_preserved": True,
        "operation": operation,
    }
