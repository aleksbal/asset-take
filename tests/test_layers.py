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
