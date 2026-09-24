"""Tests for quote units and for pricing in portfolio_monitor.py."""
from datetime import date, timedelta

import pytest

from market import quotes
from market.quotes import Quote


class TestConversion:
    def test_pence_becomes_pounds(self):
        assert quotes.as_major(4208.0, "GBp") == (42.08, "GBP")

    def test_cents_become_rand(self):
        assert quotes.as_major(1000.0, "ZAc") == (10.0, "ZAR")

    def test_a_major_unit_is_untouched(self):
        assert quotes.as_major(42.08, "GBP") == (42.08, "GBP")

    def test_an_unknown_currency_passes_through(self):
        assert quotes.as_major(100.0, "USD") == (100.0, "USD")

    def test_an_absent_price_stays_absent(self):
        assert quotes.as_major(None, "GBp") == (None, "GBp")

    def test_an_unknown_unit_is_not_guessed(self):
        assert quotes.as_major(100.0, None) == (100.0, None)


class TestValuationLayerApplies:
    """`fetch_prices` scales minor-unit prices."""

    @pytest.fixture
    def pm(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        # Indexed by trading day, like a real download.
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


class TestVolumeCapture:
    """Volumes captured by `fetch_prices`."""

    @pytest.fixture
    def pm(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0],
                              "Volume": [1_000_000.0, 1_200_000.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {"shortName": "British American Tobacco"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        return pm

    def test_the_settled_sessions_volumes_are_captured(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.volumes == {"2026-09-16": pytest.approx(1_000_000.0),
                               "2026-09-17": pytest.approx(1_200_000.0)}

    def test_no_volume_column_leaves_it_unset_not_zero(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {"shortName": "British American Tobacco"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.volumes == {}

    def test_a_nan_volume_is_not_a_number_either(self, monkeypatch):
        import math
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0],
                              "Volume": [1_000_000.0, float("nan")]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {"shortName": "British American Tobacco"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert "2026-09-17" not in out.volumes
        assert not any(math.isnan(v) for v in out.volumes.values())


class TestVolumeWithoutClose:
    """Volumes on rows that have no close."""

    def _out(self, monkeypatch, closes):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": closes,
                              "Volume": [1_000_000.0, 1_200_000.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        [out] = pm.fetch_prices([pm.Position(ticker="BATS.L", quantity=10,
                                             currency="GBP")])
        return out

    def test_a_row_without_a_close_keeps_its_volume(self, monkeypatch):
        out = self._out(monkeypatch, [4200.0, float("nan")])
        assert out.volumes["2026-09-17"] == pytest.approx(1_200_000.0)
        assert "2026-09-17" not in out.closes

    def test_a_download_without_any_close_keeps_its_volumes(self, monkeypatch):
        out = self._out(monkeypatch, [float("nan"), float("nan")])
        assert set(out.volumes) == {"2026-09-16", "2026-09-17"}
        assert out.current_price is None


class TestUnconfirmedUnit:
    """Positions whose quote unit is unknown."""

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

    def test_volume_is_captured_even_when_the_unit_cannot_be(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0],
                              "Volume": [1_000_000.0, 1_200_000.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Blind:
            fast_info = {}
            info = {}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Blind())
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.current_price is None            # unit still unresolved
        assert out.closes == {}
        assert out.volumes["2026-09-17"] == pytest.approx(1_200_000.0)

    def test_volumes_are_independent_of_closes(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        idx = pd.to_datetime(["2026-09-16", "2026-09-17"])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0],
                              "Volume": [1_000_000.0, 1_200_000.0]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Blind:
            fast_info = {}
            info = {}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Blind())
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP")
        [out] = pm.fetch_prices([pos])
        assert out.price_date is None and out.closes == {}
        assert set(out.volumes) == {"2026-09-16", "2026-09-17"}

    def test_a_recorded_unit_survives_a_failed_lookup(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        assert out.current_price == pytest.approx(42.08)

    def test_the_settled_session_is_recorded_not_the_run_date(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        assert out.price_date == "2026-09-17"

    def test_the_settled_closes_are_captured_in_the_major_unit(self, pm):
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        assert out.closes == {"2026-09-16": pytest.approx(42.00),
                              "2026-09-17": pytest.approx(42.08)}


class TestRunAfterTheClose:
    """A run after the close, when today's bar is unsettled."""

    @pytest.fixture
    def out(self, monkeypatch):
        import portfolio_monitor as pm
        import pandas as pd

        today = date.today()
        idx = pd.to_datetime([today - timedelta(days=2),
                              today - timedelta(days=1), today])
        frame = pd.DataFrame({"Close": [4200.0, 4208.0, 4250.0],
                              "Volume": [1.0e6, 1.2e6, 3.0e5]}, index=idx)
        monkeypatch.setattr(pm.yf, "download", lambda *a, **k: frame)

        class Quote:
            fast_info = {"currency": "GBp"}
            info = {"shortName": "British American Tobacco"}

        monkeypatch.setattr(pm.yf, "Ticker", lambda t: Quote())
        pos = pm.Position(ticker="BATS.L", quantity=10, currency="GBP",
                          quote_currency="GBp")
        [out] = pm.fetch_prices([pos])
        return out

    def test_the_snapshot_is_valued_at_the_latest_price(self, out):
        assert out.current_price == pytest.approx(42.50)
        assert out.price_date is None

    def test_the_settled_sessions_are_still_captured(self, out):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert out.closes[yesterday] == pytest.approx(42.08)
        assert out.volumes[yesterday] == pytest.approx(1.2e6)

    def test_todays_bar_is_not(self, out):
        assert date.today().isoformat() not in out.closes
        assert date.today().isoformat() not in out.volumes


class TestMissingRate:
    """Positions whose currency has no exchange rate."""

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

    def test_a_missing_rate_leaves_the_listings_closes_alone(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="X.QQ", quantity=10, currency="XXX",
                          current_price=100.0, previous_close=100.0,
                          closes={"2026-09-17": 100.0})
        pm.calculate_report([pos], "EUR", {"EUR": 1.0}, pm.AlertConfig())
        assert pos.closes == {"2026-09-17": 100.0}

    def test_a_position_with_a_rate_is_valued(self, monkeypatch):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="AAPL", quantity=10, currency="USD",
                          current_price=100.0, previous_close=100.0)
        report = pm.calculate_report([pos], "EUR", {"EUR": 1.0, "USD": 0.9},
                                     pm.AlertConfig())
        assert report.total_value == pytest.approx(900.0)


class TestQuoteType:
    """The Quote type."""

    TODAY = date(2026, 9, 18)

    def test_a_pence_quote_arrives_in_pounds(self):
        q = Quote.from_provider(4208.0, "GBp", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert (q.price, q.currency) == (42.08, "GBP")

    def test_an_unknown_unit_yields_no_quote(self):
        assert Quote.from_provider(4208.0, None) is None
        assert Quote.from_provider(4208.0, "") is None

    def test_an_absent_price_yields_no_quote(self):
        assert Quote.from_provider(None, "GBp") is None

    def test_a_closed_session_is_settled(self):
        q = Quote.from_provider(100.0, "EUR", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert q.settled

    def test_todays_session_is_not_settled(self):
        q = Quote.from_provider(100.0, "EUR", session=self.TODAY,
                                today=self.TODAY)
        assert not q.settled

    def test_no_session_is_not_settled(self):
        assert not Quote.from_provider(100.0, "EUR").settled

    def test_a_quote_converts_through_the_rate_service(self, monkeypatch):
        from market import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: 1.16)
        q = Quote.from_provider(4208.0, "GBp", session=date(2026, 9, 17),
                                today=self.TODAY)
        assert q.converted("EUR", fx).amount == pytest.approx(42.08 * 1.16)

    def test_a_conversion_keeps_the_rate_and_what_it_started_from(
            self, monkeypatch):
        from market import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: 1.16)
        c = Quote.from_provider(4208.0, "GBp").converted("EUR", fx)
        assert c.original.amount == 42.08      # already the major unit
        assert c.original.currency == "GBP"
        assert c.rate == 1.16
        assert c.base == "EUR"

    def test_a_quote_without_a_rate_does_not_convert(self, monkeypatch):
        from market import fx
        monkeypatch.setattr(fx, "rate", lambda c, b, on=None: None)
        assert Quote.from_provider(100.0, "EUR").converted("XXX", fx) is None

    def test_a_quote_is_immutable(self):
        q = Quote.from_provider(100.0, "EUR")
        with pytest.raises(Exception):
            q.price = 1.0


class TestCurrenciesNeeded:
    """Currencies a rate is fetched for."""

    def test_includes_the_cost_currency(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="GE", quantity=1, currency="USD",
                          cost_currency="EUR", avg_cost=100.0)
        assert pm.currencies([pos]) == {"USD", "EUR"}

    def test_a_third_currency_is_not_lost(self):
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
    """Text report P&L total when some positions have no rate."""

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
        assert "GE" in body.split("* incomplete: excludes")[1]

    def test_a_complete_total_is_not_marked(self):
        import portfolio_monitor as pm
        pos = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                          cost_currency="EUR", avg_cost=100.0,
                          current_price=150.0, previous_close=150.0)
        report = pm.calculate_report([pos], "EUR", {"EUR": 1.0}, pm.AlertConfig())
        body = pm.generate_long_report(report)
        assert "TOTAL*" not in body and "* excludes" not in body


class TestPartialPortfolioTotal:
    """Text report total when some positions are unvalued."""

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


class TestDailyChangeCompleteness:
    """Text report daily change when some positions are unvalued."""

    def report(self):
        import portfolio_monitor as pm
        ok = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                         current_price=150.0, previous_close=150.0)
        rateless = pm.Position(ticker="X.QQ", quantity=1, currency="XXX",
                               current_price=300.0, previous_close=100.0)
        return pm, pm.calculate_report([ok, rateless], "EUR", {"EUR": 1.0},
                                       pm.AlertConfig())

    def test_the_daily_change_is_marked_too(self):
        pm, report = self.report()
        body = pm.generate_long_report(report)
        line = next(l for l in body.split("\n") if "Daily Change" in l)
        assert line.rstrip().endswith("*")

    def test_the_note_says_the_daily_change_is_affected(self):
        pm, report = self.report()
        assert "daily change describes only the remaining" in \
            pm.generate_long_report(report)

    def test_a_complete_report_marks_neither(self):
        import portfolio_monitor as pm
        ok = pm.Position(ticker="ALV.DE", quantity=1, currency="EUR",
                         current_price=150.0, previous_close=150.0)
        report = pm.calculate_report([ok], "EUR", {"EUR": 1.0}, pm.AlertConfig())
        body = pm.generate_long_report(report)
        line = next(l for l in body.split("\n") if "Daily Change" in l)
        assert not line.rstrip().endswith("*")
