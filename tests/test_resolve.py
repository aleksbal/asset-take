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
    """Let a test declare the candidate universe, its prices and its depths.

    `depths` is how many days of history each listing carries. It defaults to
    equal depth, so a test that does not care about history is decided purely
    on price, as it was before depth entered the tiebreak. Stubbing it also
    keeps the suite off the network.
    """
    prices, depths, units = {}, {}, {}
    rates = {"EUR": 1.0, "USD": 0.87, "GBP": 1.15, "GBp": 0.0115}

    def set_market(candidates, quotes, history=None, fx=None, quoted_in=None):
        prices.update(quotes)
        depths.update(history or {})
        rates.update(fx or {})
        units.update(quoted_in or {})
        monkeypatch.setattr(rz, "_quote_unit",
                            lambda t: units.get(t, prices.get(t, ("", ""))[1]))
        monkeypatch.setattr(rz, "_candidates", lambda isin, name: candidates)
        monkeypatch.setattr(rz, "_price", lambda t: prices.get(t, (None, None)))
        monkeypatch.setattr(rz, "_depth", lambda t: depths.get(t, 0))
        monkeypatch.setattr(rz, "_fx", lambda c, b: (
            None if rates.get(c) is None or rates.get(b) is None
            else rates[c] / rates[b]))
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


def test_takes_a_foreign_listing_that_verifies_once_converted(market):
    """A listing in another currency is not a different instrument. Several
    holdings had no candidate at all in the broker's currency, and discarding
    the foreign one outright is what made hand-pinning necessary."""
    market(["NVDA"], {"NVDA": (219.13, "USD")})
    row = rz.resolve(holding(isin="US67066G1040", name="NVIDIA CORP.",
                             broker_price=190.64))
    assert row["ticker"] == "NVDA"
    assert row["currency"] == "USD"      # stored as quoted, converted later
    assert row["status"] == "ok"


def test_still_rejects_a_foreign_listing_that_does_not_verify(market):
    """Conversion widens the search; it does not weaken the check. The
    small-cap namesake stays rejected however its currency is read."""
    market(["IUSN.DE"], {"IUSN.DE": (6.60, "EUR")})
    row = rz.resolve(holding())
    assert row["status"] == "check"
    assert float(row["deviation_pct"]) > 50


def test_a_listing_whose_rate_is_unavailable_is_skipped(market):
    """No rate means no comparison. Pricing it at a rate of one would put a
    plausible number on an unverified instrument."""
    market(["XXX.QQ"], {"XXX.QQ": (127.40, "XXX")}, fx={"XXX": None})
    assert rz.resolve(holding())["status"] == "unresolved"


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

    def test_prefers_the_holdings_currency_when_nothing_verifies_it(self, market):
        """With no valuation to check against, currency is the only signal
        left, so a listing already in the holding's currency is the safer
        guess - but a foreign one still beats no mapping at all."""
        market(["IWDA.L", "EUNL.DE"],
               {"IWDA.L": (110.0, "GBP"), "EUNL.DE": (127.40, "EUR")})
        assert rz.resolve(holding(broker_price=None))["ticker"] == "EUNL.DE"

    def test_falls_back_to_a_foreign_listing_when_it_is_all_there_is(self, market):
        market(["IWDA.L"], {"IWDA.L": (110.0, "GBP")})
        row = rz.resolve(holding(broker_price=None))
        assert row["ticker"] == "IWDA.L"
        assert row["status"] == "unverified"

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


class TestExplicitTicker:
    def test_a_supplied_ticker_is_used_before_searching(self, market):
        """Searching the ISIN could return a different venue entirely."""
        market(["SOMETHING.ELSE"], {"SOMETHING.ELSE": (127.40, "EUR"),
                                    "EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(ticker="EUNL.DE", broker_price=None))
        assert row["ticker"] == "EUNL.DE"

    def test_falls_back_to_searching_when_the_ticker_does_not_price(self, market):
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(ticker="DELISTED.XX", broker_price=None))
        assert row["ticker"] == "EUNL.DE"


class TestHistoryBreaksAPriceTie:
    """Two listings of one security quote within a few hundredths of a percent
    of each other, and picking the closer one is picking noise. A regional
    venue priced five holdings correctly while carrying a single day of
    history - correct to value, impossible to chart.
    """

    def test_depth_wins_where_prices_are_indistinguishable(self, market):
        """The thin listing is the *closer* of the two here. Taking the
        closest price would pick it, which is what used to happen."""
        market(["DE000HAG0005.SG", "HAG.DE"],
               {"DE000HAG0005.SG": (77.25, "EUR"), "HAG.DE": (77.30, "EUR")},
               history={"DE000HAG0005.SG": 1, "HAG.DE": 505})
        row = rz.resolve(holding(broker_price=77.24))
        assert row["ticker"] == "HAG.DE"
        assert row["status"] == "ok"

    def test_depth_wins_across_the_whole_noise_band(self, market):
        """Half a percent apart is still venue noise, not a different
        security. The thin listing is again the closer one."""
        market(["THIN.SG", "DEEP.DE"],
               {"THIN.SG": (127.42, "EUR"), "DEEP.DE": (128.00, "EUR")},
               history={"THIN.SG": 1, "DEEP.DE": 505})
        assert rz.resolve(holding())["ticker"] == "DEEP.DE"

    def test_the_closer_price_still_wins_when_it_has_the_history(self, market):
        market(["EB2.SG", "EB2.F"],
               {"EB2.SG": (655.00, "EUR"), "EB2.F": (649.50, "EUR")},
               history={"EB2.SG": 1, "EB2.F": 505})
        assert rz.resolve(holding(broker_price=648.50))["ticker"] == "EB2.F"

    def test_a_real_price_difference_is_not_overridden_by_depth(self, market):
        """The band is narrow on purpose: a candidate percent away is a
        different security, however much history it carries."""
        market(["RIGHT.DE", "WRONG.DE"],
               {"RIGHT.DE": (127.40, "EUR"), "WRONG.DE": (120.00, "EUR")},
               history={"RIGHT.DE": 1, "WRONG.DE": 505})
        assert rz.resolve(holding())["ticker"] == "RIGHT.DE"

    def test_currency_still_gates_before_depth(self, market):
        market(["DEEP.US", "SHALLOW.DE"],
               {"DEEP.US": (127.41, "USD"), "SHALLOW.DE": (127.40, "EUR")},
               history={"DEEP.US": 505, "SHALLOW.DE": 1})
        assert rz.resolve(holding())["ticker"] == "SHALLOW.DE"

    def test_equal_depth_falls_back_to_the_closer_price(self, market):
        market(["A.DE", "B.DE"],
               {"A.DE": (127.41, "EUR"), "B.DE": (127.30, "EUR")},
               history={"A.DE": 505, "B.DE": 505})
        assert rz.resolve(holding())["ticker"] == "A.DE"

    def test_a_candidate_beyond_tolerance_is_still_flagged(self, market):
        market(["ONLY.DE"], {"ONLY.DE": (6.60, "EUR")}, history={"ONLY.DE": 505})
        assert rz.resolve(holding())["status"] == "check"

    def test_depth_is_a_threshold_not_a_score(self, market):
        """Once a listing carries a year, more days are not better. Ranking on
        raw count swaps a perfectly usable listing for another over a handful
        of trading days that differ only by local holidays."""
        market(["FRA.F", "VIE.VI"],
               {"FRA.F": (127.41, "EUR"), "VIE.VI": (127.60, "EUR")},
               history={"FRA.F": 505, "VIE.VI": 509})
        assert rz.resolve(holding())["ticker"] == "FRA.F"

    def test_a_listing_short_a_few_holidays_still_counts_as_deep(self, market):
        """The threshold sits below a full trading year so that a venue which
        closed for a few local holidays is not treated as historyless."""
        market(["FRA.F", "VIE.VI"],
               {"FRA.F": (127.41, "EUR"), "VIE.VI": (127.60, "EUR")},
               history={"FRA.F": 251, "VIE.VI": 252})
        assert rz.resolve(holding())["ticker"] == "FRA.F"


class TestStability:
    """Re-resolution must not churn. Candidates differ by hundredths of a
    percent and live prices move, so picking afresh each time flips between
    venues - and each flip starts the position's price history over under a
    new symbol."""

    def test_an_existing_mapping_that_still_verifies_is_kept(self, market):
        market(["RHM.HM", "RHM.DE"],
               {"RHM.HM": (127.42, "EUR"), "RHM.DE": (127.30, "EUR")},
               history={"RHM.HM": 505, "RHM.DE": 505})
        row = rz.resolve(holding(), existing={"ticker": "RHM.DE", "status": "ok"})
        assert row["ticker"] == "RHM.DE"      # despite RHM.HM being closer now

    def test_a_mapping_that_stopped_verifying_is_replaced(self, market):
        market(["RIGHT.DE"], {"RIGHT.DE": (127.40, "EUR"), "STALE.DE": (9.01, "EUR")},
               history={"RIGHT.DE": 505, "STALE.DE": 505})
        row = rz.resolve(holding(), existing={"ticker": "STALE.DE", "status": "ok"})
        assert row["ticker"] == "RIGHT.DE"

    def test_a_thin_mapping_is_not_entrenched_by_stability(self, market):
        """Keeping what we have must not preserve the very listings the depth
        preference exists to replace."""
        market(["THIN.SG", "DEEP.DE"],
               {"THIN.SG": (127.42, "EUR"), "DEEP.DE": (127.40, "EUR")},
               history={"THIN.SG": 1, "DEEP.DE": 505})
        row = rz.resolve(holding(), existing={"ticker": "THIN.SG", "status": "ok"})
        assert row["ticker"] == "DEEP.DE"

    def test_stability_does_not_apply_without_a_valuation(self, market):
        """Nothing to re-verify against, so the mapping cannot be trusted to
        still hold."""
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR"), "OLD.DE": (127.40, "EUR")},
               history={"EUNL.DE": 505, "OLD.DE": 505})
        row = rz.resolve(holding(broker_price=None),
                         existing={"ticker": "OLD.DE", "status": "ok"})
        assert row["ticker"] == "EUNL.DE"


class TestPinnedRows:
    """A pin fixes the ticker, not the figures beside it."""

    def test_a_pin_keeps_its_ticker(self, market):
        market(["OTHER.DE"], {"EUNL.DE": (127.40, "EUR"), "OTHER.DE": (127.42, "EUR")})
        row = rz.resolve(holding(), existing={"ticker": "EUNL.DE", "status": "manual",
                                              "yahoo_price": 9.047, "deviation_pct": 92.9})
        assert row["ticker"] == "EUNL.DE" and row["status"] == "manual"

    def test_a_pin_does_not_preserve_the_figures_it_replaced(self, market):
        """The row was pinned because the automatic match was 92.9% off. Those
        numbers describe the rejected match, not the pinned one."""
        market([], {"EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(), existing={"ticker": "EUNL.DE", "status": "manual",
                                              "yahoo_price": 9.047, "deviation_pct": 92.9})
        assert row["yahoo_price"] == 127.4
        assert float(row["deviation_pct"]) < 1

    def test_a_pin_that_cannot_be_priced_is_left_alone(self, market):
        market([], {})
        row = rz.resolve(holding(), existing={"ticker": "GONE.DE", "status": "manual",
                                              "yahoo_price": 9.047, "deviation_pct": 92.9})
        assert row["yahoo_price"] == 9.047


class TestMinorUnitQuotes:
    """London quotes pence, Johannesburg cents. The resolved row is written to
    positions.csv and valued downstream, where an unrecognised code such as
    GBp is given an exchange rate of 1.0 - so a 4,208 pence share is valued as
    4,208 pounds. Normalising at the comparison alone would not have helped:
    verification passed while the stored row stayed 100x out.
    """

    def test_pence_becomes_pounds(self):
        assert rz._as_major(4208.0, "GBp") == (42.08, "GBP")

    def test_pounds_are_left_alone(self):
        """GBP is the major unit. Scaling it here divided real prices by 100."""
        assert rz._as_major(42.08, "GBP") == (42.08, "GBP")

    def test_an_unknown_currency_passes_through(self):
        assert rz._as_major(100.0, "USD") == (100.0, "USD")

    def test_the_live_price_is_normalised_before_anything_sees_it(self,
                                                                 monkeypatch):
        class Quote:
            fast_info = {"last_price": 4208.0, "currency": "GBp"}

        monkeypatch.setattr(rz.yf, "Ticker", lambda t: Quote())
        assert rz._price("BATS.L") == (42.08, "GBP")

    def test_a_pence_listing_is_stored_in_the_major_unit(self, monkeypatch):
        """End to end: what lands in the row, not just what was compared."""
        class Quote:
            fast_info = {"last_price": 4876.0, "currency": "GBp"}

        monkeypatch.setattr(rz.yf, "Ticker", lambda t: Quote())
        monkeypatch.setattr(rz, "_candidates", lambda isin, name: ["BATS.L"])
        monkeypatch.setattr(rz, "_depth", lambda t: 505)
        monkeypatch.setattr(rz, "_fx", lambda c, b: {"GBP": 1.0}.get(c))
        row = rz.resolve(holding(isin="GB0002875804", name="BRIT.AMER.TOBACCO",
                                 currency="GBP", broker_price=48.76))
        assert row["currency"] == "GBP"
        assert row["yahoo_price"] == 48.76


class TestQuoteUnitIsRecorded:
    """The valuation layer must not have to ask what unit a venue quotes in.
    Asking there is a lookup that can fail, and a failure is indistinguishable
    from a major-unit quote - which values pence as pounds."""

    def test_the_row_carries_the_venues_own_unit(self, market):
        market(["BATS.L"], {"BATS.L": (48.76, "GBP")}, history={"BATS.L": 505},
               quoted_in={"BATS.L": "GBp"})
        row = rz.resolve(holding(currency="GBP", broker_price=48.76))
        assert row["currency"] == "GBP"        # the money
        assert row["quote_currency"] == "GBp"  # the unit it is quoted in

    def test_a_major_unit_listing_records_its_own_currency(self, market):
        market(["SAP.DE"], {"SAP.DE": (127.40, "EUR")}, history={"SAP.DE": 505})
        assert rz.resolve(holding())["quote_currency"] == "EUR"

    def test_a_pin_refreshes_the_unit_for_its_new_ticker(self, market):
        """The documented correction flow changes a row's ticker by hand. If
        the unit is carried over from the listing being replaced, a pence
        listing is marked as quoted in euros and valued a hundredfold high."""
        market([], {"BATS.L": (48.76, "GBP")}, quoted_in={"BATS.L": "GBp"})
        row = rz.resolve(holding(currency="GBP", broker_price=48.76),
                         existing={"ticker": "BATS.L", "status": "manual",
                                   "currency": "EUR", "quote_currency": "EUR"})
        assert row["quote_currency"] == "GBp"
        assert row["currency"] == "GBP"
