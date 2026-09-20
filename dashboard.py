#!/usr/bin/env python3
"""Build the report, then draw it.

Two steps on purpose. `views.portfolio` finishes at report.json, which is
the run's output; this page is one program that reads it, and a screen, a
chart or a model would be others. Nothing downstream is required for the
numbers to exist.

No network, no CDN: the page opens offline and contains your data, so it is
gitignored and never published anywhere.
"""
import paths
from render import html
from views import portfolio

OUT = paths.DASHBOARD
REPORT = paths.REPORT


def main():
    report = portfolio.build()
    written = portfolio.write(report, REPORT)
    OUT.write_text(html.render(report), encoding="utf-8")
    print(f"{written}")
    print(f"{OUT}  {report['totals']['positions']} positions, "
          f"{report['totals']['days_recorded']} day(s)")


if __name__ == "__main__":
    main()
