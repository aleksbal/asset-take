"""Tests for the report built by views/portfolio.py and its rendering."""
from datetime import date

import pytest

from holdings import canonical
import dashboard
from render import html as page
from market import indicators as ix
from views import portfolio
from holdings.canonical import Holding


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
    return {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                              currency="EUR", avg_cost=sap_cost),
            "ALV.DE": Holding(isin="DE0008404005", name="Allianz SE", quantity=1,
                              currency="EUR", avg_cost=alv_cost)}


def matched(sap_cost=None, alv_cost=None):
    snap = snapshot(positions=[
        {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
         "previous_close": 198.0, "currency": "EUR", "avg_cost": sap_cost},
        {"ticker": "ALV.DE", "quantity": 1, "current_price": 200.0,
         "previous_close": 198.0, "currency": "EUR", "avg_cost": alv_cost}])
    return snap, holdings(sap_cost, alv_cost)


def test_computes_pnl_where_a_cost_basis_exists():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=150.0)}
    rows = portfolio.rows(snapshot(), by_ticker, {})
    sap = next(r for r in rows if r["ticker"] == "SAP.DE")
    assert sap["position"]["pnl"] == 4 * 200.0 - 4 * 150.0
    assert sap["position"]["pnl_pct"] is not None


def test_omits_pnl_where_none_exists_rather_than_failing():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=None)}
    rows = portfolio.rows(snapshot(), by_ticker, {})
    sap = next(r for r in rows if r["ticker"] == "SAP.DE")
    assert sap["position"]["pnl"] is None and sap["position"]["pnl_pct"] is None
    assert sap["position"]["value"] == 800.0        # still valued
    assert sap["position"]["weight"] > 0


def test_values_a_position_absent_from_holdings():
    rows = portfolio.rows(snapshot(), {}, {})
    assert len(rows) == 2
    assert all(r["position"]["pnl"] is None for r in rows)


def test_renders_a_table_with_mixed_cost_availability():
    by_ticker = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                   currency="EUR", avg_cost=150.0)}
    rows = portfolio.rows(snapshot(), by_ticker, {})
    html = page.table(rows)
    assert "—" in html          # the position without a cost basis
    assert html.count('<td class="nm"') == 2  # body rows, header excluded


def test_donut_folds_beyond_seven_into_other():
    positions = [{"ticker": f"T{i}", "quantity": 1, "current_price": 100.0,
                  "previous_close": 100.0, "currency": "EUR"} for i in range(10)]
    rows = portfolio.rows(snapshot(positions=positions, total_value=1000.0), {}, {})
    svg = page.donut(rows)
    assert svg.count('class="seg"') == 8
    assert "Other (3)" in svg


class TestUnpricedPositions:
    """Positions the provider could not price."""

    def unpriced_snapshot(self):
        return snapshot(positions=[
            {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "EUR"},
            {"ticker": "BROKEN.XX", "quantity": 7, "current_price": None,
             "previous_close": None, "currency": "EUR"},
        ])

    def test_does_not_raise(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        assert len(rows) == 2

    def test_marks_the_unpriced_position(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        broken = next(r for r in rows if r["ticker"] == "BROKEN.XX")
        assert broken["priced"] is False
        assert broken["position"].get("value") is None

    def test_unpriced_is_not_valued_at_zero(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        assert next(r for r in rows if r["ticker"] == "BROKEN.XX")["position"].get("value") != 0

    def test_sorts_unpriced_last(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        assert rows[-1]["ticker"] == "BROKEN.XX"

    def test_table_renders_both(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        html = page.table(rows)
        assert "no price available" in html
        assert html.count('<td class="nm"') == 2

    def test_donut_excludes_unpriced(self):
        rows = portfolio.rows(self.unpriced_snapshot(), {}, {})
        assert page.donut(rows).count('class="seg"') == 1


class TestUnknownPortfolioPnl:
    """Total P&L when no position states a cost basis."""

    def _html(self, pair, tmp_path, monkeypatch):
        snap, by_ticker = pair
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load",
                            lambda: ([snap], by_ticker, {}))
        dashboard.main()
        return (tmp_path / "out.html").read_text(encoding="utf-8")

    def test_reports_unavailable_when_no_position_states_a_cost(
            self, tmp_path, monkeypatch):
        html = self._html(matched(), tmp_path, monkeypatch)
        assert "no cost basis recorded" in html
        assert "+0" not in html.split("Unrealised")[1][:200]

    def test_reports_the_figure_when_a_cost_exists(self, tmp_path, monkeypatch):
        html = self._html(matched(sap_cost=150.0), tmp_path, monkeypatch)
        assert "on cost" in html
        assert "no cost basis recorded" not in html


class TestForeignCurrencyCostBasis:
    """Cost bases converted to the base currency."""

    def usd_snapshot(self):
        return snapshot(
            total_value=90.0, fx_rates={"EUR": 1.0, "USD": 0.9},
            positions=[{"ticker": "AAPL", "quantity": 1, "current_price": 100.0,
                        "previous_close": 100.0, "currency": "USD"}])

    def test_flat_position_shows_no_gain_or_loss(self):
        by_ticker = {"AAPL": Holding(isin="US0378331005", name="Apple", quantity=1,
                                     currency="USD", avg_cost=100.0)}
        row = portfolio.rows(self.usd_snapshot(), by_ticker, {})[0]
        assert row["position"]["pnl"] == 0
        assert abs(row["position"]["pnl_pct"]) < 1e-9

    def test_real_gain_survives_the_conversion(self):
        by_ticker = {"AAPL": Holding(isin="US0378331005", name="Apple", quantity=1,
                                     currency="USD", avg_cost=50.0)}
        row = portfolio.rows(self.usd_snapshot(), by_ticker, {})[0]
        assert row["position"]["pnl"] == 45.0          # a 50 USD gain, converted at 0.9
        assert round(row["position"]["pnl_pct"]) == 100


class TestMissingCostNote:
    """The note explaining a missing cost basis."""

    def test_note_reports_an_absent_cost_basis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        snap, held = matched()
        monkeypatch.setattr(portfolio, "load", lambda: ([snap], held, {}))
        dashboard.main()
        html = (tmp_path / "out.html").read_text(encoding="utf-8")
        assert "no cost basis" in html
        assert "another currency" not in html


class TestCostInAnotherCurrency:
    """Cost bases in a currency other than the listing's."""

    def usd_listing(self):
        return snapshot(
            total_value=87.0, fx_rates={"EUR": 1.0, "USD": 0.87},
            positions=[{"ticker": "GE", "quantity": 1, "current_price": 100.0,
                        "previous_close": 100.0, "currency": "USD",
                        "cost_currency": "EUR"}])

    def held(self, avg_cost=80.0):
        return {"GE": Holding(isin="US3696043013", name="GE Aerospace",
                              quantity=1, currency="EUR", avg_cost=avg_cost)}

    def test_the_cost_is_converted_on_its_own_rate(self):
        row = portfolio.rows(self.usd_listing(), self.held(), {})[0]
        assert row["position"]["pnl"] == pytest.approx(7.0)

    def test_the_position_is_still_valued_in_the_base_currency(self):
        row = portfolio.rows(self.usd_listing(), self.held(), {})[0]
        assert row["position"]["value"] == pytest.approx(87.0)

    def test_a_cost_currency_without_a_rate_yields_no_pnl(self):
        snap = self.usd_listing()
        snap["positions"][0]["cost_currency"] = "XXX"
        row = portfolio.rows(snap, self.held(), {})[0]
        assert row["position"]["pnl"] is None and row["position"]["value"] == pytest.approx(87.0)

    def test_a_cost_without_a_rate_is_not_reported_as_stating_none(self):
        snap = self.usd_listing()
        snap["positions"][0]["cost_currency"] = "XXX"
        row = portfolio.rows(snap, self.held(), {})[0]
        assert row["cost_unconverted"] is True

    def test_a_position_stating_no_cost_is_not_flagged_as_unconvertible(self):
        row = portfolio.rows(self.usd_listing(), self.held(avg_cost=None),
                                  {})[0]
        assert row["position"]["pnl"] is None and row["cost_unconverted"] is False


class TestLegacySnapshotCostCurrency:
    """Snapshots written before positions carried a cost currency."""

    def legacy(self, listing="USD"):
        snap = snapshot(
            total_value=87.0, fx_rates={"EUR": 1.0, "USD": 0.87},
            positions=[{"ticker": "GE", "quantity": 1, "current_price": 100.0,
                        "previous_close": 100.0, "currency": listing,
                        "avg_cost": 80.0}])
        return snap                      # no cost_currency key, as of that era

    def held(self, currency="USD"):
        return {"GE": Holding(isin="US3696043013", name="GE Aerospace",
                              quantity=1, currency=currency, avg_cost=80.0)}

    def test_the_cost_is_read_in_the_snapshots_own_currency(self):
        row = portfolio.rows(self.legacy(), self.held(), {})[0]
        assert row["position"]["pnl"] == pytest.approx(17.4)

    def test_a_later_holding_currency_change_does_not_reinterpret_it(self):
        row = portfolio.rows(self.legacy(), self.held("EUR"), {})[0]
        assert row["position"]["pnl"] == pytest.approx(17.4)      # not 87 - 80 = 7

    def test_a_stated_cost_currency_still_wins(self):
        snap = self.legacy()
        snap["positions"][0]["cost_currency"] = "EUR"
        row = portfolio.rows(snap, self.held("EUR"), {})[0]
        assert row["position"]["pnl"] == pytest.approx(7.0)


class TestBaseCurrencyLabels:
    """Labels use the configured base currency."""

    def _html(self, base, tmp_path, monkeypatch):
        snap, held = matched(sap_cost=150.0)
        snap["base_currency"] = base
        snap["fx_rates"] = {base: 1.0, "EUR": 1.0}
        for p in snap["positions"]:
            p["currency"] = base
        for h in held.values():
            h.currency = base
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load", lambda: ([snap], held, {}))
        dashboard.main()
        return (tmp_path / "out.html").read_text(encoding="utf-8")

    def test_the_total_is_labelled_in_the_snapshots_currency(
            self, tmp_path, monkeypatch):
        html = self._html("USD", tmp_path, monkeypatch)
        assert "> USD</span>" in html

    def test_no_eur_label_survives_a_usd_snapshot(self, tmp_path, monkeypatch):
        html = self._html("USD", tmp_path, monkeypatch)
        assert "EUR" not in html

    def test_a_eur_snapshot_still_says_eur(self, tmp_path, monkeypatch):
        assert "> EUR</span>" in self._html("EUR", tmp_path, monkeypatch)


class TestMixedBaseCurrencies:
    """Value chart across a change of base currency."""

    def snap(self, day, base, total):
        s = snapshot(date=day, base_currency=base, total_value=total)
        s["fx_rates"] = {base: 1.0}
        return s

    def test_a_single_base_keeps_every_snapshot(self):
        snaps = [self.snap("2026-09-17", "EUR", 100.0),
                 self.snap("2026-09-18", "EUR", 110.0)]
        assert portfolio.comparable(snaps) == snaps

    def test_earlier_snapshots_in_another_base_are_dropped(self):
        snaps = [self.snap("2026-09-17", "EUR", 100.0),
                 self.snap("2026-09-18", "USD", 118.0)]
        kept = portfolio.comparable(snaps)
        assert [s["date"] for s in kept] == ["2026-09-18"]

    def test_a_base_that_changed_and_changed_back_is_not_spliced(self):
        snaps = [self.snap("2026-09-16", "EUR", 100.0),
                 self.snap("2026-09-17", "USD", 118.0),
                 self.snap("2026-09-18", "EUR", 102.0)]
        assert [s["date"] for s in portfolio.comparable(snaps)] == ["2026-09-18"]

    def test_a_snapshot_silent_on_its_base_is_read_as_eur(self):
        old = snapshot(date="2026-09-17", total_value=100.0)
        del old["base_currency"]
        snaps = [old, self.snap("2026-09-18", "EUR", 110.0)]
        assert len(portfolio.comparable(snaps)) == 2

    def test_the_dashboard_says_what_it_left_out(self, tmp_path, monkeypatch):
        snap, held = matched(sap_cost=150.0)
        older = snapshot(date="2026-09-17", base_currency="USD", total_value=99.0)
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load", lambda: ([older, snap], held, {}))
        dashboard.main()
        html = (tmp_path / "out.html").read_text(encoding="utf-8")
        assert "denominated in another currency" in html


class TestStaleSnapshot:
    """Refusing a snapshot that does not match the holdings on file."""

    def test_agrees_where_the_snapshot_matches(self):
        assert portfolio.mismatch(snapshot(), holdings()) == ([], [], [])

    def test_reports_a_position_no_longer_held(self):
        held = holdings()
        del held["ALV.DE"]
        gone, added, changed = portfolio.mismatch(snapshot(), held)
        assert gone == ["ALV.DE"] and not added and not changed

    def test_reports_a_newly_held_position(self):
        held = holdings()
        held["RHM.DE"] = Holding(isin="DE0007030009", name="Rheinmetall AG",
                                 quantity=19, currency="EUR", avg_cost=564.28)
        gone, added, changed = portfolio.mismatch(snapshot(), held)
        assert added == ["RHM.DE"] and not gone and not changed

    def test_catches_a_quantity_change_the_tickers_hide(self):
        held = holdings()
        held["SAP.DE"].quantity = 9
        gone, added, changed = portfolio.mismatch(snapshot(), held)
        assert changed == ["SAP.DE quantity 4 -> 9"] and not gone and not added

    def test_catches_a_cost_change_the_quantities_hide(self):
        snap = snapshot(positions=[
            {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "EUR", "avg_cost": 150.0}])
        held = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                  currency="EUR", avg_cost=175.0)}
        gone, added, changed = portfolio.mismatch(snap, held)
        assert changed == ["SAP.DE cost 150 -> 175"] and not gone and not added

    def test_catches_a_cost_basis_that_appeared(self):
        snap = snapshot(positions=[
            {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "EUR", "avg_cost": None}])
        held = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                  currency="EUR", avg_cost=150.0)}
        assert portfolio.mismatch(snap, held)[2] == ["SAP.DE cost — -> 150"]

    def test_catches_a_cost_currency_change(self):
        snap = snapshot(positions=[
            {"ticker": "GE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "USD", "avg_cost": 150.0,
             "cost_currency": "USD"}])
        held = {"GE": Holding(isin="US3696043013", name="GE Aerospace", quantity=4,
                              currency="EUR", avg_cost=150.0)}
        assert portfolio.mismatch(snap, held)[2] == ["GE cost currency USD -> EUR"]

    def test_a_snapshot_silent_on_cost_currency_is_not_a_change(self):
        snap = snapshot(positions=[
            {"ticker": "SAP.DE", "quantity": 4, "current_price": 200.0,
             "previous_close": 198.0, "currency": "EUR", "avg_cost": 150.0}])
        held = {"SAP.DE": Holding(isin="DE0007164600", name="SAP SE", quantity=4,
                                  currency="EUR", avg_cost=150.0)}
        assert portfolio.mismatch(snap, held) == ([], [], [])

    def test_main_refuses_rather_than_rendering_a_hybrid(
            self, tmp_path, monkeypatch):
        out = tmp_path / "out.html"
        monkeypatch.setattr(dashboard, "OUT", out)
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load",
                            lambda: ([snapshot()], {}, {}))
        with pytest.raises(SystemExit) as e:
            dashboard.main()
        assert "snapshot.py" in str(e.value)
        assert not out.exists()          # nothing written, not even a partial

    def test_the_refusal_names_what_changed(self):
        held = holdings()
        held["SAP.DE"].quantity = 9
        del held["ALV.DE"]
        msg = portfolio._stale(snapshot(), *portfolio.mismatch(snapshot(), held))
        assert "ALV.DE" in msg and "SAP.DE quantity 4 -> 9" in msg
        assert "2026-09-18" in msg       # which snapshot is the stale one


class TestTrendColumns:
    """Parameter columns, including absent values."""

    def test_a_fall_shows_its_size(self):
        assert "-12.4%" in page.cell(-12.4, "fall")

    def test_the_age_of_a_high_is_shown_in_days(self):
        assert "78d" in page.cell(78, "days")

    def test_a_parameter_without_a_series_shows_a_dash(self):
        for unit in ("fall", "days", "percent", "index"):
            assert "\u2014" in page.cell(None, unit)

    def test_an_absent_metric_is_not_rendered_as_zero(self):
        assert "0" not in page.cell(None, "percent")

    def test_a_small_fall_is_not_marked_as_a_decline(self):
        assert "dn" not in page.cell(-2.0, "fall")

    def test_a_large_fall_is(self):
        assert "dn" in page.cell(-12.4, "fall")

    def test_a_fall_is_never_coloured_green(self):
        assert "up" not in page.cell(-0.1, "fall")

    def test_an_index_is_left_neutral(self):
        cell = page.cell(82.0, "index")
        assert "up" not in cell and "dn" not in cell

    def test_a_value_rounding_to_zero_carries_no_sign(self):
        for unit in ("percent", "fall", "volume"):
            assert page.cell(-0.02, unit) == '<td class="n">0.0%</td>'
            assert page.cell(0.0, unit) == '<td class="n">0.0%</td>'

    def test_volatility_is_a_magnitude_never_signed_or_coloured(self):
        cell = page.cell(23.4, "vol")
        assert "23.4%" in cell and "+" not in cell
        assert "up" not in cell and "dn" not in cell

    def test_volume_trend_is_signed_but_uncoloured(self):
        cell = page.cell(45.0, "volume")
        assert "+45.0%" in cell
        assert "up" not in cell and "dn" not in cell

    def test_the_table_carries_a_header_per_declared_parameter(self):
        rows = portfolio.rows(snapshot(), {}, {})
        markup = page.table(rows)
        for declared in ix.declared():
            assert declared["label"] in markup

    def test_a_header_explains_itself(self):
        assert ix.RSI.means in page.table([])

    def test_an_unpriced_rows_ownership_columns_still_add_up(self):
        markup = page.table([])
        header = markup.count("<th") - markup.count("<thead")   # <thead matches <th
        units = [(d["key"], d["unit"]) for d in ix.declared()]
        row = page._row({"priced": False, "name": "X", "ticker": "X",
                         "values": {}, "position": {"quantity": 1}}, units)
        colspan = int(row.split('colspan="')[1].split('"')[0])
        # Every <td> (name, qty, the 7-wide span, one per parameter) minus
        # the extra 4 columns the span covers beyond its own single <td>.
        individual_cells = row.count("<td") - 1
        assert colspan + individual_cells == header

    def test_an_unpriced_row_still_shows_a_volume_only_parameter(self):
        units = [(d["key"], d["unit"]) for d in ix.declared()]
        row = page._row({"priced": False, "name": "X", "ticker": "X",
                         "values": {"volume_trend": 42.0},
                         "position": {"quantity": 1}}, units)
        assert "+42.0%" in row


class TestTrendUsesTheDisplayedPrice:
    """Parameters measured against the live price."""

    @pytest.fixture
    def series(self, monkeypatch):
        from datetime import date, timedelta
        start = date(2025, 1, 1)
        stored = {(start + timedelta(days=i)).isoformat(): (100.0, "yahoo")
                  for i in range(400)}
        monkeypatch.setattr(portfolio.price_history, "load",
                            lambda t: dict(stored))
        return stored

    def test_the_live_price_is_included(self, series):
        m = portfolio._series("SAP.DE", price=150.0, on=date(2026, 2, 5))
        assert m["last"] == 150.0

    def test_it_is_not_written_back(self, series, monkeypatch):
        written = []
        monkeypatch.setattr(portfolio.price_history, "record",
                            lambda *a, **k: written.append(a))
        portfolio._series("SAP.DE", price=150.0, on=date(2026, 2, 5))
        assert written == []

    def test_a_session_already_stored_is_not_duplicated(self, series):
        m = portfolio._series("SAP.DE", price=999.0, on=date(2025, 6, 1))
        assert m["last"] == 100.0      # the stored close for that day wins

    def test_without_a_price_the_series_stands_alone(self, series):
        assert portfolio._series("SAP.DE")["last"] == 100.0

    def test_no_series_means_no_metrics(self, monkeypatch):
        monkeypatch.setattr(portfolio.price_history, "load", lambda t: {})
        assert portfolio._series("SAP.DE", price=150.0) == {}

    def test_a_stale_quote_does_not_invent_a_session(self, series):
        m = portfolio._series("SAP.DE", price=999.0, on="2025-06-01")
        assert m["last"] == 100.0

    def test_a_quote_newer_than_the_series_is_used(self, series):
        m = portfolio._series("SAP.DE", price=999.0, on="2026-02-05")
        assert m["last"] == 999.0

    def test_a_settled_quote_is_not_re_entered_as_live(self, series):
        stored_last = "2026-02-04"
        m = portfolio._series("SAP.DE", price=100.0, on=stored_last)
        assert m["sessions"] == 400
        assert m["rsi"] == portfolio._series("SAP.DE")["rsi"]

    def test_positions_passes_the_settled_date_not_the_snapshot_date(
            self, series, monkeypatch):
        seen = {}
        monkeypatch.setattr(portfolio, "_series",
                            lambda t, price=None, on=None: seen.update(on=on) or {})
        snap = snapshot(date="2026-02-07")          # a Saturday snapshot
        snap["positions"] = [{"ticker": "SAP.DE", "quantity": 1,
                              "current_price": 100.0, "previous_close": 100.0,
                              "currency": "EUR", "price_date": "2026-02-06"}]
        portfolio.rows(snap, {}, {})
        assert seen["on"] == "2026-02-06"

    def test_the_snapshot_date_is_used_when_the_quote_is_unsettled(
            self, series, monkeypatch):
        seen = {}
        monkeypatch.setattr(portfolio, "_series",
                            lambda t, price=None, on=None: seen.update(on=on) or {})
        snap = snapshot(date="2026-02-09")
        snap["positions"] = [{"ticker": "SAP.DE", "quantity": 1,
                              "current_price": 100.0, "previous_close": 100.0,
                              "currency": "EUR", "price_date": None}]
        portfolio.rows(snap, {}, {})
        assert seen["on"] == "2026-02-09"


class TestSeriesIncludesVolume:
    """`_series` with price history, volume history, or both."""

    @pytest.fixture
    def closes(self, monkeypatch):
        from datetime import date, timedelta
        start = date(2025, 1, 1)
        stored = {(start + timedelta(days=i)).isoformat(): (100.0, "yahoo")
                  for i in range(30)}
        monkeypatch.setattr(portfolio.price_history, "load",
                            lambda t: dict(stored))
        return stored

    def test_no_volume_series_means_no_volume_trend(self, closes, monkeypatch):
        monkeypatch.setattr(portfolio.volume_history, "load", lambda t: {})
        m = portfolio._series("SAP.DE")
        assert "volume_trend" not in m

    def test_a_volume_series_produces_a_volume_trend(self, closes, monkeypatch):
        from datetime import date, timedelta
        start = date(2025, 1, 1)
        volumes = {(start + timedelta(days=i)).isoformat(): (1_000_000.0, "yahoo")
                   for i in range(20)}
        monkeypatch.setattr(portfolio.volume_history, "load",
                            lambda t: dict(volumes))
        m = portfolio._series("SAP.DE")
        assert m["volume_trend"] is not None

    def test_volume_trend_needs_no_price_series(self, monkeypatch):
        from datetime import date, timedelta
        monkeypatch.setattr(portfolio.price_history, "load", lambda t: {})
        start = date(2025, 1, 1)
        volumes = {(start + timedelta(days=i)).isoformat(): (1_000_000.0, "yahoo")
                   for i in range(20)}
        monkeypatch.setattr(portfolio.volume_history, "load",
                            lambda t: dict(volumes))
        m = portfolio._series("SAP.DE")
        assert m["volume_trend"] is not None
        assert "last" not in m       # nothing to say about a price that isn't there


class TestIncompleteAggregate:
    """Headline P&L when some positions cannot be converted."""

    def pair(self):
        snap, held = matched(sap_cost=150.0, alv_cost=100.0)
        snap["positions"][1]["cost_currency"] = "XXX"     # ALV.DE, no rate
        held["ALV.DE"].currency = "XXX"
        return snap, held

    def _html(self, pair, tmp_path, monkeypatch):
        snap, held = pair
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load", lambda: ([snap], held, {}))
        dashboard.main()
        return (tmp_path / "out.html").read_text(encoding="utf-8")

    def test_the_headline_says_it_excludes_something(self, tmp_path, monkeypatch):
        html = self._html(self.pair(), tmp_path, monkeypatch)
        assert "excludes 1" in html

    def test_the_omitted_position_is_named_and_explained(
            self, tmp_path, monkeypatch):
        html = self._html(self.pair(), tmp_path, monkeypatch)
        assert "no available rate" in html and "ALV.DE" in html

    def test_it_is_not_called_a_missing_cost_basis(self, tmp_path, monkeypatch):
        html = self._html(self.pair(), tmp_path, monkeypatch)
        assert "states no cost basis" not in html

    def test_a_complete_portfolio_carries_no_such_note(
            self, tmp_path, monkeypatch):
        html = self._html(matched(sap_cost=150.0, alv_cost=100.0),
                          tmp_path, monkeypatch)
        assert "no available rate" not in html
        assert "excludes" not in html

    def test_an_unpriced_warning_survives_a_complete_cost_basis(
            self, tmp_path, monkeypatch):
        snap, held = matched(sap_cost=150.0, alv_cost=100.0)
        snap["positions"][1]["current_price"] = None      # ALV.DE unpriced
        html = self._html((snap, held), tmp_path, monkeypatch)
        assert "could not be priced" in html


class TestNothingConverts:
    """Headline P&L when no cost basis converts."""

    def pair(self):
        snap, held = matched(sap_cost=150.0, alv_cost=100.0)
        for p in snap["positions"]:
            p["cost_currency"] = "XXX"
        for h in held.values():
            h.currency = "XXX"
        return snap, held

    def _html(self, pair, tmp_path, monkeypatch):
        snap, held = pair
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load", lambda: ([snap], held, {}))
        dashboard.main()
        return (tmp_path / "out.html").read_text(encoding="utf-8")

    def test_the_headline_blames_the_rate_not_the_basis(
            self, tmp_path, monkeypatch):
        html = self._html(self.pair(), tmp_path, monkeypatch)
        assert "no rate for the cost basis" in html

    def test_it_does_not_claim_the_bases_are_absent(self, tmp_path, monkeypatch):
        html = self._html(self.pair(), tmp_path, monkeypatch)
        assert "no cost basis recorded" not in html

    def test_a_portfolio_truly_without_costs_still_says_so(
            self, tmp_path, monkeypatch):
        html = self._html(matched(), tmp_path, monkeypatch)
        assert "no cost basis recorded" in html


class TestIncompleteSnapshotsLeaveTheChart:
    """Incomplete snapshots are left out of the value chart."""

    def snap(self, day, total, priced=True):
        s = snapshot(date=day, total_value=total)
        s["positions"] = [{"ticker": "SAP.DE", "quantity": 4, "currency": "EUR",
                           "current_price": 200.0 if priced else None,
                           "previous_close": 198.0}]
        return s

    def test_a_fully_priced_snapshot_is_complete(self):
        assert portfolio.complete(self.snap("2026-09-18", 800.0))

    def test_an_unpriced_position_makes_it_incomplete(self):
        assert not portfolio.complete(self.snap("2026-09-18", 0.0, priced=False))

    def test_the_partial_point_is_dropped_from_the_series(self):
        snaps = [self.snap("2026-09-16", 800.0),
                 self.snap("2026-09-17", 0.0, priced=False),
                 self.snap("2026-09-18", 810.0)]
        kept = [s["date"] for s in portfolio.comparable(snaps)]
        assert kept == ["2026-09-16", "2026-09-18"]

    def test_completeness_needs_no_stored_flag(self):
        old = self.snap("2026-09-18", 800.0)
        assert "complete" not in old and portfolio.complete(old)

    def test_the_dashboard_says_it_left_something_out(
            self, tmp_path, monkeypatch):
        snap, held = matched(sap_cost=150.0, alv_cost=100.0)
        partial = snapshot(date="2026-09-17", total_value=0.0)
        partial["positions"] = [{"ticker": "SAP.DE", "quantity": 4,
                                 "currency": "EUR", "current_price": None,
                                 "previous_close": None}]
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(portfolio, "load", lambda: ([partial, snap], held, {}))
        dashboard.main()
        html = (tmp_path / "out.html").read_text(encoding="utf-8")
        assert "left out of the value chart" in html


class TestSnapshotTime:
    """The time the snapshot was taken, shown under the heading."""

    def test_a_timestamp_is_shown_with_its_time(self):
        from render.html import taken
        assert taken("2026-09-24T08:03:37+02:00") == "Thu 24 Sep 2026, 08:03"

    def test_a_date_alone_is_shown_without_a_time(self):
        from render.html import taken
        assert taken("2026-09-18") == "Fri 18 Sep 2026"

    def test_an_unreadable_value_is_shown_as_given(self):
        from render.html import taken
        assert taken("yesterday") == "yesterday"
