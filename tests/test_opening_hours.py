from datetime import date

from backend.schedule.opening_hours import (
    opening_intervals_for_date,
    visit_fits_opening_hours,
)


def test_parses_weekday_ranges_and_returns_only_matching_day_hours():
    weekly = "周一至周五:10:00-16:00；周六至周日:09:00-14:00"

    assert opening_intervals_for_date(weekly, date(2026, 9, 21)) == [(600, 960)]
    assert opening_intervals_for_date(weekly, date(2026, 9, 27)) == [(540, 840)]


def test_unknown_or_holiday_exception_hours_do_not_constrain_planning():
    assert opening_intervals_for_date(None, date(2026, 9, 21)) is None
    assert opening_intervals_for_date("以景区公告为准", date(2026, 9, 21)) is None
    assert (
        opening_intervals_for_date(
            "周一至周五:09:00-17:00（法定节假日除外）",
            date(2026, 9, 21),
        )
        is None
    )


def test_missing_departure_date_only_applies_uniform_weekly_hours():
    assert opening_intervals_for_date("09:00-17:00", None) == [(540, 1020)]
    assert (
        opening_intervals_for_date(
            "周一至周五:09:00-17:00；周六至周日:10:00-16:00",
            None,
        )
        is None
    )


def test_visit_must_fit_at_the_planned_time_without_waiting():
    weekly = "周一至周日:10:00-16:00"

    assert not visit_fits_opening_hours(weekly, date(2026, 9, 21), 8 * 60, 90)
    assert visit_fits_opening_hours(weekly, date(2026, 9, 21), 10 * 60, 90)
    assert visit_fits_opening_hours(weekly, date(2026, 9, 21), 14 * 60 + 30, 90)
    assert not visit_fits_opening_hours(weekly, date(2026, 9, 21), 14 * 60 + 31, 90)


def test_explicit_closed_day_rejects_visit_but_unknown_day_is_unconstrained():
    weekly = "周一:闭馆；周二至周日:09:00-17:00"

    assert opening_intervals_for_date(weekly, date(2026, 9, 21)) == []
    assert not visit_fits_opening_hours(weekly, date(2026, 9, 21), 8 * 60, 90)
    assert opening_intervals_for_date(weekly, date(2026, 9, 22)) == [(540, 1020)]


def test_weekday_closure_exception_in_open_interval_is_treated_as_unknown():
    weekly = "周一至周日:09:00-17:00（周三闭馆）"

    assert opening_intervals_for_date(weekly, date(2026, 9, 23)) is None
