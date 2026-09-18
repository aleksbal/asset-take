"""The generic CSV fallback.

A second adapter exists mainly to test a claim the architecture makes: that a
broker is one new file and nothing else changes. It was not true. `Holding`
had been written against a single export and assumed a broker-supplied
valuation, which only a specific adapter can provide.
"""
import pytest

import adapters
from adapters import generic, ing


def write(tmp_path, text, name="export.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return p


def test_reads_a_plain_csv(tmp_path):
    p = write(tmp_path, "ticker,quantity,currency,avg_cost\n"
                        "IWDA.AS,30,EUR,70.42\nALV.DE,56,EUR,228.59\n")
    holdings = generic.parse(p)
    assert [h.isin for h in holdings] == ["IWDA.AS", "ALV.DE"]
    assert holdings[0].quantity == 30
    assert holdings[1].avg_cost == pytest.approx(228.59)


@pytest.mark.parametrize("header", [
    "isin,quantity", "symbol,shares", "ticker,anzahl",
    "ISIN,Stück", "wkn,units",
])
def test_accepts_common_column_names(tmp_path, header):
    p = write(tmp_path, f"{header}\nIE00B4L5Y983,10\n")
    assert generic.detect(p)
    assert generic.parse(p)[0].quantity == 10


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_accepts_common_delimiters(tmp_path, delimiter):
    p = write(tmp_path, f"ticker{delimiter}quantity\nSAP.DE{delimiter}18\n")
    assert generic.detect(p)
    assert generic.parse(p)[0].quantity == 18


def test_declines_a_csv_without_holdings(tmp_path):
    assert not generic.detect(write(tmp_path, "date,note\n2026-01-01,hello\n"))


def test_states_no_valuation(tmp_path):
    """The distinguishing property: nothing to verify a ticker against."""
    p = write(tmp_path, "ticker,quantity\nIWDA.AS,30\n")
    assert generic.parse(p)[0].broker_price is None


def test_skips_rows_without_a_quantity(tmp_path):
    p = write(tmp_path, "ticker,quantity\nIWDA.AS,30\nTOTAL,\nALV.DE,0\n")
    assert [h.isin for h in generic.parse(p)] == ["IWDA.AS"]


def test_defaults_currency_when_absent(tmp_path):
    p = write(tmp_path, "ticker,quantity\nIWDA.AS,30\n")
    assert generic.parse(p)[0].currency == "EUR"


def test_tags_its_source(tmp_path):
    p = write(tmp_path, "ticker,quantity\nIWDA.AS,30\n")
    assert generic.parse(p)[0].source == "generic"


class TestPrecedence:
    """A generic reader recognises files a specific adapter parses better."""

    @pytest.fixture
    def contested(self, tmp_path):
        """A header both adapters claim: ING's signature, generic's aliases."""
        p = tmp_path / "contested.csv"
        header = ("ISIN;Wertpapiername;Stück/Nominale;Einheitskennzeichen;"
                  "Einstandskurs;Währung;Einstandswert;Währung;Bewertungskurs;"
                  "Währung;Zeit;Handelsplatz;Kurswert;Währung")
        p.write_text(f"{header}\n"
                     "IE00B4L5Y983;ISHSIII-CORE MSCI WLD DLA;100;Stück;82,368;EUR;"
                     "8.236,80;EUR;127,42;EUR;18:40 Uhr;Direkthandel;12.742,00;EUR\n",
                     encoding="cp1252")
        return p

    def test_both_would_claim_it(self, contested):
        assert ing.detect(contested)
        assert generic.detect(contested)

    def test_specific_adapter_wins(self, contested):
        assert adapters.detect(contested) is ing

    def test_generic_is_registered_as_a_fallback(self):
        assert generic in adapters.FALLBACK
        assert generic not in adapters.SPECIFIC
        assert adapters.ADAPTERS.index(generic) == len(adapters.ADAPTERS) - 1
