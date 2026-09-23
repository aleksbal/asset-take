"""Per-listing daily volume series, seeded from the provider and extended by
our own runs - the volume counterpart of `market.prices`, minus what a share
count does not need: no currency, so no Quote, and no rescale/refresh, since
a split changing the share count is real rather than a scale error.
"""
from datetime import date

import pytest

import paths
from market import volume


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "VOLUMES", tmp_path / "volumes")
    return tmp_path / "volumes"


def fetch_ok(ticker):
    return {"2026-09-16": 1_000_000.0, "2026-09-17": 1_200_000.0}


def fetch_none(ticker):
    return {}


class TestSeeding:
    def test_seeds_a_listing_we_hold_nothing_for(self):
        assert volume.backfill("SAP.DE", fetch=fetch_ok) == 2
        assert set(volume.load("SAP.DE")) == {"2026-09-16", "2026-09-17"}

    def test_a_listing_we_already_hold_is_left_alone(self):
        volume.backfill("SAP.DE", fetch=fetch_ok)

        def fetch_different(ticker):
            raise AssertionError("must not re-fetch an existing series")

        assert volume.backfill("SAP.DE", fetch=fetch_different) == 0

    def test_an_unavailable_history_leaves_no_trace(self, store):
        assert volume.backfill("NEW.DE", fetch=fetch_none) == 0
        assert not volume.path_for("NEW.DE").exists()

    def test_an_unavailable_history_is_retried(self):
        volume.backfill("NEW.DE", fetch=fetch_none)
        assert volume.backfill("NEW.DE", fetch=fetch_ok) == 2


class TestRecording:
    def test_records_todays_volume(self):
        assert volume.record("SAP.DE", 500_000.0, on=date(2026, 9, 18))
        assert volume.load("SAP.DE")["2026-09-18"] == 500_000.0

    def test_a_second_run_on_one_day_does_not_duplicate(self):
        volume.record("SAP.DE", 500_000.0, on=date(2026, 9, 18))
        assert not volume.record("SAP.DE", 999.0, on=date(2026, 9, 18))
        series = volume.load("SAP.DE")
        assert len(series) == 1 and series["2026-09-18"] == 500_000.0

    def test_a_missing_volume_is_not_recorded_as_zero(self):
        """The provider can fail to report volume for one ticker - that is
        not a session with no trading."""
        assert not volume.record("SAP.DE", None, on=date(2026, 9, 18))
        assert volume.load("SAP.DE") == {}


class TestDailyUpdate:
    def test_seeds_and_records_in_one_pass(self):
        seeded, recorded = volume.update(
            {"SAP.DE": 500_000.0, "ALV.DE": 600_000.0},
            fetch=fetch_ok, on=date(2026, 9, 18))
        assert (seeded, recorded) == (2, 2)

    def test_a_position_without_a_volume_is_skipped(self):
        seeded, recorded = volume.update(
            {"SAP.DE": None, "ALV.DE": 600_000.0}, fetch=fetch_ok,
            on=date(2026, 9, 18))
        assert recorded == 1
        assert volume.load("SAP.DE") == {}

    def test_an_unpriceable_listing_does_not_block_the_rest(self):
        def fetch_one(ticker):
            return fetch_ok(ticker) if ticker == "ALV.DE" else {}

        seeded, recorded = volume.update(
            {"NEW.DE": 10.0, "ALV.DE": 600_000.0}, fetch=fetch_one,
            on=date(2026, 9, 18))
        assert seeded == 1 and recorded == 2


class TestStoredFormat:
    def test_series_is_written_in_date_order(self):
        volume.record("SAP.DE", 3.0, on=date(2026, 9, 18))
        volume.record("SAP.DE", 1.0, on=date(2026, 9, 16))
        volume.record("SAP.DE", 2.0, on=date(2026, 9, 17))
        rows = volume.path_for("SAP.DE").read_text().splitlines()
        assert [r.split(",")[0] for r in rows[1:]] == ["2026-09-16",
                                                       "2026-09-17", "2026-09-18"]

    def test_a_corrupt_row_does_not_poison_the_series(self):
        volume.backfill("SAP.DE", fetch=fetch_ok)
        p = volume.path_for("SAP.DE")
        p.write_text(p.read_text() + "not-a-date,not-a-number\n")
        assert len(volume.load("SAP.DE")) == 2
