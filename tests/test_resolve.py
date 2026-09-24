"""Tests for ISIN to ticker resolution in holdings/resolve.py."""
import pytest

from holdings import resolve as rz
from holdings.canonical import Holding


def holding(**kw):
    base = dict(isin="IE00B4L5Y983", name="ISHSIII-CORE MSCI WLD DLA",
                quantity=100, avg_cost=82.37, currency="EUR",
                broker_price=127.42, source="ing")
    base.update(kw)
    return Holding(**base)


@pytest.fixture
def market(monkeypatch):
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
    market(["IUSN.DE"], {"IUSN.DE": (6.60, "EUR")})
    row = rz.resolve(holding())
    assert row["status"] == "check"
    assert float(row["deviation_pct"]) > 50


def test_takes_a_foreign_listing_that_verifies_once_converted(market):
    market(["NVDA"], {"NVDA": (219.13, "USD")})
    row = rz.resolve(holding(isin="US67066G1040", name="NVIDIA CORP.",
                             broker_price=190.64))
    assert row["ticker"] == "NVDA"
    assert row["currency"] == "USD"      # stored as quoted, converted later
    assert row["status"] == "ok"


def test_still_rejects_a_foreign_listing_that_does_not_verify(market):
    market(["IUSN.DE"], {"IUSN.DE": (6.60, "EUR")})
    row = rz.resolve(holding())
    assert row["status"] == "check"
    assert float(row["deviation_pct"]) > 50


def test_a_listing_whose_rate_is_unavailable_is_skipped(market):
    market(["XXX.QQ"], {"XXX.QQ": (127.40, "XXX")}, fx={"XXX": None})
    assert rz.resolve(holding())["status"] == "unresolved"


def test_an_exact_foreign_listing_beats_a_near_domestic_one(market):
    market(["NVDA", "NVD.DE"], {
        "NVDA": (219.13, "USD"),    # 190.64 EUR once converted - exact
        "NVD.DE": (191.00, "EUR"),  # slightly off
    }, history={"NVDA": 505, "NVD.DE": 505})
    row = rz.resolve(holding(isin="US67066G1040", broker_price=190.64))
    assert row["ticker"] == "NVDA"


def test_the_currency_preference_restores_the_domestic_listing(market):
    market(["NVDA", "NVD.DE"], {
        "NVDA": (219.13, "USD"),
        "NVD.DE": (191.00, "EUR"),
    }, history={"NVDA": 505, "NVD.DE": 505})
    row = rz.resolve(holding(isin="US67066G1040", broker_price=190.64),
                     prefer_currency=True)
    assert row["ticker"] == "NVD.DE"


def test_unresolved_when_nothing_is_found(market):
    market([], {})
    row = rz.resolve(holding())
    assert row["status"] == "unresolved"
    assert row["ticker"] == ""


def test_manual_entries_survive_reresolution(market):
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
    """Resolution for sources that state no broker price."""

    def test_takes_a_currency_match_and_marks_it_unverified(self, market):
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(broker_price=None))
        assert row["ticker"] == "EUNL.DE"
        assert row["status"] == "unverified"

    def test_prefers_the_holdings_currency_when_nothing_verifies_it(self, market):
        market(["IWDA.L", "EUNL.DE"],
               {"IWDA.L": (110.0, "GBP"), "EUNL.DE": (127.40, "EUR")})
        assert rz.resolve(holding(broker_price=None))["ticker"] == "EUNL.DE"

    def test_falls_back_to_a_foreign_listing_when_it_is_all_there_is(self, market):
        market(["IWDA.L"], {"IWDA.L": (110.0, "GBP")})
        row = rz.resolve(holding(broker_price=None))
        assert row["ticker"] == "IWDA.L"
        assert row["status"] == "unverified"

    def test_uses_a_ticker_given_directly(self, market):
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
        market(["SOMETHING.ELSE"], {"SOMETHING.ELSE": (127.40, "EUR"),
                                    "EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(ticker="EUNL.DE", broker_price=None))
        assert row["ticker"] == "EUNL.DE"

    def test_falls_back_to_searching_when_the_ticker_does_not_price(self, market):
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR")})
        row = rz.resolve(holding(ticker="DELISTED.XX", broker_price=None))
        assert row["ticker"] == "EUNL.DE"


class TestHistoryBreaksAPriceTie:
    """History depth decides between equally close candidates."""

    def test_depth_wins_where_prices_are_indistinguishable(self, market):
        market(["DE000HAG0005.SG", "HAG.DE"],
               {"DE000HAG0005.SG": (77.25, "EUR"), "HAG.DE": (77.30, "EUR")},
               history={"DE000HAG0005.SG": 1, "HAG.DE": 505})
        row = rz.resolve(holding(broker_price=77.24))
        assert row["ticker"] == "HAG.DE"
        assert row["status"] == "ok"

    def test_depth_wins_across_the_whole_noise_band(self, market):
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
        market(["RIGHT.DE", "WRONG.DE"],
               {"RIGHT.DE": (127.40, "EUR"), "WRONG.DE": (120.00, "EUR")},
               history={"RIGHT.DE": 1, "WRONG.DE": 505})
        assert rz.resolve(holding())["ticker"] == "RIGHT.DE"

    def test_depth_outranks_currency(self, market):
        market(["DEEP.US", "SHALLOW.DE"],
               {"DEEP.US": (146.46, "USD"),     # 127.42 EUR once converted
                "SHALLOW.DE": (127.40, "EUR")},
               history={"DEEP.US": 505, "SHALLOW.DE": 1})
        assert rz.resolve(holding())["ticker"] == "DEEP.US"

    def test_the_currency_preference_does_not_rescue_a_stub(self, market):
        market(["DEEP.US", "SHALLOW.DE"],
               {"DEEP.US": (146.46, "USD"), "SHALLOW.DE": (127.40, "EUR")},
               history={"DEEP.US": 505, "SHALLOW.DE": 1})
        assert rz.resolve(holding(), prefer_currency=True)["ticker"] == "DEEP.US"

    def test_equal_depth_falls_back_to_the_closer_price(self, market):
        market(["A.DE", "B.DE"],
               {"A.DE": (127.41, "EUR"), "B.DE": (127.30, "EUR")},
               history={"A.DE": 505, "B.DE": 505})
        assert rz.resolve(holding())["ticker"] == "A.DE"

    def test_a_candidate_beyond_tolerance_is_still_flagged(self, market):
        market(["ONLY.DE"], {"ONLY.DE": (6.60, "EUR")}, history={"ONLY.DE": 505})
        assert rz.resolve(holding())["status"] == "check"

    def test_depth_is_a_threshold_not_a_score(self, market):
        market(["FRA.F", "VIE.VI"],
               {"FRA.F": (127.41, "EUR"), "VIE.VI": (127.60, "EUR")},
               history={"FRA.F": 505, "VIE.VI": 509})
        assert rz.resolve(holding())["ticker"] == "FRA.F"

    def test_a_listing_short_a_few_holidays_still_counts_as_deep(self, market):
        market(["FRA.F", "VIE.VI"],
               {"FRA.F": (127.41, "EUR"), "VIE.VI": (127.60, "EUR")},
               history={"FRA.F": 251, "VIE.VI": 252})
        assert rz.resolve(holding())["ticker"] == "FRA.F"


class TestStability:
    """An existing mapping is kept while it still verifies."""

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
        market(["THIN.SG", "DEEP.DE"],
               {"THIN.SG": (127.42, "EUR"), "DEEP.DE": (127.40, "EUR")},
               history={"THIN.SG": 1, "DEEP.DE": 505})
        row = rz.resolve(holding(), existing={"ticker": "THIN.SG", "status": "ok"})
        assert row["ticker"] == "DEEP.DE"

    def test_stability_does_not_apply_without_a_valuation(self, market):
        market(["EUNL.DE"], {"EUNL.DE": (127.40, "EUR"), "OLD.DE": (127.40, "EUR")},
               history={"EUNL.DE": 505, "OLD.DE": 505})
        row = rz.resolve(holding(broker_price=None),
                         existing={"ticker": "OLD.DE", "status": "ok"})
        assert row["ticker"] == "EUNL.DE"


class TestPinnedRows:
    """Manual rows keep their ticker and get refreshed prices."""

    def test_a_pin_keeps_its_ticker(self, market):
        market(["OTHER.DE"], {"EUNL.DE": (127.40, "EUR"), "OTHER.DE": (127.42, "EUR")})
        row = rz.resolve(holding(), existing={"ticker": "EUNL.DE", "status": "manual",
                                              "yahoo_price": 9.047, "deviation_pct": 92.9})
        assert row["ticker"] == "EUNL.DE" and row["status"] == "manual"

    def test_a_pin_does_not_preserve_the_figures_it_replaced(self, market):
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
    """Listings quoted in pence."""

    def test_the_live_price_is_normalised_before_anything_sees_it(self,
                                                                 monkeypatch):
        class Quote:
            fast_info = {"last_price": 4208.0, "currency": "GBp"}

        monkeypatch.setattr(rz.yf, "Ticker", lambda t: Quote())
        assert rz._price("BATS.L") == (42.08, "GBP")

    def test_a_pence_listing_is_stored_in_the_major_unit(self, monkeypatch):
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
    """The quote unit is written to the map."""

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
        market([], {"BATS.L": (48.76, "GBP")}, quoted_in={"BATS.L": "GBp"})
        row = rz.resolve(holding(currency="GBP", broker_price=48.76),
                         existing={"ticker": "BATS.L", "status": "manual",
                                   "currency": "EUR", "quote_currency": "EUR"})
        assert row["quote_currency"] == "GBp"
        assert row["currency"] == "GBP"


class TestDisplayNameSurvivesReresolution:
    """Display names carried across a change of ticker."""

    def pinned(self, **kw):
        base = {"isin": "US3696043013", "ticker": "GCP.DE", "currency": "EUR",
                "quote_currency": "EUR", "yahoo_price": 272.55,
                "broker_price": 273.05, "deviation_pct": 0.18, "status": "ok",
                "display_name": "GE Aerospace"}
        base.update(kw)
        return base

    def test_a_kept_mapping_keeps_its_name(self, market):
        market(["GCP.DE"], {"GCP.DE": (127.40, "EUR")}, history={"GCP.DE": 505})
        row = rz.resolve(holding(), self.pinned())
        assert row["display_name"] == "GE Aerospace"

    def test_a_name_survives_the_ticker_changing(self, market):
        market(["GE"], {"GE": (127.40, "EUR")}, history={"GE": 505})
        row = rz.resolve(holding(), self.pinned(ticker="THIN.SG",
                                                yahoo_price=1.0))
        assert row["ticker"] == "GE"
        assert row["display_name"] == "GE Aerospace"

    def test_a_pinned_row_keeps_its_name(self, market):
        market(["GCP.DE"], {"GCP.DE": (127.40, "EUR")})
        row = rz.resolve(holding(), self.pinned(status="manual"))
        assert row["display_name"] == "GE Aerospace"

    def test_a_first_resolution_states_no_name(self, market):
        market(["GCP.DE"], {"GCP.DE": (127.40, "EUR")}, history={"GCP.DE": 505})
        assert not rz.resolve(holding()).get("display_name")

    def test_a_blank_stored_name_is_not_carried(self, market):
        market(["GCP.DE"], {"GCP.DE": (127.40, "EUR")}, history={"GCP.DE": 505})
        row = rz.resolve(holding(), self.pinned(display_name="   "))
        assert not (row.get("display_name") or "").strip()
