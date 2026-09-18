"""Descriptive statistics over a price series.

Facts, not signals. Each says what already happened; none says what to do.
The tests that matter most here are the ones asserting a metric is *absent*
when the series cannot support it - a 200-day average of 30 prices is not a
rough 200-day average, it is a different number wearing the name, and nothing
in the output would tell a reader which they were looking at.
"""
from datetime import date, timedelta

import pytest

import trends


def series(values, start=date(2026, 1, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def flat(n, value=100.0):
    return series([value] * n)


# Wilder, New Concepts in Technical Trading Systems - the published worked
# example. An RSI that agrees with it is the standard one, not an invention.
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
          46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
          46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03,
          44.18, 44.22, 44.57, 43.42, 42.66, 43.13]


class TestRsi:
    def test_matches_wilders_published_example(self):
        assert trends.rsi(series(WILDER[:15])) == pytest.approx(70.46, abs=0.01)

    def test_matches_after_smoothing_continues(self):
        assert trends.rsi(series(WILDER[:16])) == pytest.approx(66.25, abs=0.01)
        assert trends.rsi(series(WILDER)) == pytest.approx(37.79, abs=0.01)

    def test_too_short_a_series_has_no_rsi(self):
        assert trends.rsi(series(WILDER[:10])) is None

    def test_exactly_enough_is_enough(self):
        assert trends.rsi(series(WILDER[:15])) is not None

    def test_an_unbroken_rise_approaches_a_hundred(self):
        assert trends.rsi(series([100 + i for i in range(30)])) == 100.0

    def test_a_flat_series_is_neither(self):
        """No losses to divide by. 50 by convention, not by arithmetic - and
        emphatically not the 100 that an unbroken rise gives."""
        assert trends.rsi(flat(30)) == 50.0


class TestDrawdown:
    def test_reports_the_fall_from_a_peak(self):
        s = series([100 + i for i in range(170)] + [270 - i for i in range(30)])
        d = trends.drawdown(s)
        assert d["peak"] == pytest.approx(270.0)
        assert d["pct"] == pytest.approx((241.0 / 270.0 - 1) * 100)

    def test_dates_the_peak_and_counts_the_days_since(self):
        """The plateau case: a price that stopped rising some time ago. How
        long ago is the part a table of current values cannot show."""
        s = series([100 + i for i in range(160)] + [259.0] * 40)
        d = trends.drawdown(s)
        assert d["peak_on"] == date(2026, 1, 1) + timedelta(days=159)
        assert d["days_since_peak"] == 40

    def test_a_series_at_its_peak_shows_no_drawdown(self):
        assert trends.drawdown(series([100 + i for i in range(200)]))["pct"] == 0

    def test_too_short_a_series_has_no_drawdown(self):
        assert trends.drawdown(flat(20)) is None

    def test_a_series_not_reaching_back_six_months_has_no_drawdown(self):
        """Otherwise it is the high of whatever we happen to hold, reported
        under a label that says six months."""
        assert trends.drawdown(flat(150)) is None

    def test_only_the_window_is_searched_for_the_peak(self):
        """An older, higher price is outside the six months being described."""
        s = series([500.0] + [100.0 + i for i in range(220)])
        assert trends.drawdown(s)["peak"] < 500.0

    def test_a_worthless_series_is_not_a_drawdown(self):
        """Dividing by a peak of zero is not a 100% fall, it is no answer."""
        assert trends.drawdown(flat(200, 0.0)) is None


class TestMovingAverage:
    def test_averages_the_window(self):
        assert trends.moving_average(series([1.0] * 40 + [2.0] * 50), 50) \
            == pytest.approx(2.0)

    def test_reports_position_relative_to_the_average(self):
        s = series([100.0] * 60 + [110.0])
        assert trends.relative_to_average(s, 50) > 0

    def test_an_unsupported_window_is_absent_not_approximated(self):
        """30 prices cannot make a 200-day average. Returning one anyway is
        the failure this guards: the output looks identical either way."""
        assert trends.moving_average(flat(30), 200) is None
        assert trends.relative_to_average(flat(30), 200) is None

    def test_a_session_window_has_no_tolerance(self):
        """Non-trading days are already absent from a price series, so
        holidays are no argument for shortening a session count. 170 closes
        averaged under a 200-session label is a different number, and the
        output cannot be told apart from the real one."""
        assert trends.moving_average(flat(199), 200) is None
        assert trends.moving_average(flat(200), 200) is not None


class TestDescribe:
    def test_reports_what_the_series_supports(self):
        m = trends.describe(series([100 + i * 0.1 for i in range(250)]))
        assert m["sessions"] == 250
        assert m["drawdown"] is not None
        assert m["vs_ma50"] is not None and m["vs_ma200"] is not None
        assert m["rsi"] is not None

    def test_a_short_series_reports_absence_rather_than_guesses(self):
        m = trends.describe(flat(20))
        assert m["sessions"] == 20
        assert m["drawdown"] is None
        assert m["vs_ma50"] is None and m["vs_ma200"] is None
        assert m["rsi"] is not None      # 14 sessions is enough for this one

    def test_an_empty_series_describes_nothing(self):
        assert trends.describe([]) == {}

    def test_rows_are_ordered_before_anything_is_computed(self):
        """A series read back from disk should be sorted, but a metric that
        silently depends on order would be wrong rather than absent."""
        s = series([100 + i for i in range(130)])
        assert trends.describe(list(reversed(s)))["last"] == \
            trends.describe(s)["last"]


class TestCalendarWindow:
    """Six months is whole calendar months, not an averaged day count. Months
    differ in length, so 365.25/12 puts the boundary on the wrong day and a
    peak sitting on it falls outside a window labelled six months."""

    def test_six_months_back_lands_on_the_same_day_of_month(self):
        assert trends._minus_months(date(2026, 9, 18), 6) == date(2026, 3, 18)

    def test_the_averaged_day_count_would_have_missed_it(self):
        """183 days before 18 September is the 19th of March."""
        assert trends._minus_months(date(2026, 9, 18), 6) \
            != date(2026, 9, 18) - timedelta(days=183)

    def test_crossing_a_year_boundary(self):
        assert trends._minus_months(date(2026, 2, 10), 6) == date(2025, 8, 10)

    def test_a_day_absent_from_the_earlier_month_clamps(self):
        """The 31st of August less six months is the end of February."""
        assert trends._minus_months(date(2026, 8, 31), 6) == date(2026, 2, 28)

    def test_a_leap_february_clamps_to_the_29th(self):
        assert trends._minus_months(date(2024, 8, 31), 6) == date(2024, 2, 29)

    def test_a_peak_on_the_boundary_is_inside_the_window(self):
        """The case the averaged count excluded."""
        start, end = date(2026, 3, 18), date(2026, 9, 18)
        days = (end - start).days
        s = [(start + timedelta(days=i),
              200.0 if i == 0 else 100.0) for i in range(days + 1)]
        assert trends.drawdown(s)["peak"] == 200.0
