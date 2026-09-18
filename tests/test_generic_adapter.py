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


class TestNumberLocale:
    """`1,234` is 1234 in English and 1.234 in German, and the file delimiter
    cannot settle it - CSV quoting permits a comma inside a field. The locale
    is inferred from all of the file's numbers together, and applied uniformly.
    """

    def test_english_grouping_inferred_from_a_corroborating_value(self, tmp_path):
        """`1,234.56` can only be English, which settles `1,234` too."""
        p = write(tmp_path, 'ticker,quantity,avg_cost\nAAPL,"1,234","1,234.56"\n')
        h = generic.parse(p)[0]
        assert h.quantity == 1234
        assert h.avg_cost == pytest.approx(1234.56)

    def test_quoted_german_decimal_in_a_comma_delimited_file(self, tmp_path):
        """The inverse corruption: a comma-delimited file may quote `12,34`."""
        p = write(tmp_path, 'ticker,quantity\nSAP.DE,"12,34"\n')
        assert generic.parse(p)[0].quantity == pytest.approx(12.34)

    def test_a_lone_ambiguous_value_is_not_guessed_at(self, tmp_path):
        """`1,234` alone carries no evidence. The heuristic reads it as a
        decimal; inventing a locale from the delimiter would corrupt the
        German case instead. Documented, not asserted as correct."""
        p = write(tmp_path, 'ticker,quantity\nAAPL,"1,234"\n')
        assert generic.parse(p)[0].quantity == pytest.approx(1.234)

    def test_one_row_settles_the_locale_for_the_whole_file(self, tmp_path):
        p = write(tmp_path, 'ticker,quantity\nSAP.DE,"12,34"\nAAPL,"1,234"\n')
        assert [h.quantity for h in generic.parse(p)] == [
            pytest.approx(12.34), pytest.approx(1.234)]

    def test_decimal_comma_still_works_in_a_semicolon_file(self, tmp_path):
        """German exports use semicolons precisely so the comma stays free."""
        p = write(tmp_path, "ticker;quantity;avg_cost\nSAP.DE;18;245,8172\n")
        h = generic.parse(p)[0]
        assert h.quantity == 18
        assert h.avg_cost == pytest.approx(245.8172)

    def test_german_thousands_and_decimal_in_a_semicolon_file(self, tmp_path):
        p = write(tmp_path, "ticker;quantity;avg_cost\nSAP.DE;18;1.234,56\n")
        assert generic.parse(p)[0].avg_cost == pytest.approx(1234.56)

    def test_plain_decimal_point_is_unaffected(self, tmp_path):
        p = write(tmp_path, "ticker,quantity\nAAPL,30.5\n")
        assert generic.parse(p)[0].quantity == pytest.approx(30.5)


class TestExplicitTicker:
    """A file naming an exchange listing means that listing, not whatever a
    search for the ISIN returns first."""

    def test_ticker_is_kept_alongside_the_isin(self, tmp_path):
        p = write(tmp_path, "isin,ticker,quantity\nIE00B4L5Y983,EUNL.DE,100\n")
        h = generic.parse(p)[0]
        assert h.isin == "IE00B4L5Y983"
        assert h.ticker == "EUNL.DE"

    def test_ticker_only_file_still_works(self, tmp_path):
        p = write(tmp_path, "ticker,quantity\nEUNL.DE,100\n")
        h = generic.parse(p)[0]
        assert h.ticker == "EUNL.DE"
        assert h.isin == "EUNL.DE"


class TestFileDiscovery:
    """Default discovery must match what adapters accept, or a supported
    export in the imports directory is reported as absent."""

    @pytest.mark.parametrize("suffix", [".csv", ".tsv", ".txt"])
    def test_accepted_suffixes_are_discoverable(self, tmp_path, suffix):
        import import_holdings
        (tmp_path / f"export{suffix}").write_text("ticker\tquantity\nSAP.DE\t18\n")
        assert import_holdings.pick_file(tmp_path).suffix == suffix

    def test_declared_suffixes_match_what_detect_accepts(self, tmp_path):
        for suffix in generic.SUFFIXES:
            p = tmp_path / f"x{suffix}"
            p.write_text("ticker,quantity\nSAP.DE,18\n")
            assert generic.detect(p), f"{suffix} declared but not detected"


class TestRowsWithoutAnIdentifier:
    """A quantity alone does not make a holding. Exports commonly end in a
    subtotal line carrying a summed quantity and no instrument; admitting it
    creates a holding keyed on the empty string, and several such rows
    overwrite one another downstream."""

    def test_footer_row_is_skipped(self, tmp_path):
        p = write(tmp_path, "isin,ticker,name,quantity\n"
                            "IE00B4L5Y983,EUNL.DE,Core World,100\n"
                            ",,Total,100\n")
        holdings = generic.parse(p)
        assert [h.isin for h in holdings] == ["IE00B4L5Y983"]

    def test_no_holding_is_keyed_on_an_empty_identifier(self, tmp_path):
        p = write(tmp_path, "isin,ticker,quantity\n,,250\nSAP.DE,,18\n")
        assert all(h.isin for h in generic.parse(p))
