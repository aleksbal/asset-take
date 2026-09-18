"""Price series seeded from the provider and extended by our own runs.

The series is per listing, never per account: it records what a listing closed
at, so backfilling it carries no assumption about what was held when.
"""
from datetime import date

import pytest

import paths
import price_history


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PRICES", tmp_path / "prices")
    return tmp_path / "prices"


def fetch_ok(ticker):
    return {"2026-09-16": 100.0, "2026-09-17": 101.0}


def fetch_none(ticker):
    return {}


class TestSeeding:
    def test_seeds_a_listing_we_hold_nothing_for(self):
        assert price_history.backfill("SAP.DE", fetch=fetch_ok) == 2
        assert set(price_history.load("SAP.DE")) == {"2026-09-16", "2026-09-17"}

    def test_a_seeded_row_is_marked_as_the_providers(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)
        assert price_history.load("SAP.DE")["2026-09-16"][1] == price_history.YAHOO

    def test_a_listing_we_already_hold_is_left_alone(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)

        def fetch_different(ticker):
            raise AssertionError("must not re-fetch an existing series")

        assert price_history.backfill("SAP.DE", fetch=fetch_different) == 0

    def test_an_unavailable_history_leaves_no_trace(self, store):
        """No marker, so a listing too new to have history today is simply
        retried later rather than being written off for good."""
        assert price_history.backfill("NEW.DE", fetch=fetch_none) == 0
        assert not price_history.path_for("NEW.DE").exists()

    def test_an_unavailable_history_is_retried(self):
        price_history.backfill("NEW.DE", fetch=fetch_none)
        assert price_history.backfill("NEW.DE", fetch=fetch_ok) == 2


class TestRecording:
    def test_records_todays_close(self):
        assert price_history.record("SAP.DE", 205.5, on=date(2026, 9, 18))
        assert price_history.load("SAP.DE")["2026-09-18"] == (205.5,
                                                              price_history.LOCAL)

    def test_a_second_run_on_one_day_does_not_duplicate(self):
        price_history.record("SAP.DE", 205.5, on=date(2026, 9, 18))
        assert not price_history.record("SAP.DE", 999.0, on=date(2026, 9, 18))
        series = price_history.load("SAP.DE")
        assert len(series) == 1 and series["2026-09-18"][0] == 205.5

    def test_recording_preserves_a_seeded_day(self):
        """A day the provider supplied keeps its provenance. Overwriting it as
        local would lose that it had been adjusted for splits and dividends."""
        price_history.backfill("SAP.DE", fetch=fetch_ok)
        price_history.record("SAP.DE", 555.0, on=date(2026, 9, 17))
        assert price_history.load("SAP.DE")["2026-09-17"] == (101.0,
                                                              price_history.YAHOO)

    def test_recording_extends_a_seeded_series(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)
        price_history.record("SAP.DE", 102.0, on=date(2026, 9, 18))
        series = price_history.load("SAP.DE")
        assert len(series) == 3
        assert series["2026-09-18"] == (102.0, price_history.LOCAL)


class TestDailyUpdate:
    def test_seeds_and_records_in_one_pass(self):
        seeded, recorded, rescaled = price_history.update(
            {"SAP.DE": 102.0, "ALV.DE": 103.0},
            fetch=fetch_ok, on=date(2026, 9, 18))
        assert (seeded, recorded, rescaled) == (2, 2, 0)

    def test_a_position_without_a_price_is_skipped(self):
        """The provider can fail one ticker. That is not a close of zero."""
        seeded, recorded, _ = price_history.update(
            {"SAP.DE": None, "ALV.DE": 103.0}, fetch=fetch_ok,
            on=date(2026, 9, 18))
        assert recorded == 1
        assert price_history.load("SAP.DE") == {}

    def test_an_unpriceable_listing_does_not_block_the_rest(self):
        def fetch_one(ticker):
            return fetch_ok(ticker) if ticker == "ALV.DE" else {}

        seeded, recorded, _ = price_history.update(
            {"NEW.DE": 10.0, "ALV.DE": 103.0}, fetch=fetch_one,
            on=date(2026, 9, 18))
        assert seeded == 1 and recorded == 2   # NEW.DE still records today


class TestStoredFormat:
    def test_series_is_written_in_date_order(self):
        price_history.record("SAP.DE", 3.0, on=date(2026, 9, 18))
        price_history.record("SAP.DE", 1.0, on=date(2026, 9, 16))
        price_history.record("SAP.DE", 2.0, on=date(2026, 9, 17))
        rows = price_history.path_for("SAP.DE").read_text().splitlines()
        assert [r.split(",")[0] for r in rows[1:]] == ["2026-09-16",
                                                       "2026-09-17", "2026-09-18"]

    def test_a_corrupt_row_does_not_poison_the_series(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)
        p = price_history.path_for("SAP.DE")
        p.write_text(p.read_text() + "2026-09-18,,local\n")
        assert len(price_history.load("SAP.DE")) == 2


class TestCorporateActions:
    """A split rewrites the provider's history retroactively while ours stays
    as observed, so closes recorded before it sit on the old scale and closes
    after it on the new. Provenance cannot repair that - both sides are ours
    and no ratio is stored - so the series has to be re-seeded."""

    def test_a_split_sized_drop_re_seeds_the_series(self):
        price_history.record("SAP.DE", 400.0, on=date(2026, 9, 17))

        def post_split(ticker):
            return {"2026-09-16": 99.0, "2026-09-17": 100.0}

        seeded, recorded, rescaled = price_history.update(
            {"SAP.DE": 100.0}, fetch=post_split, on=date(2026, 9, 18))
        assert rescaled == 1
        series = price_history.load("SAP.DE")
        assert series["2026-09-17"] == (100.0, price_history.YAHOO)  # rescaled

    def test_an_ordinary_move_does_not_re_seed(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)

        def must_not_fetch(ticker):
            raise AssertionError("an ordinary day must not trigger a re-seed")

        _, _, rescaled = price_history.update(
            {"SAP.DE": 104.0}, fetch=must_not_fetch, on=date(2026, 9, 18))
        assert rescaled == 0

    def test_a_re_seed_keeps_what_the_provider_does_not_cover(self):
        price_history.record("SAP.DE", 400.0, on=date(2026, 9, 17))
        price_history.record("SAP.DE", 401.0, on=date(2026, 9, 20))
        price_history.refresh("SAP.DE", fetch=fetch_ok)
        series = price_history.load("SAP.DE")
        assert series["2026-09-20"] == (401.0, price_history.LOCAL)
        assert series["2026-09-17"] == (101.0, price_history.YAHOO)

    def test_a_re_seed_that_fetches_nothing_leaves_the_series_intact(self):
        price_history.backfill("SAP.DE", fetch=fetch_ok)
        assert price_history.refresh("SAP.DE", fetch=fetch_none) == 0
        assert len(price_history.load("SAP.DE")) == 2

    def test_a_three_for_two_split_is_detected(self):
        """Ratio 0.667. A threshold set for halvings misses it entirely and
        leaves the series on two incompatible scales."""
        price_history.record("SAP.DE", 150.0, on=date(2026, 9, 17))

        def post_split(ticker):
            return {"2026-09-17": 100.0}

        _, _, rescaled = price_history.update(
            {"SAP.DE": 100.0}, fetch=post_split, on=date(2026, 9, 18))
        assert rescaled == 1

    def test_a_reverse_three_for_two_is_detected(self):
        price_history.record("SAP.DE", 100.0, on=date(2026, 9, 17))

        def post_split(ticker):
            return {"2026-09-17": 150.0}

        _, _, rescaled = price_history.update(
            {"SAP.DE": 150.0}, fetch=post_split, on=date(2026, 9, 18))
        assert rescaled == 1


class TestRefreshWindow:
    """The provider serves a fixed window. Once a series outlives it, a
    refresh that keeps only the fetched range deletes every older row - and
    does so again on every subsequent refresh."""

    def test_rows_older_than_the_window_survive(self):
        for day, close in [(1, 50.0), (2, 51.0), (16, 400.0), (17, 404.0)]:
            price_history.record("SAP.DE", close, on=date(2026, 9, day))

        def window(ticker):          # provider covers the 16th onward only
            return {"2026-09-16": 100.0, "2026-09-17": 101.0}

        price_history.refresh("SAP.DE", fetch=window)
        series = price_history.load("SAP.DE")
        assert "2026-09-01" in series and "2026-09-02" in series

    def test_older_rows_are_put_on_the_providers_scale(self):
        """They predate the rescaling. The overlapping day gives the ratio."""
        price_history.record("SAP.DE", 200.0, on=date(2026, 9, 1))
        price_history.record("SAP.DE", 400.0, on=date(2026, 9, 16))

        def window(ticker):          # a 4:1 split: 400 became 100
            return {"2026-09-16": 100.0}

        price_history.refresh("SAP.DE", fetch=window)
        assert price_history.load("SAP.DE")["2026-09-01"][0] == pytest.approx(50.0)

    def test_repeated_refreshes_do_not_erode_the_series(self):
        for day in range(1, 5):
            price_history.record("SAP.DE", 100.0, on=date(2026, 9, day))

        def window(ticker):
            return {"2026-09-04": 100.0}

        for _ in range(3):
            price_history.refresh("SAP.DE", fetch=window)
        assert len(price_history.load("SAP.DE")) == 4


class TestProviderHistoryUnits:
    """The provider serves history in the venue's quote unit while the daily
    close arrives already converted. Storing them unscaled mixes pence with
    pounds in one series, and the hundredfold step then reads as a corporate
    action that re-seeds back to the raw values on every run."""

    @pytest.fixture
    def pence(self, monkeypatch):
        import pandas as pd

        class Handle:
            fast_info = {"currency": "GBp"}

            def history(self, **kw):
                idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
                return pd.DataFrame({"Close": [4200.0, 4208.0]}, index=idx)

        monkeypatch.setattr(price_history.yf, "Ticker", lambda t: Handle())

    def test_downloaded_history_is_stored_in_the_major_unit(self, pence):
        assert price_history._fetch("BATS.L") == {"2026-09-16": 42.0,
                                                  "2026-09-17": 42.08}

    def test_a_seeded_series_agrees_with_the_daily_close(self, pence):
        """Both sides of the handover must be on one scale, or the next run
        reads the step as a split."""
        price_history.backfill("BATS.L")
        _, _, rescaled = price_history.update({"BATS.L": 42.10},
                                              on=date(2026, 9, 18))
        assert rescaled == 0
        series = price_history.load("BATS.L")
        assert max(series.values(), key=lambda v: v[0])[0] < 100


class TestSettledSession:
    """A close belongs to the session it settled in, not to the day of the
    run. A weekend or pre-close run otherwise files it under a day the market
    never traded, and the same-day guard prevents a later run correcting it."""

    def test_a_close_is_filed_under_its_own_session(self):
        price_history.update({"SAP.DE": 102.0}, dates={"SAP.DE": "2026-09-17"},
                             fetch=fetch_none, on=date(2026, 9, 19))
        assert set(price_history.load("SAP.DE")) == {"2026-09-17"}

    def test_a_weekend_run_does_not_invent_a_trading_day(self):
        price_history.update({"SAP.DE": 102.0}, dates={"SAP.DE": "2026-09-18"},
                             fetch=fetch_none, on=date(2026, 9, 19))
        price_history.update({"SAP.DE": 102.0}, dates={"SAP.DE": "2026-09-18"},
                             fetch=fetch_none, on=date(2026, 9, 20))
        assert set(price_history.load("SAP.DE")) == {"2026-09-18"}

    def test_the_run_date_is_used_when_no_session_is_given(self):
        price_history.update({"SAP.DE": 102.0}, fetch=fetch_none,
                             on=date(2026, 9, 19))
        assert set(price_history.load("SAP.DE")) == {"2026-09-19"}

    def test_record_accepts_a_session_string(self):
        price_history.record("SAP.DE", 102.0, on="2026-09-17")
        assert set(price_history.load("SAP.DE")) == {"2026-09-17"}


class TestUnknownQuoteUnit:
    """History in an unknown unit is not history. Stored unscaled it would sit
    beside converted daily closes and read as a corporate action, re-seeding
    back to the same raw values on every run."""

    @pytest.fixture
    def blind(self, monkeypatch):
        import pandas as pd

        class Handle:
            fast_info = {}          # the unit lookup fails

            def history(self, **kw):
                idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
                return pd.DataFrame({"Close": [4200.0, 4208.0]}, index=idx)

        monkeypatch.setattr(price_history.yf, "Ticker", lambda t: Handle())

    def test_no_history_is_returned_when_the_unit_is_unknown(self, blind):
        assert price_history._fetch("BATS.L") == {}

    def test_nothing_is_stored_so_the_next_run_retries(self, blind):
        assert price_history.backfill("BATS.L") == 0
        assert not price_history.path_for("BATS.L").exists()

    def test_a_known_unit_is_used_instead_of_asking(self, blind):
        """positions.csv already records it, so the fetch need not succeed."""
        assert price_history._fetch("BATS.L", unit="GBp") == {
            "2026-09-16": 42.0, "2026-09-17": 42.08}

    def test_the_unit_reaches_the_fetch_through_update(self, blind):
        price_history.update({"BATS.L": 42.10}, dates={"BATS.L": "2026-09-18"},
                             units={"BATS.L": "GBp"})
        series = price_history.load("BATS.L")
        assert series["2026-09-17"][0] == 42.08


class TestUnsettledSession:
    """A bar dated today may still be in progress, and `record` refuses to
    overwrite a date - so an intraday value written as a close stays wrong
    for good. The series lags a session instead."""

    def test_a_ticker_with_no_settled_session_is_not_recorded(self):
        seeded, recorded, _ = price_history.update(
            {"SAP.DE": 102.0}, dates={}, fetch=fetch_none)
        assert recorded == 0
        assert price_history.load("SAP.DE") == {}

    def test_seeding_still_happens_for_an_unsettled_ticker(self):
        seeded, recorded, _ = price_history.update(
            {"SAP.DE": 102.0}, dates={}, fetch=fetch_ok)
        assert seeded == 1 and recorded == 0
        assert len(price_history.load("SAP.DE")) == 2

    def test_a_settled_ticker_alongside_an_unsettled_one_is_recorded(self):
        _, recorded, _ = price_history.update(
            {"SAP.DE": 102.0, "ALV.DE": 103.0},
            dates={"ALV.DE": "2026-09-17"}, fetch=fetch_none)
        assert recorded == 1
        assert set(price_history.load("ALV.DE")) == {"2026-09-17"}
