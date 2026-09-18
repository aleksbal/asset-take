"""ISIN → ticker resolution.

The provider returns a security's primary listing, which for a German broker's
EUR cost basis is frequently the wrong currency, and an ISIN search can return
a similarly-named but different instrument. Neither failure is visible in the
output, so resolution is verified against the broker's own price rather than
trusted.

Network calls are stubbed: these test the decision, not the provider.
"""
import pytest

import resolve as rz
from canonical import Holding


def holding(**kw):
    base = dict(isin="IE00B4L5Y983", name="ISHSIII-CORE MSCI WLD DLA",
                quantity=100, avg_cost=82.37, currency="EUR",
                broker_price=127.42, source="ing")
    base.update(kw)
    return Holding(**base)


@pytest.fixture
def market(monkeypatch):
    """Let a test declare the candidate universe and its prices."""
    prices = {}

    def set_market(candidates, quotes):
        prices.update(quotes)
        monkeypatch.setattr(rz, "_candidates", lambda isin, name: candidates)
        monkeypatch.setattr(rz, "_price", lambda t: prices.get(t, (None, None)))
    return set_market


def test_picks_the_listing_matching_the_broker_price(market):
    market(["IUSN.DE", "EUNL.DE"], {
        "IUSN.DE": (6.60, "EUR"),    # small-cap namesake — wildly off
        "EUNL.DE": (127.40, "EUR"),  # the actual instrument
    })
    row = rz.resolve(holding())
    assert row["ticker"] == "EUNL.DE"
    assert row["status"] == "ok"


def test_flags_a_near_namesake_when_it_is_the_only_candidate(market):
    """The real failure: a small-cap ETF matched to its large-cap namesake."""
    market(["IUSN.DE"], {"IUSN.DE": (6.60, "EUR")})
    row = rz.resolve(holding())
    assert row["status"] == "check"
    assert float(row["deviation_pct"]) > 50


def test_rejects_a_listing_in_another_currency(market):
    """A USD quote against an EUR cost basis makes every P&L figure wrong."""
    market(["NVDA"], {"NVDA": (190.64, "USD")})
    row = rz.resolve(holding(isin="US67066G1040", name="NVIDIA CORP.",
                             broker_price=190.64))
    assert row["ticker"] == ""
    assert row["status"] == "unresolved"


def test_prefers_currency_match_over_price_proximity(market):
    market(["NVDA", "NVD.DE"], {
        "NVDA": (190.64, "USD"),   # exact, wrong currency
        "NVD.DE": (191.00, "EUR"),  # slightly off, right currency
    })
    row = rz.resolve(holding(isin="US67066G1040", broker_price=190.64))
    assert row["ticker"] == "NVD.DE"


def test_unresolved_when_nothing_is_found(market):
    market([], {})
    row = rz.resolve(holding())
    assert row["status"] == "unresolved"
    assert row["ticker"] == ""


def test_manual_entries_survive_reresolution(market):
    """A correction must not be undone by the next import."""
    market(["SOMETHING.ELSE"], {"SOMETHING.ELSE": (127.40, "EUR")})
    pinned = {"isin": "IE00B4L5Y983", "ticker": "EUNL.DE", "currency": "EUR",
              "yahoo_price": "", "broker_price": 127.42, "deviation_pct": "",
              "status": "manual", "display_name": "iShares Core MSCI World",
              "name": "ISHSIII-CORE MSCI WLD DLA"}
    assert rz.resolve(holding(), existing=pinned) == pinned


def test_tolerance_boundary_is_the_documented_one(market):
    just_inside = 127.42 * (1 + rz.TOLERANCE * 0.9)
    market(["X.DE"], {"X.DE": (just_inside, "EUR")})
    assert rz.resolve(holding())["status"] == "ok"

    just_outside = 127.42 * (1 + rz.TOLERANCE * 1.1)
    market(["Y.DE"], {"Y.DE": (just_outside, "EUR")})
    assert rz.resolve(holding())["status"] == "check"


class TestWithoutABrokerPrice:
    """A source that states no valuation, e.g. the generic CSV adapter.

    Before the second adapter existed, every such holding resolved to nothing:
    verification was mandatory and there was nothing to verify against.
    """

    def test_takes_a_currency_match_and_marks_it_unverified(self, market):
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(broker_price=None))
        assert row["ticker"] == "EUNL.DE"
        assert row["status"] == "unverified"

    def test_still_rejects_a_currency_mismatch(self, market):
        market(["IWDA.L"], {"IWDA.L": (110.0, "GBP")})
        assert rz.resolve(holding(broker_price=None))["status"] == "unresolved"

    def test_uses_a_ticker_given_directly(self, market):
        """The generic adapter may supply a ticker rather than an ISIN."""
        market([], {"IWDA.AS": (127.40, "EUR")})
        row = rz.resolve(holding(isin="IWDA.AS", broker_price=None))
        assert row["ticker"] == "IWDA.AS"
        assert row["status"] == "unverified"

    def test_a_given_ticker_is_still_verified_when_a_price_exists(self, market):
        market([], {"IWDA.AS": (127.40, "EUR")})
        row = rz.resolve(holding(isin="IWDA.AS", broker_price=127.42))
        assert row["status"] == "ok"
