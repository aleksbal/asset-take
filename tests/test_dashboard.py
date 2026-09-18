"""Dashboard computation over holdings that state no cost basis.

This is where the missing-cost bug surfaced: `canonical.read()` raised on the
empty field, so any import from a source without cost data killed the whole
dashboard rather than simply omitting P&L.
"""
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
