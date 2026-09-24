#!/usr/bin/env python3
"""Builds report.json, then renders dashboard.html from that file.

    dashboard.py                  value what is held now, then draw it
    dashboard.py --from-report    draw the report on disk, fetch nothing

The page works offline and contains your data; it is gitignored.
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
    """Command-line entry point. `argv` defaults to no arguments, not
    sys.argv."""
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
