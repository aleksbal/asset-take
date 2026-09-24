"""Tests for the import rules between layers, and for the report/page split."""
import ast

import pytest
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BELOW = {"render": {"views", "holdings", "market"},
         "views": {"holdings", "market"},
         "holdings": {"market"},
         "market": set()}
LAYERS = set(BELOW)


def sources(layer):
    return sorted((ROOT / layer).rglob("*.py"))


def imported(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return out & LAYERS


class TestALayerOnlyUsesLayersBelowIt:
    def test_every_import_points_downwards(self):
        for layer, allowed in BELOW.items():
            for path in sources(layer):
                reaching = imported(path) - allowed - {layer}
                assert not reaching, (
                    f"{path.relative_to(ROOT)} imports {sorted(reaching)}, "
                    f"which is not below {layer}/")

    def test_market_never_learns_that_a_portfolio_exists(self):
        for path in sources("market"):
            assert "holdings" not in imported(path)


class TestOnlyTheRendererKnowsWhatHtmlIs:
    def test_no_markup_outside_render(self):
        for layer in LAYERS - {"render"}:
            for path in sources(layer):
                text = path.read_text()
                for tag in ("<td", "<tr", "<th", "<div", "<span", "<p ",
                            "<svg", "<table", "&mdash;", "&amp;"):
                    assert tag not in text, (
                        f"{path.relative_to(ROOT)} contains {tag!r}; "
                        f"only render/ draws")


class TestARowNeedNotBeHeld:
    """Rendering a row that has no position block."""

    def unheld(self):
        return {"ticker": "X", "name": "X Corp", "priced": True,
                "cost_unconverted": False, "values": {"rsi": 55.0},
                "price": {"amount": 10.0, "currency": "USD"}}

    def test_it_renders_with_every_declared_column(self):
        from market import indicators as ix
        from render import html
        markup = html.table([self.unheld()])
        assert markup.count("<td") == markup.count("<th") - markup.count("<thead")
        for declared in ix.declared():
            assert declared["label"] in markup

    def test_its_parameters_still_show(self):
        from render import html
        assert "55" in html.table([self.unheld()])

    def test_the_ownership_columns_are_absent_not_zero(self):
        from render import html
        markup = html.table([self.unheld()])
        assert ">0<" not in markup and "0.0%" not in markup

    def test_the_allocation_chart_leaves_it_out(self):
        from render import html
        assert 'class="seg"' not in html.donut([self.unheld()])


def test_the_report_can_be_produced_without_a_renderer():
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "views.portfolio"],
                          cwd=ROOT, capture_output=True, text=True)
    assert "ModuleNotFoundError" not in done.stderr, done.stderr
    assert "<" not in done.stdout              # a path, never markup


class TestThePageIsDrawnFromTheFile:
    """The page is rendered from report.json on disk."""

    def report(self, **totals):
        base = {"value": 1234.0, "day": 1.0, "day_pct": 0.1, "pnl": 2.0,
                "pnl_pct": 0.2, "positions": 1, "top5_weight": 100.0,
                "days_recorded": 1}
        base.update(totals)
        return {"as_of": "2026-09-18", "date": "2026-09-18",
                "base_currency": "EUR", "indicators": [], "totals": base,
                "excluded": {"unpriced": [], "cost_unconverted": [],
                             "no_cost": [], "snapshots_not_comparable": 0},
                "series": [], "rows": []}

    def test_what_is_on_disk_is_what_is_drawn(self, tmp_path, monkeypatch):
        import json

        import dashboard
        report = tmp_path / "report.json"
        report.write_text(json.dumps(self.report(value=98765.0)))
        monkeypatch.setattr(dashboard, "REPORT", report)
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")

        dashboard.main(["--from-report"])
        assert "98 765" in (tmp_path / "out.html").read_text()

    def test_it_fetches_nothing_to_do_it(self, tmp_path, monkeypatch):
        import json

        import dashboard
        report = tmp_path / "report.json"
        report.write_text(json.dumps(self.report()))
        monkeypatch.setattr(dashboard, "REPORT", report)
        monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
        monkeypatch.setattr(dashboard.portfolio, "build",
                            lambda: pytest.fail("rebuilt instead of reading"))

        dashboard.main(["--from-report"])

    def test_it_says_so_when_there_is_no_report(self, tmp_path, monkeypatch):
        import dashboard
        monkeypatch.setattr(dashboard, "REPORT", tmp_path / "absent.json")
        with pytest.raises(SystemExit) as raised:
            dashboard.main(["--from-report"])
        assert "no report" in str(raised.value)
