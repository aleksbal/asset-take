"""The ING adapter against a synthetic export in the real format.

The fixture reproduces every quirk of a genuine Depotübersicht — cp1252
encoding, a five-line preamble, four columns named Währung, German decimals,
and a Depot-Gesamtwert trailer — with invented holdings.

The strongest assertion available is reconciliation: the export states its own
total, so a parse that does not reproduce it to the cent is wrong.
"""
from pathlib import Path

import pytest

import adapters
from adapters import ing

FIXTURE = Path(__file__).parent / "fixtures" / "ing_depotuebersicht.csv"
STATED_TOTAL = 19781.88  # the Depot-Gesamtwert row


def test_detects_own_format():
    assert adapters.detect(FIXTURE) is ing


def test_rejects_unrelated_csv(tmp_path):
    other = tmp_path / "something.csv"
    other.write_text("a,b\n1,2\n")
    assert adapters.detect(other) is None


def test_detection_ignores_filename(tmp_path):
    """Exports arrive under whatever name the broker chose."""
    renamed = tmp_path / "arbitrary-name-2026.csv"
    renamed.write_bytes(FIXTURE.read_bytes())
    assert adapters.detect(renamed) is ing


def test_reconciles_to_stated_total():
    holdings = ing.parse(FIXTURE)
    computed = sum(h.quantity * h.broker_price for h in holdings)
    assert computed == pytest.approx(STATED_TOTAL, abs=0.01)


def test_excludes_preamble_and_trailer():
    holdings = ing.parse(FIXTURE)
    assert len(holdings) == 4
    assert all(len(h.isin) == 12 for h in holdings)
    assert not any("Gesamtwert" in h.name for h in holdings)


def test_parses_german_decimals():
    h = next(x for x in ing.parse(FIXTURE) if x.isin == "IE00B4L5Y983")
    assert h.quantity == 100
    assert h.avg_cost == pytest.approx(82.368)
    assert h.broker_price == pytest.approx(127.42)


def test_reads_cp1252_umlauts():
    """A mis-decoded file yields replacement characters, not an exception."""
    assert "�" not in "".join(h.name for h in ing.parse(FIXTURE))


def test_combines_report_date_with_row_time():
    h = ing.parse(FIXTURE)[0]
    assert h.broker_as_of == "2026-09-17T18:40"


def test_tags_its_source():
    assert {h.source for h in ing.parse(FIXTURE)} == {"ing"}
