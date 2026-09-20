#!/usr/bin/env python3
"""Build the report, then draw what was written.

Two steps on purpose. `views.portfolio` finishes at report.json, which is the
run's output; this page is one program that reads it, and a screen, a chart
or a model would be others.

The page is rendered from the file rather than from the report still in
memory, so the claim is demonstrated rather than asserted. It also makes the
two paths one: a figure that does not survive the round trip through JSON
would otherwise be right on a fresh run and wrong on every later read, and
nothing in a test of `build()` would show it.

    dashboard.py                  value what is held now, then draw it
    dashboard.py --from-report    draw the report on disk, fetch nothing

The second is what makes a complete report still usable after the holdings
have moved on: `build()` refuses to blend vintages, and refusing is right,
but a report already written is a finished fact about the day it was taken.

No network, no CDN: the page opens offline and contains your data, so it is
gitignored and never published anywhere.
"""
import argparse
import json
import sys

import paths
from render import html
from views import portfolio

OUT = paths.DASHBOARD
REPORT = paths.REPORT


def main(argv=()):
    """`argv` defaults to nothing rather than to sys.argv: called in-process
    it would otherwise parse whatever arguments the caller was started with,
    which is how the test runner's own flags reached this parser."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-report", action="store_true",
                    help="draw report.json as it stands; do not rebuild it")
    args = ap.parse_args(argv)

    if args.from_report:
        if not REPORT.exists():
            raise SystemExit(f"no report at {REPORT} - run dashboard.py first")
    else:
        print(portfolio.write(portfolio.build(), REPORT))

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    OUT.write_text(html.render(report), encoding="utf-8")
    print(f"{OUT}  {report['totals']['positions']} positions, "
          f"{report['totals']['days_recorded']} day(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
