"""Tests for the canonical holding record and its CSV round-trip."""
import pytest

from holdings import canonical
from holdings.canonical import Holding


def holding(**kw):
    base = dict(isin="IE00B4L5Y983", name="iShares Core MSCI World",
                quantity=100, currency="EUR", avg_cost=82.37,
                broker_price=127.42, source="ing")
    base.update(kw)
    return Holding(**base)


def test_round_trips_a_complete_holding(tmp_path):
    p = tmp_path / "h.csv"
    canonical.write([holding()], p)
    assert canonical.read(p) == [holding()]


@pytest.mark.parametrize("absent", ["avg_cost", "broker_price"])
def test_round_trips_a_missing_optional_field(tmp_path, absent):
    p = tmp_path / "h.csv"
    canonical.write([holding(**{absent: None})], p)
    assert getattr(canonical.read(p)[0], absent) is None


def test_missing_cost_is_none_not_zero(tmp_path):
    p = tmp_path / "h.csv"
    canonical.write([holding(avg_cost=None)], p)
    assert canonical.read(p)[0].avg_cost != 0
    assert canonical.read(p)[0].avg_cost is None


def test_reads_a_holding_with_every_optional_field_absent(tmp_path):
    p = tmp_path / "h.csv"
    canonical.write([Holding(isin="IWDA.AS", name="IWDA.AS", quantity=30,
                             currency="EUR", source="generic")], p)
    h = canonical.read(p)[0]
    assert (h.avg_cost, h.broker_price, h.broker_as_of, h.venue) == (None, None, None, None)
    assert h.quantity == 30


def test_quantity_stays_required(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("isin,name,quantity,currency,avg_cost,broker_price,broker_as_of,venue,source\n"
                 "X,Y,,EUR,,,,,generic\n")
    with pytest.raises(ValueError):
        canonical.read(p)
