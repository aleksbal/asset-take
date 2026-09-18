"""Quote units.

The first attempt at this normalised prices inside resolution while the
valuation layer downloaded its own quotes and bypassed the scaling, so the
stored row was right and every snapshot was still a hundredfold out. These
tests cover the shared conversion and both layers that must apply it.
"""
import pytest

import quotes


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

        frame = pd.DataFrame({"Close": [4200.0, 4208.0]})
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
