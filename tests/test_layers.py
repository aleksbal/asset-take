"""The layering, enforced rather than agreed to.

A rule that lives only in a document erodes the first time someone is in a
hurry, and the erosion is invisible in review: one convenient import, and
the next person has a precedent. These two tests fail instead.

    render -> views -> holdings -> market

`holdings` sits above `market` rather than beside it: resolving a broker
export to a listing means fetching a price and checking it against the
broker's own, so it genuinely needs what `market` knows. The direction that
matters is the other one - `market` must never learn that a portfolio
exists, which is what lets a market-wide view reuse it unchanged, and that
has a test of its own.

`paths` sits below all of them. It imports nothing from the project, so
everything may read it without reaching sideways or up.
"""
import ast
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
    """Every project package this file imports, however it spells it."""
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
        """What makes a market-wide view able to reuse this code unchanged."""
        for path in sources("market"):
            assert "holdings" not in imported(path)


class TestOnlyTheRendererKnowsWhatHtmlIs:
    def test_no_markup_outside_render(self):
        """Markup below `render/` is a view that has started drawing.

        Checked as a tag rather than a bare `<`, so a comparison or a type
        annotation is not mistaken for one.
        """
        for layer in LAYERS - {"render"}:
            for path in sources(layer):
                text = path.read_text()
                for tag in ("<td", "<tr", "<th", "<div", "<span", "<p ",
                            "<svg", "<table", "&mdash;", "&amp;"):
                    assert tag not in text, (
                        f"{path.relative_to(ROOT)} contains {tag!r}; "
                        f"only render/ draws")


class TestARowNeedNotBeHeld:
    """The shape a market-wide view will emit: an instrument with a price and
    parameters, and no position at all. Nothing in `render/` may assume the
    ownership block is there, or the claim that both views share one shape is
    only true until something reads the file."""

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
        """A zero quantity is a claim about a holding; there is no holding."""
        from render import html
        markup = html.table([self.unheld()])
        assert ">0<" not in markup and "0.0%" not in markup

    def test_the_allocation_chart_leaves_it_out(self):
        """It has no weight, because weight is a share of something owned."""
        from render import html
        assert 'class="seg"' not in html.donut([self.unheld()])


def test_the_report_can_be_produced_without_a_renderer():
    """`python -m views.portfolio` writes the report and draws nothing.

    The claim that a run finishes at report.json is only true if it can
    finish there. Run as a path rather than a module it cannot: Python puts
    views/ on sys.path instead of the repository root and `import paths`
    fails before __main__ is reached, which is why the module form is the
    documented one.

    Asserted on the import rather than the output, so a checkout with no
    snapshots yet still exercises what this is about. Declining for want of
    a snapshot is a working entry point; not resolving `paths` is not.
    """
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "views.portfolio"],
                          cwd=ROOT, capture_output=True, text=True)
    assert "ModuleNotFoundError" not in done.stderr, done.stderr
    assert "<" not in done.stdout              # a path, never markup
