"""Tests for market/trends.py."""
from datetime import date, timedelta

import pytest

from market import trends


def series(values, start=date(2026, 1, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def flat(n, value=100.0):
    return series([value] * n)


# Worked example from Wilder, New Concepts in Technical Trading Systems.
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
        assert trends.rsi(flat(30)) == 50.0


class TestDrawdown:
    def test_reports_the_fall_from_a_peak(self):
        s = series([100 + i for i in range(170)] + [270 - i for i in range(30)])
        d = trends.drawdown(s)
        assert d["peak"] == pytest.approx(270.0)
        assert d["pct"] == pytest.approx((241.0 / 270.0 - 1) * 100)

    def test_dates_the_peak_and_counts_the_days_since(self):
        s = series([100 + i for i in range(160)] + [259.0] * 40)
        d = trends.drawdown(s)
        assert d["peak_on"] == date(2026, 1, 1) + timedelta(days=159)
        assert d["days_since_peak"] == 40

    def test_a_series_at_its_peak_shows_no_drawdown(self):
        assert trends.drawdown(series([100 + i for i in range(200)]))["pct"] == 0

    def test_too_short_a_series_has_no_drawdown(self):
        assert trends.drawdown(flat(20)) is None

    def test_a_series_not_reaching_back_six_months_has_no_drawdown(self):
        assert trends.drawdown(flat(150)) is None

    def test_only_the_window_is_searched_for_the_peak(self):
        s = series([500.0] + [100.0 + i for i in range(220)])
        assert trends.drawdown(s)["peak"] < 500.0

    def test_a_worthless_series_is_not_a_drawdown(self):
        assert trends.drawdown(flat(200, 0.0)) is None


class TestMovingAverage:
    def test_averages_the_window(self):
        assert trends.moving_average(series([1.0] * 40 + [2.0] * 50), 50) \
            == pytest.approx(2.0)

    def test_reports_position_relative_to_the_average(self):
        s = series([100.0] * 60 + [110.0])
        assert trends.relative_to_average(s, 50) > 0

    def test_an_unsupported_window_is_absent_not_approximated(self):
        assert trends.moving_average(flat(30), 200) is None
        assert trends.relative_to_average(flat(30), 200) is None

    def test_a_session_window_has_no_tolerance(self):
        assert trends.moving_average(flat(199), 200) is None
        assert trends.moving_average(flat(200), 200) is not None


class TestRealizedVol:
    def test_a_flat_series_has_zero_volatility(self):
        assert trends.realized_vol(flat(21)) == pytest.approx(0.0)

    def test_too_short_a_series_has_no_volatility(self):
        assert trends.realized_vol(series([100.0] * 20)) is None

    def test_exactly_enough_is_enough(self):
        assert trends.realized_vol(flat(21)) is not None

    def test_a_wider_swing_reports_more_volatility(self):
        calm = series([100 + (1 if i % 2 else -1) for i in range(21)])
        wild = series([100 + (10 if i % 2 else -10) for i in range(21)])
        assert trends.realized_vol(wild) > trends.realized_vol(calm)

    def test_it_is_never_negative(self):
        s = series([100 + (3 if i % 2 else -3) for i in range(21)])
        assert trends.realized_vol(s) >= 0

    def test_reported_on_the_annualised_scale_daily_swings_would_understate(self):
        calm = series([100 + (1 if i % 2 else -1) for i in range(21)])
        assert trends.realized_vol(calm) > 5

    def test_a_zero_close_is_absent_not_a_shortened_window(self):
        values = [100.0] * 10 + [0.0] + [100.0] * 10
        assert trends.realized_vol(series(values)) is None

    def test_a_zero_close_at_the_very_end_is_still_absent(self):
        values = [100.0] * 20 + [0.0]
        assert trends.realized_vol(series(values)) is None


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
        s = series([100 + i for i in range(130)])
        assert trends.describe(list(reversed(s)))["last"] == \
            trends.describe(s)["last"]


class TestCalendarWindow:
    """The six-month drawdown window in calendar months."""

    def test_six_months_back_lands_on_the_same_day_of_month(self):
        assert trends._minus_months(date(2026, 9, 18), 6) == date(2026, 3, 18)

    def test_the_averaged_day_count_would_have_missed_it(self):
        assert trends._minus_months(date(2026, 9, 18), 6) \
            != date(2026, 9, 18) - timedelta(days=183)

    def test_crossing_a_year_boundary(self):
        assert trends._minus_months(date(2026, 2, 10), 6) == date(2025, 8, 10)

    def test_a_day_absent_from_the_earlier_month_clamps(self):
        assert trends._minus_months(date(2026, 8, 31), 6) == date(2026, 2, 28)

    def test_a_leap_february_clamps_to_the_29th(self):
        assert trends._minus_months(date(2024, 8, 31), 6) == date(2024, 2, 29)

    def test_a_peak_on_the_boundary_is_inside_the_window(self):
        start, end = date(2026, 3, 18), date(2026, 9, 18)
        days = (end - start).days
        s = [(start + timedelta(days=i),
              200.0 if i == 0 else 100.0) for i in range(days + 1)]
        assert trends.drawdown(s)["peak"] == 200.0


class TestLiveQuote:
    """A live price is measured against, not counted in, a window."""

    def test_it_is_not_counted_in_the_average_window(self):
        assert trends.describe(flat(199), live=150.0)["vs_ma200"] is None

    def test_it_does_not_displace_a_settled_close(self):
        s = series([100.0] * 199 + [200.0])
        m = trends.describe(s, live=100.0)
        # The average is of the 200 stored closes, one of which is 200.
        assert m["vs_ma200"] == pytest.approx((100.0 / 100.5 - 1) * 100)

    def test_it_is_what_the_average_is_compared_against(self):
        s = flat(200)
        assert trends.describe(s, live=110.0)["vs_ma200"] == pytest.approx(10.0)

    def test_it_can_itself_be_the_peak(self):
        s = series([100.0 + i for i in range(220)])
        assert trends.describe(s, live=999.0)["drawdown"]["pct"] == 0

    def test_it_is_the_reported_last_price(self):
        assert trends.describe(flat(200), live=150.0)["last"] == 150.0

    def test_the_session_count_is_of_stored_closes(self):
        assert trends.describe(flat(200), live=150.0)["sessions"] == 200

    def test_rsi_measures_the_change_to_the_live_price(self):
        rising = series([100.0 + i for i in range(30)])
        assert trends.describe(rising, live=50.0)["rsi"] < \
            trends.describe(rising)["rsi"]

    def test_the_age_is_measured_to_the_live_quote(self):
        s = series([100.0 + i for i in range(200)])        # peak on the last
        peak_day = date(2026, 1, 1) + timedelta(days=199)
        m = trends.describe(s, live=150.0, live_on=peak_day + timedelta(days=3))
        assert m["drawdown"]["days_since_peak"] == 3

    def test_without_a_live_quote_the_age_runs_to_the_last_close(self):
        s = series([100.0 + i for i in range(180)] + [279.0] * 20)
        assert trends.describe(s)["drawdown"]["days_since_peak"] == 20

    def test_the_window_ends_at_the_live_quote(self):
        s = series([100.0 + i for i in range(200)])
        last = date(2026, 1, 1) + timedelta(days=199)
        assert trends.describe(s, live=150.0,
                               live_on=last + timedelta(days=200)) is not None
