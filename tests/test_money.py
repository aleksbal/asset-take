"""Money that knows its currency, and conversions that keep their working.

A converted amount used to be a bare float. That is how a rate of 1.0 came to
be defaulted in seven places: a number that has been converted is
indistinguishable from one that has not, and both render.
"""
import pytest

import fx
from money import Converted, Money, total


@pytest.fixture
def rates(monkeypatch):
    """Stub the rate service. None where a pair cannot be priced."""
    table = {("USD", "EUR"): 0.87, ("GBP", "EUR"): 1.15, ("EUR", "EUR"): 1.0}

    def fake(currency, base, on=None):
        return table.get((currency, base))

    monkeypatch.setattr(fx, "rate", fake)
    return table


class TestMoney:
    def test_an_amount_carries_its_currency(self):
        m = Money(100.0, "USD")
        assert m.amount == 100.0 and m.currency == "USD"

    def test_scaling_is_quantity_times_price(self):
        assert Money(150.0, "USD").scaled(4) == Money(600.0, "USD")

    def test_scaling_does_not_change_the_currency(self):
        assert Money(150.0, "USD").scaled(4).currency == "USD"

    def test_money_is_immutable(self):
        with pytest.raises(Exception):
            Money(1.0, "EUR").amount = 2.0


class TestExchange:
    def test_converts_at_the_rate(self, rates):
        c = fx.exchange(Money(100.0, "USD"), "EUR")
        assert c.amount == pytest.approx(87.0)

    def test_keeps_what_it_started_from(self, rates):
        c = fx.exchange(Money(100.0, "USD"), "EUR")
        assert c.original == Money(100.0, "USD")
        assert c.rate == 0.87
        assert c.base == "EUR"

    def test_an_unavailable_rate_gives_nothing(self, rates):
        """None, never the unconverted amount and never a rate of 1.0. A
        holding valued at a made-up rate looks exactly like a correct one."""
        assert fx.exchange(Money(100.0, "XXX"), "EUR") is None

    def test_the_same_currency_still_produces_a_conversion(self, rates):
        c = fx.exchange(Money(100.0, "EUR"), "EUR")
        assert c.rate == 1.0 and c.amount == 100.0

    def test_an_absent_amount_stays_absent(self, rates):
        assert fx.exchange(None, "EUR") is None

    def test_the_rate_date_is_recorded(self, rates):
        from datetime import date
        on = date(2026, 9, 17)
        assert fx.exchange(Money(100.0, "USD"), "EUR", on=on).on == on

    def test_a_spot_conversion_records_no_date(self, rates):
        """Absent and 'today' are different claims about which rate was used."""
        assert fx.exchange(Money(100.0, "USD"), "EUR").on is None


class TestConverted:
    def test_the_result_is_available_as_money(self):
        c = Converted(Money(100.0, "USD"), rate=0.87, base="EUR")
        assert c.money == Money(pytest.approx(87.0), "EUR")

    def test_the_result_is_denominated_in_the_base(self):
        c = Converted(Money(100.0, "USD"), rate=0.87, base="EUR")
        assert c.money.currency == "EUR"


class TestTotal:
    def test_sums_converted_amounts(self):
        items = [Converted(Money(100.0, "USD"), 0.87, "EUR"),
                 Converted(Money(50.0, "EUR"), 1.0, "EUR")]
        assert total(items, "EUR").amount == pytest.approx(137.0)

    def test_skips_what_could_not_be_converted(self):
        items = [Converted(Money(100.0, "USD"), 0.87, "EUR"), None]
        assert total(items, "EUR").amount == pytest.approx(87.0)

    def test_nothing_to_total_is_none_not_zero(self):
        """sum([]) is 0, and a portfolio with no cost basis reported +0 of
        unrealised P&L - an unknown dressed as a certainty."""
        assert total([], "EUR") is None

    def test_all_unconvertible_is_none_not_zero(self):
        assert total([None, None], "EUR") is None
