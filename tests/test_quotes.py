"""Quote units.

The first attempt at this normalised prices inside resolution while the
valuation layer downloaded its own quotes and bypassed the scaling, so the
stored row was right and every snapshot was still a hundredfold out. These
tests cover the shared conversion and both layers that must apply it.
"""
from datetime import date

import pytest

import quotes
from quotes import Quote


class TestConversion:
    def test_pence_becomes_pounds(self):
        assert quotes.as_major(4208.0, "GBp") == (42.08, "GBP")

    def test_cents_become_rand(self):
        assert quotes.as_major(1000.0, "ZAc") == (10.0, "ZAR")

    def test_a_major_unit_is_untouched(self):
        """GBP is the major unit. Scaling it divided real prices by 100."""
        assert quotes.as_major(42.08, "GBP") == (42.08, "GBP")

    def test_an_unknown_currency_passes_through(self):
        assert quotes.as_major(100.0, "USD") == (100.0, "USD")

    def test_an_absent_price_stays_absent(self):
        """A missing price is not a price of zero."""
        assert quotes.as_major(None, "GBp") == (None, "GBp")

    def test_an_unknown_unit_is_not_guessed(self):
        assert quotes.as_major(100.0, None) == (100.0, None)


class TestValuationLayerApplies:
    """`fetch_prices` downloads its own closes and never passes through
    resolution, so it has to scale them itself."""

    @pytest.fixture
    def pm(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        # A real download is indexed by trading day. Without one the date
        # handling raised and was swallowed, so this passed on the back of an
        # exception rather than on the scaling it claims to test.
        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {"shortName": "British American Tobacco"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        return pm

    def test_a_downloaded_pence_close_is_scaled(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.current_price == pytest.approx(42.08)
        assert out.previous_close == pytest.approx(42.00)

    def test_a_major_unit_download_is_left_alone(self, pm, monkeypatch):
        class Quote:
            fast_info = {"currency": "EUR"}
            info = {"shortName": "SAP SE"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        pos = pm.Position(ticker="SAP.DE", quantity=10, currency="EUR")
        [out] = pm.fetch_prices([pos])
        assert out.current_price == pytest.approx(4208.0)


class TestUnconfirmedUnit:
    """A failed unit lookup looks exactly like a major-unit quote. Assuming
    the latter values a pence quote as pounds, so the position is left
    unpriced instead - the dashboard reports those and excludes them from the
    total, which is visible, where a hundredfold overstatement is not."""

    @pytest.fixture
    def pm(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Blind:
            fast_info = {}
            info = {}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Blind())
        return pm

    def test_an_unknown_unit_leaves_the_position_unpriced(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.current_price is None

    def test_a_recorded_unit_survives_a_failed_lookup(self, pm):
        """This is the point of storing it: resolution already established the
        unit, so the valuation layer never depends on the network for it."""
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        assert out.current_price == pytest.approx(42.08)

    def test_the_settled_session_is_recorded_not_the_run_date(self, pm):
        """A weekend run downloads Friday's close. Filing it under Saturday
        invents a trading day, and the same-day guard then locks it in."""
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        assert out.price_date == "2026-09-17"


class TestMissingRate:
    """An unavailable rate used to default to 1.0, valuing a foreign holding
    as though it were domestic - wrong by whatever the exchange rate is, and
    reported as a price."""

    def test_a_currency_without_a_rate_is_omitted(self, monkeypatch):
        import portfolio_monitor as pm
        monkeypatch.setattr(pm.fx_service, "rate",
                            lambda c, b, on=None: None if c == "XXX" else 0.87)
        rates = pm.fetch_fx_rates({"USD", "XXX"}, "EUR")
        assert rates == {"EUR": 1.0, "USD": 0.87}

    def test_a_position_without_a_rate_is_left_unvalued(self, monkeypatch):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="X.QQ", quantity=10, currency="XXX",
                          current_price=100.0, previous_close=100.0)
        report = pm.calculate_report([pos], "EUR", {"EUR": 1.0},
                                     pm.AlertConfig())
        assert report.total_value == 0
        assert pos.current_price is None

    def test_a_position_with_a_rate_is_valued(self, monkeypatch):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="AAPL", quantity=10, currency="USD",
                          current_price=100.0, previous_close=100.0)
        report = pm.calculate_report([pos], "EUR", {"EUR": 1.0, "USD": 0.9},
                                     pm.AlertConfig())
        assert report.total_value == pytest.approx(900.0)


class TestQuoteType:
    """A bare float cannot say whether it is pounds or pence, or whether its
    session has closed. Six review rounds found that same gap in six places.
    A Quote cannot be built without the unit, so a later reader has nothing
    left to forget."""

    TODAY = date(2026, 9, 18)

    def test_a_pence_quote_arrives_in_pounds(self):
        q = Quote.from_provider(4208.0, "GBp", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert (q.price, q.currency) == (42.08, "GBP")

    def test_an_unknown_unit_yields_no_quote(self):
        """Not a quote in an assumed unit. Guessing has been the single most
        expensive assumption in this codebase."""
        assert Quote.from_provider(4208.0, None) is None
        assert Quote.from_provider(4208.0, "") is None

    def test_an_absent_price_yields_no_quote(self):
        assert Quote.from_provider(None, "GBp") is None

    def test_a_closed_session_is_settled(self):
        q = Quote.from_provider(100.0, "EUR", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert q.settled

    def test_todays_session_is_not_settled(self):
        """The bar may still be in progress and the provider flags nothing."""
        q = Quote.from_provider(100.0, "EUR", session=self.TODAY,
                                today=self.TODAY)
        assert not q.settled

    def test_no_session_is_not_settled(self):
        assert not Quote.from_provider(100.0, "EUR").settled

    def test_a_quote_converts_through_the_rate_service(self, monkeypatch):
        import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: 1.16)
        q = Quote.from_provider(4208.0, "GBp", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert q.converted("EUR", fx).amount == pytest.approx(42.08 * 1.16)

    def test_a_conversion_keeps_the_rate_and_what_it_started_from(
            self, monkeypatch):
        """The pence price, the rate and the result are all recoverable.

        A bare float cannot say whether a rate was applied, which is how 1.0
        came to be defaulted in seven places.
        """
        import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: 1.16)
        c = Quote.from_provider(4208.0, "GBp").converted("EUR", fx)
        assert c.original.amount == 42.08      # already the major unit
        assert c.original.currency == "GBP"
        assert c.rate == 1.16
        assert c.base == "EUR"

    def test_a_quote_without_a_rate_does_not_convert(self, monkeypatch):
        import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: None)
        assert Quote.from_provider(100.0, "EUR").converted("XXX", fx) is None

    def test_a_quote_is_immutable(self):
        """The unit travels with the price; neither can drift from the other."""
        q = Quote.from_provider(100.0, "EUR")
        with pytest.raises(Exception):
            q.price = 1.0


class TestCurrenciesNeeded:
    """Every currency a rate is needed for, cost bases included.

    The CLI built its rate set from listing currencies alone. A cost basis in
    a third currency then reached cost_of() without a rate, and that
    position's P&L dropped out of the report in silence - while snapshot.py,
    which used the helper, handled the same positions correctly.
    """

    def test_includes_the_cost_currency(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="GE", quantity=1, currency="USD",
                          cost_currency="EUR", avg_cost=100.0)
        assert pm.currencies([pos]) == {"USD", "EUR"}

    def test_a_third_currency_is_not_lost(self):
        """Listing, cost and base can all differ."""
        import portfolio_monitor as pm
        pos = pm.Position(ticker="BATS.L", quantity=1, currency="GBP",
                          cost_currency="CHF", avg_cost=100.0)
        assert pm.currencies([pos]) == {"GBP", "CHF"}

    def test_falls_back_to_the_position_currency(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR")
        assert pm.currencies([pos]) == {"EUR"}

    def test_a_cost_without_a_rate_yields_no_pnl_rather_than_a_wrong_one(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="GE", quantity=2, currency="USD",
                          cost_currency="CHF", avg_cost=100.0,
                          current_price=150.0)
        rates = {"EUR": 1.0, "USD": 0.87}          # no CHF
        assert pm.cost_of(pos, rates, "EUR") is None
        assert pm.value_of(pos, rates, "EUR").amount == pytest.approx(261.0)


class TestPartialPnlTotal:
    """A total that silently excludes positions states a sum of the rows that
    happened to work as though it were the portfolio. fetch_fx_rates omits a
    pair it cannot price rather than inventing 1.0, so this is reachable even
    after every currency has been requested."""

    def report(self):
        import portfolio_monitor as pm
        priced = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                             cost_currency="EUR", avg_cost=100.0,
                             current_price=150.0, previous_close=150.0)
        rateless = pm.Position(ticker="GE", quantity=1, currency="EUR",
                               cost_currency="CHF", avg_cost=100.0,
                               current_price=150.0, previous_close=150.0)
        return pm, pm.calculate_report([priced, rateless], "EUR", {"EUR": 1.0},
                                       pm.AlertConfig())

    def test_the_total_is_marked_incomplete(self):
        pm, report = self.report()
        body = pm.generate_long_report(report)
        assert "TOTAL*" in body

    def test_the_omitted_position_is_named(self):
        pm, report = self.report()
        body = pm.generate_long_report(report)
        assert "GE" in body.split("* excludes")[1]

    def test_a_complete_total_is_not_marked(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                          cost_currency="EUR", avg_cost=100.0,
                          current_price=150.0, previous_close=150.0)
        report = pm.calculate_report([pos], "EUR", {"EUR": 1.0}, pm.AlertConfig())
        body = pm.generate_long_report(report)
        assert "TOTAL*" not in body and "* excludes" not in body


class TestPartialPortfolioTotal:
    """A total that silently omits positions is a partial sum presented as
    the portfolio. Position details filters on current_price, so an omitted
    ticker vanishes from the table too - a reader who is not told cannot
    distinguish a smaller portfolio from an understated one."""

    def report(self):
        import portfolio_monitor as pm
        ok = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                         current_price=150.0, previous_close=150.0)
        rateless = pm.Position(ticker="X.QQ", quantity=1, currency="XXX",
                               current_price=150.0, previous_close=150.0)
        return pm, pm.calculate_report([ok, rateless], "EUR", {"EUR": 1.0},
                                       pm.AlertConfig())

    def test_the_omitted_position_is_recorded(self):
        pm, report = self.report()
        assert report.unvalued == ["X.QQ"]

    def test_the_long_report_marks_the_total(self):
        pm, report = self.report()
        body = pm.generate_long_report(report)
        assert "no exchange rate" in body and "X.QQ" in body

    def test_the_short_report_marks_the_total(self):
        pm, report = self.report()
        assert "excl. 1 unpriced" in pm.generate_short_report(report)

    def test_a_complete_portfolio_is_not_marked(self):
        import portfolio_monitor as pm
        ok = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                         current_price=150.0, previous_close=150.0)
        report = pm.calculate_report([ok], "EUR", {"EUR": 1.0}, pm.AlertConfig())
        assert report.unvalued == []
        assert "no exchange rate" not in pm.generate_long_report(report)
