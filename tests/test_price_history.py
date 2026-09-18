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
