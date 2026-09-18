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
        seeded, recorded = price_history.update(
            {"SAP.DE": 102.0, "ALV.DE": 300.0},
            fetch=fetch_ok, on=date(2026, 9, 18))
        assert (seeded, recorded) == (2, 2)

    def test_a_position_without_a_price_is_skipped(self):
        """The provider can fail one ticker. That is not a close of zero."""
        seeded, recorded = price_history.update(
            {"SAP.DE": None, "ALV.DE": 300.0}, fetch=fetch_ok,
            on=date(2026, 9, 18))
        assert recorded == 1
        assert price_history.load("SAP.DE") == {}

    def test_an_unpriceable_listing_does_not_block_the_rest(self):
        def fetch_one(ticker):
            return fetch_ok(ticker) if ticker == "ALV.DE" else {}

        seeded, recorded = price_history.update(
            {"NEW.DE": 10.0, "ALV.DE": 300.0}, fetch=fetch_one,
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
