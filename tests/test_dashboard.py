"""Dashboard computation over holdings that state no cost basis.

This is where the missing-cost bug surfaced: `canonical.read()` raised on the
empty field, so any import from a source without cost data killed the whole
dashboard rather than simply omitting P&L.
"""
from datetime import date

import pytest

import canonical
import dashboard
from canonical import Holding


def snapshot(**kw):
    base = dict(
        date="2026-09-18", base_currency="EUR", total_value=1000.0,
        daily_change=8.0, daily_change_pct=0.8, fx_rates={"EUR": 1.0}, alerts=[],
        positions=[{"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
                    "previous_close": 198.0, "currency": "EUR"},
                   {"ticker": "ALV.DE", "quantity": 1, "current_price": 200.0,
                    "previous_close": 198.0, "currency": "EUR"}],
    )
    base.update(kw)
    return base


def holdings(sap_cost=None, alv_cost=None):
    """The holdings the default snapshot was taken from.

    A test about missing cost data must still hold the same positions: the
    dashboard refuses to render a snapshot against a different portfolio, and
    an empty map states "no longer held", which is a different claim from
    "held, but states no cost".
    """
    return {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                              currency="EUR", avg_cost=sap_cost),
            "ALV.DE": Holding(isin="DE0008404005", name="Allianz SE", quantity=1,
                              currency="EUR", avg_cost=alv_cost)}


def test_computes_pnl_where_a_cost_basis_exists():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=150.0)}
    rows = dashboard.positions(snapshot(), by_ticker, {})
    sap = next(r for r in rows if r["ticker"] == "SAP.DE")
    assert sap["pnl"] == 4 * 200.0 - 4 * 150.0
    assert sap["pnl_pct"] is not None


def test_omits_pnl_where_none_exists_rather_than_failing():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=None)}
    rows = dashboard.positions(snapshot(), by_ticker, {})
    sap = next(r for r in rows if r["ticker"] == "SAP.DE")
    assert sap["pnl"] is None and sap["pnl_pct"] is None
    assert sap["value"] == 800.0        # still valued
    assert sap["weight"] > 0


def test_values_a_position_absent_from_holdings():
    """A snapshot may name a ticker the holdings file no longer carries."""
    rows = dashboard.positions(snapshot(), {}, {})
    assert len(rows) == 2
    assert all(r["pnl"] is None for r in rows)


def test_renders_a_table_with_mixed_cost_availability():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=150.0)}
    rows = dashboard.positions(snapshot(), by_ticker, {})
    html = dashboard.table(rows)
    assert "—" in html          # the position without a cost basis
    assert html.count('<td class="nm"') == 2  # body rows, header excluded


def test_donut_folds_beyond_seven_into_other():
    """Categorical hues are assigned in fixed order and never cycled."""
    positions = [{"ticker": f"T{i}", "quantity": 1, "current_price": 100.0,
                  "previous_close": 100.0, "currency": "EUR"} for i in range(10)]
    rows = dashboard.positions(snapshot(positions=positions, total_value=1000.0), {}, {})
    svg = dashboard.donut(rows)
    assert svg.count('class="seg"') == 8
    assert "Other (3)" in svg


class TestUnpricedPositions:
    """The provider can fail for one ticker while succeeding for the rest.
    That killed the dashboard outright: float * None."""

    def unpriced_snapshot(self):
        return snapshot(positions=[
            {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "EUR"},
            {"ticker": "BROKEN.XX", "quantity": 7, "current_price": None,
             "previous_close": None, "currency": "EUR"},
        ])

    def test_does_not_raise(self):
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        assert len(rows) == 2

    def test_marks_the_unpriced_position(self):
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        broken = next(r for r in rows if r["ticker"] == "BROKEN.XX")
        assert broken["unpriced"] is True
        assert broken["value"] is None

    def test_unpriced_is_not_valued_at_zero(self):
        """Zero would understate the total while looking complete."""
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        assert next(r for r in rows if r["ticker"] == "BROKEN.XX")["value"] != 0

    def test_sorts_unpriced_last(self):
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        assert rows[-1]["ticker"] == "BROKEN.XX"

    def test_table_renders_both(self):
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        html = dashboard.table(rows)
        assert "no price available" in html
        assert html.count('<td class="nm"') == 2

    def test_donut_excludes_unpriced(self):
        rows = dashboard.positions(self.unpriced_snapshot(), {}, {})
        assert dashboard.donut(rows).count('class="seg"') == 1


class TestUnknownPortfolioPnl:
    """An unknown total P&L must not render as a gain of zero. Every position
    in a file without a cost-basis column lands here, so the headline figure
    would otherwise read `+0 EUR` for an entire portfolio."""

    def _html(self, by_ticker, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "load",
                            lambda: ([snapshot()], by_ticker, {}))
        dashboard.main()
        return (tmp_path / "out.html").read_text(encoding="utf-8")

    def test_reports_unavailable_when_no_position_states_a_cost(
            self, tmp_path, monkeypatch):
        html = self._html(holdings(), tmp_path, monkeypatch)
        assert "no cost basis recorded" in html
        assert "+0" not in html.split("Unrealised")[1][:200]

    def test_reports_the_figure_when_a_cost_exists(self, tmp_path, monkeypatch):
        html = self._html(holdings(sap_cost=150.0), tmp_path, monkeypatch)
        assert "on cost" in html
        assert "no cost basis recorded" not in html


class TestForeignCurrencyCostBasis:
    """`avg_cost` is stated in the holding's currency. The value is converted
    to the base currency, so the cost must be too - otherwise the exchange
    rate itself is reported as a gain or loss."""

    def usd_snapshot(self):
        return snapshot(
            total_value=90.0, fx_rates={"EUR": 1.0, "USD": 0.9},
            positions=[{"ticker": "AAPL", "quantity": 1, "current_price": 100.0,
                        "previous_close": 100.0, "currency": "USD"}])

    def test_flat_position_shows_no_gain_or_loss(self):
        by_ticker = {"AAPL": Holding(isin="US0378331005", name="Apple", quantity=1,
                                     currency="USD", avg_cost=100.0)}
        row = dashboard.positions(self.usd_snapshot(), by_ticker, {})[0]
        assert row["pnl"] == 0
        assert abs(row["pnl_pct"]) < 1e-9

    def test_real_gain_survives_the_conversion(self):
        by_ticker = {"AAPL": Holding(isin="US0378331005", name="Apple", quantity=1,
                                     currency="USD", avg_cost=50.0)}
        row = dashboard.positions(self.usd_snapshot(), by_ticker, {})[0]
        assert row["pnl"] == 45.0          # a 50 USD gain, converted at 0.9
        assert round(row["pnl_pct"]) == 100


class TestMissingCostNote:
    """The note must name the actual reason. It described a currency mismatch
    the code no longer performs, sending the reader after the wrong fix."""

    def test_note_reports_an_absent_cost_basis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "load", lambda: ([snapshot()], holdings(), {}))
        dashboard.main()
        html = (tmp_path / "out.html").read_text(encoding="utf-8")
        assert "no cost basis" in html
        assert "another currency" not in html


class TestCostInAnotherCurrency:
    """A cost basis need not share the listing's currency any more.

    It used to be dropped on a mismatch, which is why the dashboard never met
    this case. The conversion it did have fell back to the *position's* rate
    where the holding's currency was absent - a USD cost converted at the EUR
    rate, reported as P&L.
    """

    def usd_listing(self):
        """A EUR cost basis against a listing quoted in USD."""
        return snapshot(
            total_value=87.0, fx_rates={"EUR": 1.0, "USD": 0.87},
            positions=[{"ticker": "GE", "quantity": 1, "current_price": 100.0,
                        "previous_close": 100.0, "currency": "USD",
                        "cost_currency": "EUR"}])

    def held(self, avg_cost=80.0):
        return {"GE": Holding(isin="US3696043013", name="GE Aerospace",
                              quantity=1, currency="EUR", avg_cost=avg_cost)}

    def test_the_cost_is_converted_on_its_own_rate(self):
        """80 EUR cost, 100 USD value = 87 EUR. A 7 EUR gain, not a 13 loss."""
        row = dashboard.positions(self.usd_listing(), self.held(), {})[0]
        assert row["pnl"] == pytest.approx(7.0)

    def test_the_position_is_still_valued_in_the_base_currency(self):
        row = dashboard.positions(self.usd_listing(), self.held(), {})[0]
        assert row["value"] == pytest.approx(87.0)

    def test_a_cost_currency_without_a_rate_yields_no_pnl(self):
        """Not a P&L computed at the position's rate, which is a different
        number wearing the right shape."""
        snap = self.usd_listing()
        snap["positions"][0]["cost_currency"] = "XXX"
        row = dashboard.positions(snap, self.held(), {})[0]
        assert row["pnl"] is None and row["value"] == pytest.approx(87.0)

    def test_an_older_snapshot_falls_back_to_the_holdings_currency(self):
        """Snapshots written before positions carried a cost currency state
        only the holding's, which is what they meant at the time."""
        snap = self.usd_listing()
        del snap["positions"][0]["cost_currency"]
        row = dashboard.positions(snap, self.held(), {})[0]
        assert row["pnl"] == pytest.approx(7.0)


class TestStaleSnapshot:
    """The dashboard values a snapshot but reads the cost basis from holdings.
    Where the two describe different portfolios, every figure still renders
    and none of them is right - so it must refuse rather than blend them.

    This is what an import without a snapshot after it produced: a position
    valued on 300 shares against the cost of 640 reported a 44% loss, and
    positions sold the day before were reported as holdings lacking cost data.
    """

    def test_agrees_where_the_snapshot_matches(self):
        assert dashboard.mismatch(snapshot(), holdings()) == ([], [], [])

    def test_reports_a_position_no_longer_held(self):
        held = holdings()
        del held["ALV.DE"]
        gone, added, moved = dashboard.mismatch(snapshot(), held)
        assert gone == ["ALV.DE"] and not added and not moved

    def test_reports_a_newly_held_position(self):
        held = holdings()
        held["RHM.DE"] = Holding(isin="DE0007030009", name="Rheinmetall AG",
                                 quantity=19, currency="EUR", avg_cost=564.28)
        gone, added, moved = dashboard.mismatch(snapshot(), held)
        assert added == ["RHM.DE"] and not gone and not moved

    def test_catches_a_quantity_change_the_tickers_hide(self):
        """Buying more of something already held leaves the ticker sets equal
        while making every derived figure for that position wrong."""
        held = holdings()
        held["SAP.DE"].quantity = 9
        gone, added, moved = dashboard.mismatch(snapshot(), held)
        assert moved == [("SAP.DE", 4, 9)] and not gone and not added

    def test_main_refuses_rather_than_rendering_a_hybrid(
            self, tmp_path, monkeypatch):
        out = tmp_path / "out.html"
        monkeypatch.setattr(dashboard, "OUT", out)
        monkeypatch.setattr(dashboard, "load",
                            lambda: ([snapshot()], {}, {}))
        with pytest.raises(SystemExit) as e:
            dashboard.main()
        assert "snapshot.py" in str(e.value)
        assert not out.exists()          # nothing written, not even a partial

    def test_the_refusal_names_what_changed(self):
        held = holdings()
        held["SAP.DE"].quantity = 9
        del held["ALV.DE"]
        msg = dashboard._stale(snapshot(), *dashboard.mismatch(snapshot(), held))
        assert "ALV.DE" in msg and "SAP.DE 4 -> 9" in msg
        assert "2026-09-18" in msg       # which snapshot is the stale one


class TestTrendColumns:
    """Trend metrics are shown as facts. A position with no series shows an
    em dash rather than a blank or a zero - absent and neutral are different
    claims, and a reader cannot tell them apart from an empty cell."""

    def trended(self, **kw):
        base = {"drawdown": {"pct": -12.4, "days_since_peak": 78,
                             "peak": 100.0, "peak_on": "2026-07-01"},
                "vs_ma50": -3.2, "vs_ma200": -8.1, "rsi": 41.0,
                "last": 87.6, "sessions": 505}
        base.update(kw)
        return base

    def test_a_drawdown_shows_the_fall_and_the_days(self):
        html = dashboard._peak_cell(self.trended())
        assert "-12.4%" in html and "78d ago" in html
        assert 'class="peak-age"' in html   # a span, never the cell itself

    def test_a_position_without_a_series_shows_a_dash(self):
        assert "—" in dashboard._peak_cell({})
        assert "—" in dashboard._ma_cell({})
        assert "—" in dashboard._rsi_cell({})

    def test_an_absent_metric_is_not_rendered_as_zero(self):
        """A missing 200-day average is not 'at its average'."""
        assert "0" not in dashboard._ma_cell({"vs_ma200": None})

    def test_a_small_fall_is_not_marked_as_a_decline(self):
        """Colour is a claim. A 2% wobble is not one."""
        assert "dn" not in dashboard._peak_cell(
            self.trended(drawdown={"pct": -2.0, "days_since_peak": 5,
                                   "peak": 100.0, "peak_on": "2026-09-13"}))

    def test_the_table_carries_the_trend_headers(self):
        rows = dashboard.positions(snapshot(), {}, {})
        html = dashboard.table(rows)
        assert "From 6m high" in html and "vs 200d" in html and "RSI" in html

    def test_an_unpriced_row_still_spans_the_full_table(self):
        """The colspan has to match the header, or the row shears sideways."""
        markup = dashboard.table([])
        header = markup.count("<th") - markup.count("<thead")   # <thead matches <th
        row = dashboard._row_html({"unpriced": True, "name": "X", "ticker": "X",
                                   "qty": 1})
        assert int(row.split('colspan="')[1].split('"')[0]) == header - 2


class TestTrendUsesTheDisplayedPrice:
    """The stored series excludes the current session, because an in-progress
    close cannot be corrected once written. The row shows that live price
    though, so a position that moved sharply today would otherwise display
    today's price beside yesterday's drawdown."""

    @pytest.fixture
    def series(self, monkeypatch):
        from datetime import date, timedelta
        start = date(2025, 1, 1)
        stored = {(start + timedelta(days=i)).isoformat(): (100.0, "yahoo")
                  for i in range(400)}
        monkeypatch.setattr(dashboard.price_history, "load",
                            lambda t: dict(stored))
        return stored

    def test_the_live_price_is_included(self, series):
        m = dashboard.trend("SAP.DE", price=150.0, on=date(2026, 2, 5))
        assert m["last"] == 150.0

    def test_it_is_not_written_back(self, series, monkeypatch):
        """Transient. Writing it would fix an intraday value as a close."""
        written = []
        monkeypatch.setattr(dashboard.price_history, "record",
                            lambda *a, **k: written.append(a))
        dashboard.trend("SAP.DE", price=150.0, on=date(2026, 2, 5))
        assert written == []

    def test_a_session_already_stored_is_not_duplicated(self, series):
        m = dashboard.trend("SAP.DE", price=999.0, on=date(2025, 6, 1))
        assert m["last"] == 100.0      # the stored close for that day wins

    def test_without_a_price_the_series_stands_alone(self, series):
        assert dashboard.trend("SAP.DE")["last"] == 100.0

    def test_no_series_means_no_metrics(self, monkeypatch):
        monkeypatch.setattr(dashboard.price_history, "load", lambda t: {})
        assert dashboard.trend("SAP.DE", price=150.0) == {}

    def test_a_stale_quote_does_not_invent_a_session(self, series):
        """Regenerating the dashboard without a fresh snapshot would append
        the last snapshot's price under a new date, fabricating a session
        across whatever gap had passed and resetting days_since_peak."""
        m = dashboard.trend("SAP.DE", price=999.0, on="2025-06-01")
        assert m["last"] == 100.0

    def test_a_quote_newer_than_the_series_is_used(self, series):
        m = dashboard.trend("SAP.DE", price=999.0, on="2026-02-05")
        assert m["last"] == 999.0

    def test_a_settled_quote_is_not_re_entered_as_live(self, series):
        """A snapshot taken on a weekend is dated later than the close it
        holds. Treating that close as a new observation advances Wilder's
        smoothing with a duplicate zero change."""
        stored_last = "2026-02-04"
        m = dashboard.trend("SAP.DE", price=100.0, on=stored_last)
        assert m["sessions"] == 400
        assert m["rsi"] == dashboard.trend("SAP.DE")["rsi"]

    def test_positions_passes_the_settled_date_not_the_snapshot_date(
            self, series, monkeypatch):
        """The wiring, not just the helper: a snapshot dated after the close
        it holds must not re-enter that close as a live observation."""
        seen = {}
        monkeypatch.setattr(dashboard, "trend",
                            lambda t, price=None, on=None: seen.update(on=on) or {})
        snap = snapshot(date="2026-02-07")          # a Saturday snapshot
        snap["positions"] = [{"ticker": "SAP.DE", "quantity": 1,
                              "current_price": 100.0, "previous_close": 100.0,
                              "currency": "EUR", "price_date": "2026-02-06"}]
        dashboard.positions(snap, {}, {})
        assert seen["on"] == "2026-02-06"

    def test_the_snapshot_date_is_used_when_the_quote_is_unsettled(
            self, series, monkeypatch):
        seen = {}
        monkeypatch.setattr(dashboard, "trend",
                            lambda t, price=None, on=None: seen.update(on=on) or {})
        snap = snapshot(date="2026-02-09")
        snap["positions"] = [{"ticker": "SAP.DE", "quantity": 1,
                              "current_price": 100.0, "previous_close": 100.0,
                              "currency": "EUR", "price_date": None}]
        dashboard.positions(snap, {}, {})
        assert seen["on"] == "2026-02-09"
