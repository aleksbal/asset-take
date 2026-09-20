"""The holdings -> positions.csv hand-off.

Untested until now, and it carried the defect that made the listing's currency
a selection criterion: a cost basis was dropped whenever the chosen listing
quoted in another currency, so resolution had to protect against that by
preferring a currency match over a usable price series.
"""
import csv

import pytest

import portfolio_monitor as pm
from holdings.canonical import Holding
from import_holdings import POSITION_COLUMNS, _de, position_row


def holding(**kw):
    base = dict(isin="US3696043013", name="GE AEROSPACE", quantity=33,
                currency="EUR", avg_cost=146.5527, broker_price=273.05)
    base.update(kw)
    return Holding(**base)


def resolved(**kw):
    base = {"ticker": "GE", "currency": "USD", "quote_currency": "USD"}
    base.update(kw)
    return base


@pytest.fixture
def as_position(tmp_path):
    """Round-trip a row through the reader it is written for.

    The writer and the reader are the contract; asserting on the cell text
    would pass while the two disagreed about what it meant.
    """
    def roundtrip(h, r):
        path = tmp_path / "positions.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(POSITION_COLUMNS)
            w.writerow(position_row(h, r))
        return pm.load_portfolio(str(path))[0]
    return roundtrip


class TestGermanNumberFormat:
    """`_de` writes what `parse_number` reads back. Round-trip, not looks."""

    @pytest.mark.parametrize("value", [146.5527, 82.368, 1234.56, 0.5, 640])
    def test_a_number_survives_the_round_trip(self, value):
        assert pm.parse_number(_de(value)) == pytest.approx(value)

    def test_a_whole_number_loses_its_decimal_part(self):
        assert _de(56.0) == "56"

    def test_absent_stays_absent(self):
        assert _de(None) == "" and _de("") == ""

    def test_a_blank_reads_back_as_none_not_zero(self):
        """A zero cost basis reads as a 100% gain."""
        assert pm.parse_number(_de(None) or "") is None


class TestCostCurrency:
    def test_the_cost_basis_survives_a_currency_mismatch(self, as_position):
        """The whole point. A EUR cost against a USD listing used to be
        written empty, which is why resolution avoided such listings."""
        p = as_position(holding(), resolved())
        assert p.avg_cost == pytest.approx(146.5527)

    def test_the_cost_keeps_the_brokers_currency(self, as_position):
        p = as_position(holding(), resolved())
        assert p.cost_currency == "EUR"      # what the broker charged in

    def test_the_position_keeps_the_listings_currency(self, as_position):
        p = as_position(holding(), resolved())
        assert p.currency == "USD"           # what the venue quotes in

    def test_a_matching_currency_is_still_recorded_explicitly(self, as_position):
        p = as_position(holding(), resolved(ticker="ALV.DE", currency="EUR",
                                            quote_currency="EUR"))
        assert p.currency == "EUR" and p.cost_currency == "EUR"

    def test_a_holding_without_a_cost_writes_no_cost(self, as_position):
        p = as_position(holding(avg_cost=None), resolved())
        assert p.avg_cost is None

    def test_the_quote_unit_is_not_case_folded(self, as_position):
        """GBp is pence and GBP is pounds."""
        p = as_position(holding(currency="GBP"),
                        resolved(ticker="BATS.L", currency="GBP",
                                 quote_currency="GBp"))
        assert p.quote_currency == "GBp"
