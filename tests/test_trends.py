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
        s = series([100 + i for i in range(100)] + [200 - i for i in range(30)])
        d = trends.drawdown(s)
        assert d["peak"] == pytest.approx(200.0)
        assert d["pct"] == pytest.approx((171.0 / 200.0 - 1) * 100)

    def test_dates_the_peak_and_counts_the_days_since(self):
        """The plateau case: a price that stopped rising some time ago. How
        long ago is the part a table of current values cannot show."""
        s = series([100 + i for i in range(90)] + [189.0] * 40)
        d = trends.drawdown(s)
        assert d["peak_on"] == date(2026, 1, 1) + timedelta(days=89)
        assert d["days_since_peak"] == 40

    def test_a_series_at_its_peak_shows_no_drawdown(self):
        assert trends.drawdown(series([100 + i for i in range(130)]))["pct"] == 0

    def test_too_short_a_series_has_no_drawdown(self):
        assert trends.drawdown(flat(20)) is None

    def test_a_worthless_series_is_not_a_drawdown(self):
        """Dividing by a peak of zero is not a 100% fall, it is no answer."""
        assert trends.drawdown(flat(130, 0.0)) is None


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

    def test_a_window_within_tolerance_is_allowed(self):
        """Venue holidays mean a series need not be gapless, so a little
        slack is allowed - documented, and nothing like 30 for 200."""
        assert trends.moving_average(flat(170), 200) is not None


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
