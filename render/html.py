"""The report as a page you can look at.

The only place that knows what HTML is. It takes report.json and nothing
else: no fetching, no arithmetic beyond laying figures out, and no knowledge
of which parameters exist.

Columns come from the report's own `indicators` block, so a parameter added
to the registry appears here without this file being touched. What a
renderer decides is presentation only - decimal places, an em dash for an
absent value, which direction is green - and it reads that from each
parameter's `unit`, never from its name.
"""
import math

from market import indicators as ix

SERIES = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"),
          ("#eda100", "#c98500"), ("#e87ba4", "#d55181"), ("#008300", "#008300"),
          ("#4a3aa7", "#9085e9")]
OTHER = ("#8a8985", "#6f6e6a")


DASH = '<td class="n none">\u2014</td>'


def num(x, dp=0):
    return f"{x:,.{dp}f}".replace(",", " ")


def cell(value, unit):
    """One parameter, formatted by what kind of number it is.

    An absent value is an em dash and never a zero: no data and no movement
    are different claims, and a zero is indistinguishable from a real one.
    """
    if value is None:
        return DASH
    # A value that rounds to 0.0 has no sign worth printing: "-0.0%" and
    # "+0.0%" both claim a direction the displayed magnitude doesn't show.
    if unit in ("percent", "fall", "volume") and round(value, 1) == 0:
        return '<td class="n">0.0%</td>'
    if unit == "percent":
        return f'<td class="n {"up" if value >= 0 else "dn"}">{value:+.1f}%</td>'
    # A fall is never positive, so its sign says nothing and coloring by it
    # would mark every row. Marked only where the drop is large enough to be
    # a claim: colour is one, and a 2% wobble is not.
    if unit == "fall":
        return f'<td class="n {"dn" if value < -5 else ""}">{value:+.1f}%</td>'
    if unit == "days":
        return f'<td class="n none">{value:.0f}d</td>'
    # Volatility is a magnitude, never negative, so a sign would claim a
    # direction it does not have. Volume against its own average is signed,
    # but neither direction is a gain or a loss the way percent's is - both
    # stay uncoloured for the same reason RSI does.
    if unit == "vol":
        return f'<td class="n none">{value:.1f}%</td>'
    if unit == "volume":
        return f'<td class="n none">{value:+.1f}%</td>'
    return f'<td class="n">{value:.0f}</td>'


def line_chart(series, w=760, h=220, pad=(16, 56, 28, 8)):
    """Portfolio value over time. One series, so no legend - the title names it."""
    pts = [(s["date"], s["value"]) for s in series]
    if len(pts) < 2:
        return ('<div class="sparse">One day recorded so far. '
                'The value line appears once a second snapshot exists.</div>')
    top, right, bottom, left = pad
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or (hi * 0.01) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.12
    iw, ih = w - left - right, h - top - bottom

    def xy(i, v):
        x = left + (iw * i / (len(pts) - 1))
        y = top + ih - (v - lo) / (hi - lo) * ih
        return x, y

    coords = [xy(i, v) for i, (_, v) in enumerate(pts)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = path + f" L{coords[-1][0]:.1f},{top+ih} L{coords[0][0]:.1f},{top+ih} Z"
    grid = "".join(
        f'<line class="grid" x1="{left}" x2="{left+iw}" y1="{top+ih*i/3:.1f}" y2="{top+ih*i/3:.1f}"/>'
        f'<text class="axis" x="{left+iw+6}" y="{top+ih*i/3+4:.1f}">{num(hi-(hi-lo)*i/3)}</text>'
        for i in range(4))
    dots = "".join(
        f'<circle class="pt" cx="{x:.1f}" cy="{y:.1f}" r="4" '
        f'data-d="{pts[i][0]}" data-v="{num(pts[i][1], 2)}"/>'
        for i, (x, y) in enumerate(coords))
    first, last = pts[0][0], pts[-1][0]
    return f'''<svg viewBox="0 0 {w} {h}" class="chart" role="img"
      aria-label="Portfolio value from {first} to {last}">
      {grid}
      <path class="area" d="{area}"/><path class="line" d="{path}"/>{dots}
      <text class="axis" x="{left}" y="{h-8}">{first}</text>
      <text class="axis end" x="{left+iw}" y="{h-8}">{last}</text>
      <text class="lbl" x="{coords[-1][0]-6:.1f}" y="{coords[-1][1]-12:.1f}">{num(pts[-1][1])}</text>
    </svg>'''


def donut(rows, size=220, thick=26):
    """Top 7 by weight; the remainder folds into Other. Hues are never cycled."""
    rows = [r for r in rows if (r.get("position") or {}).get("weight") is not None]
    shown = rows[:7]
    rest = sum(r["position"]["weight"] for r in rows[7:])
    slices = [(r["name"], r["position"]["weight"], SERIES[i]) for i, r in enumerate(shown)]
    if rest > 0:
        slices.append((f"Other ({len(rows)-7})", rest, OTHER))
    c, r = size / 2, size / 2 - thick / 2
    segs, a = [], -math.pi / 2
    for name, pct, (lightc, darkc) in slices:
        sweep = 2 * math.pi * pct / 100
        # 2px surface gap between adjacent fills
        gap = min(0.02, sweep * 0.08)
        x1, y1 = c + r * math.cos(a + gap / 2), c + r * math.sin(a + gap / 2)
        x2, y2 = c + r * math.cos(a + sweep - gap / 2), c + r * math.sin(a + sweep - gap / 2)
        large = 1 if sweep - gap > math.pi else 0
        segs.append(
            f'<path class="seg" d="M{x1:.2f},{y1:.2f} A{r:.2f},{r:.2f} 0 {large} 1 {x2:.2f},{y2:.2f}" '
            f'style="--c:{lightc};--cd:{darkc}" data-n="{name}" data-p="{pct:.1f}%"/>')
        a += sweep
    legend = "".join(
        f'<li><span class="sw" style="--c:{lc};--cd:{dc}"></span>'
        f'<span class="ln">{n}</span><span class="lv">{p:.1f}%</span></li>'
        for n, p, (lc, dc) in slices)
    return (f'<svg viewBox="0 0 {size} {size}" class="donut" style="--t:{thick}px" '
            f'role="img" aria-label="Allocation by position">{"".join(segs)}</svg>'
            f'<ul class="legend">{legend}</ul>')



def _row(r, units):
    """One instrument. Parameters in whatever order the report declares them.

    The ownership columns - quantity, value, weight, day, P&L - come from the
    row's `position`, which a row need not have. An instrument nobody owns
    renders every one of them as an em dash rather than a zero, and its
    parameters render exactly as a held one's do. That is the shape a
    market-wide view emits, so the renderer must not assume the block is
    there.
    """
    held = r.get("position") or {}
    name = (f'<td class="nm" title="{r["name"]}">{r["name"]}'
            f'<span class="tk">{r["ticker"]}</span></td>')
    qty = (f'<td class="n">{held["quantity"]:g}</td>' if "quantity" in held
           else DASH)
    if not r.get("priced"):
        # Only the price-derived ownership columns are unavailable here -
        # value, weight, day and P&L all need today's price or FX rate.
        # The trend parameters come from the stored series, independent of
        # today's fetch, so volume_trend in particular can still be there
        # for a row with no price at all; spanning past them would hide it.
        return (f'<tr class="unpriced">{name}{qty}'
                f'<td class="n" colspan="5">no price available</td>'
                + "".join(cell(r["values"].get(k), u) for k, u in units)
                + '</tr>')
    value, weight = held.get("value"), held.get("weight")
    day, pnl = held.get("day_pct"), held.get("pnl")
    return (f'<tr>{name}{qty}'
            f'<td class="n">{num(r["price"]["amount"], 2)} '
            f'{r["price"]["currency"]}</td>'
            + (f'<td class="n">{num(value)}</td>' if value is not None else DASH)
            + (f'<td class="n w"><span class="bar" style="--p:{weight:.1f}%">'
               f'</span>{weight:.1f}%</td>' if weight is not None else DASH)
            + (f'<td class="n {"up" if day >= 0 else "dn"}">{day:+.2f}%</td>'
               if day is not None else DASH)
            + (f'<td class="n {"up" if pnl >= 0 else "dn"}">{num(pnl)} '
               f'({held["pnl_pct"]:+.1f}%)</td>' if pnl is not None else DASH)
            + "".join(cell(r["values"].get(k), u) for k, u in units)
            + '</tr>')


def table(rows, declared=None):
    """Headers from the report's own declaration, never from a name here.

    Defaults to the registry so a caller holding only rows still renders
    every parameter; a report carries its own list, which is what lets a
    file written by an older run be drawn with the columns it actually has.
    """
    declared = ix.declared() if declared is None else declared
    units = [(d["key"], d["unit"]) for d in declared]
    heads = "".join(
        f'<th class="n"><span class="tip" tabindex="0" data-tip="{d["means"]}">'
        f'{d["label"]}</span></th>' for d in declared)
    body = "".join(_row(r, units) for r in rows)
    return f"""<div class="scroll"><table><thead><tr>
      <th>Position</th><th class="n">Qty</th><th class="n">Price</th>
      <th class="n">Value</th><th class="n">Weight</th><th class="n">Day</th>
      <th class="n"><span class="tip" tabindex="0" data-tip="unrealised gain or loss against cost basis, converted to the portfolio's base currency; positions stating no cost basis are excluded rather than counted as zero">P&amp;L</span></th>{heads}
    </tr></thead><tbody>{body}</tbody></table></div>"""


def notes(report):
    """Everything the figures do not say for themselves.

    Built as a list rather than a chain of conditional concatenations. The
    chain read `A + B + C if no_cost else ""`, which Python groups as
    `(A + B + C) if no_cost else ""` - so a portfolio where every position
    stated a cost basis suppressed the others, including the one saying
    positions could not be priced and the total is understated. Exactly the
    warning that must never be the one to go missing.
    """
    left = report["excluded"]

    def plural(items, s="s", one=""):
        return s if len(items) > 1 else one

    out = []
    if left["unpriced"]:
        n = left["unpriced"]
        out.append(f'<p class="note"><b>{len(n)} position{plural(n)} could not '
                   f'be priced</b> ({", ".join(n)}) and {plural(n, "are", "is")} '
                   f'excluded from the total, which is therefore understated.</p>')
    if left["cost_unconverted"]:
        n = left["cost_unconverted"]
        out.append(f'<p class="note"><b>{len(n)} position{plural(n)} '
                   f'state{plural(n, "", "s")} a cost basis in a currency with '
                   f'no available rate</b> ({", ".join(n)}), so the unrealised '
                   f'P&amp;L above excludes {plural(n, "them", "it")}. A P&amp;L '
                   f'can be negative, so {plural(n, "they", "it")} could move '
                   f'that figure either way.</p>')
    if left["no_cost"]:
        n = left["no_cost"]
        out.append(f'<p class="note">{len(n)} position{plural(n)} '
                   f'state{plural(n, "", "s")} no cost basis ({", ".join(n)}), '
                   f'so P&amp;L excludes {plural(n, "them", "it")}.</p>')
    dropped = left["snapshots_not_comparable"]
    if dropped:
        out.append(f'<p class="note">{dropped} earlier snapshot'
                   f'{"s are" if dropped > 1 else " is"} left out of the '
                   f'value chart, as {"they are" if dropped > 1 else "it is"} '
                   f'either denominated in another currency or '
                   f'{"were" if dropped > 1 else "was"} taken while a position '
                   f'could not be valued.</p>')
    return out


def render(report):
    """The whole page, as a string."""
    t = report["totals"]
    base = report["base_currency"]
    pnl, pnl_pct = t["pnl"], t["pnl_pct"]
    no_rate = report["excluded"]["cost_unconverted"]
    return TEMPLATE.format(
        generated=report["as_of"],
        days=t["days_recorded"],
        total=num(t["value"], 2),
        base=base,
        day_base=f'{t["day"]:+,.0f}'.replace(",", " "),
        day_pct=f'{t["day_pct"]:+.2f}',
        day_cls="up" if t["day"] >= 0 else "dn",
        pnl=f'{pnl:+,.0f}'.replace(",", " ") if pnl is not None else "&mdash;",
        # Three different states, and the wrong one was the default. No
        # position converted is not the same as no position stating a basis:
        # the second is a fact about the holdings, the first about our rates,
        # and saying the second contradicts the note directly below.
        pnl_sub=((f'{pnl_pct:+.1f}% on cost'
                  + (f' · excludes {len(no_rate)}' if no_rate else ''))
                 if pnl_pct is not None
                 else ("no rate for the cost basis" if no_rate
                       else "no cost basis recorded")),
        pnl_cls="up" if (pnl or 0) >= 0 else "dn",
        n=t["positions"], top5=f'{t["top5_weight"]:.0f}',
        caveat="".join(notes(report)),
        chart=line_chart(report["series"]),
        donut=donut(report["rows"]),
        table=table(report["rows"], report["indicators"]),
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio</title><style>
:root {{
  --surface-0:#f6f6f4; --surface-1:#fcfcfb; --line:#e4e3de;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#82817c;
  --up:#008300; --dn:#e34948;
  color-scheme:light;
}}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme="light"]) {{
  --surface-0:#111110; --surface-1:#1a1a19; --line:#2f2f2d;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8b8a82;
  --up:#3fae4e; --dn:#e66767;
  color-scheme:dark;
}} }}
:root[data-theme="dark"] {{
  --surface-0:#111110; --surface-1:#1a1a19; --line:#2f2f2d;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8b8a82;
  --up:#3fae4e; --dn:#e66767; color-scheme:dark;
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 16px 64px;background:var(--surface-0);color:var(--text-primary);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;}}
.wrap{{max-width:1040px;margin:0 auto}}
h1{{font-size:15px;font-weight:600;letter-spacing:.02em;color:var(--text-secondary);
  margin:0 0 20px;text-transform:uppercase}}
h2{{font-size:13px;font-weight:600;color:var(--text-secondary);margin:0 0 14px;
  text-transform:uppercase;letter-spacing:.04em}}
.card{{background:var(--surface-1);border:1px solid var(--line);border-radius:12px;
  padding:20px;margin-bottom:16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px}}
.tile .k{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}}
.tile .v{{font-size:27px;font-weight:650;letter-spacing:-.02em;margin-top:4px;
  font-variant-numeric:tabular-nums}}
.tile .s{{font-size:12px;color:var(--text-muted);margin-top:2px}}
.up{{color:var(--up)}} .dn{{color:var(--dn)}}
.cols{{display:grid;grid-template-columns:1fr;gap:16px}}
@media(min-width:860px){{.cols{{grid-template-columns:1.55fr 1fr}}}}
.chart{{width:100%;height:auto;overflow:visible}}
.grid{{stroke:var(--line);stroke-width:1}}
.axis{{fill:var(--text-muted);font-size:10px}} .axis.end{{text-anchor:end}}
.line{{fill:none;stroke:#2a78d6;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.area{{fill:#2a78d6;opacity:.09;stroke:none}}
.pt{{fill:#2a78d6;stroke:var(--surface-1);stroke-width:2;opacity:0;transition:opacity .12s}}
.pt:hover,.chart:hover .pt{{opacity:1}}
.lbl{{fill:var(--text-primary);font-size:12px;font-weight:600;text-anchor:end}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]) .line,
  :root:not([data-theme="light"]) .area,:root:not([data-theme="light"]) .pt{{stroke:#3987e5}}
  :root:not([data-theme="light"]) .area{{fill:#3987e5}}
  :root:not([data-theme="light"]) .pt{{fill:#3987e5;stroke:var(--surface-1)}}}}
.sparse{{color:var(--text-muted);font-size:13px;padding:34px 0;text-align:center;
  border:1px dashed var(--line);border-radius:8px}}
.alloc{{display:flex;gap:20px;align-items:center;flex-wrap:wrap}}
.donut{{width:180px;height:180px;flex:none}}
.seg{{fill:none;stroke:var(--c);stroke-width:var(--t)}}
.seg:hover{{stroke-width:calc(var(--t) + 4px)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]) .seg{{stroke:var(--cd)}}}}
.legend{{list-style:none;margin:0;padding:0;font-size:12px;flex:1;min-width:170px}}
.legend li{{display:flex;align-items:center;gap:8px;padding:3px 0}}
.sw{{width:10px;height:10px;border-radius:3px;background:var(--c);flex:none}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]) .sw{{background:var(--cd)}}}}
.ln{{flex:1;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.lv{{font-variant-numeric:tabular-nums;color:var(--text-primary);font-weight:600}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);
  font-weight:600;padding:0 10px 8px;border-bottom:1px solid var(--line);text-align:left}}
td{{padding:9px 10px;border-bottom:1px solid var(--line)}}
tbody tr:hover{{background:var(--surface-0)}}
.n{{text-align:right;white-space:nowrap}}
.scroll{{overflow-x:auto;margin:0 -4px;padding:0 4px}}
.nm{{font-weight:500;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .tk{{display:block;font-size:11px;color:var(--text-muted);font-weight:400}}
/* A block inside the drawdown cell, never on a cell itself: display:block
   on a td drops it out of table layout and shears the row sideways. */
.peak-age{{display:block;font-size:11px;color:var(--text-muted);font-weight:400}}
.none{{color:var(--text-muted)}}
.tip{{cursor:help;border-bottom:1px dotted var(--text-muted)}}
.w{{position:relative}}
.bar{{position:absolute;left:10px;right:10px;bottom:3px;height:2px;background:var(--line);
  border-radius:2px}}
.bar::after{{content:"";position:absolute;left:0;top:0;height:100%;width:var(--p);
  background:#2a78d6;border-radius:2px}}
.note{{font-size:12px;color:var(--text-muted);margin:12px 0 0}}
.foot{{color:var(--text-muted);font-size:11px;margin-top:20px}}
#tip{{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);
  padding:5px 9px;border-radius:6px;font-size:12px;opacity:0;transition:opacity .1s;z-index:9}}
</style></head><body><div class="wrap">

<h1>Portfolio</h1>

<div class="card tiles">
  <div class="tile"><div class="k">Total value</div>
    <div class="v">{total}<span style="font-size:15px;color:var(--text-muted)"> {base}</span></div>
    <div class="s">{n} positions · top 5 = {top5}%</div></div>
  <div class="tile"><div class="k">Today</div>
    <div class="v {day_cls}">{day_pct}%</div><div class="s">{day_base} {base}</div></div>
  <div class="tile"><div class="k">Unrealised P&amp;L</div>
    <div class="v {pnl_cls}">{pnl}</div><div class="s">{pnl_sub}</div></div>
</div>

<div class="cols">
  <div class="card"><h2>Value over time</h2>{chart}</div>
  <div class="card"><h2>Allocation</h2><div class="alloc">{donut}</div></div>
</div>

<div class="card"><h2>Positions</h2>{table}{caveat}</div>

<p class="foot">{generated} · {days} day(s) recorded · prices via Yahoo Finance,
which does not carry ING's Direkthandel venue, so totals differ from ING by ~0.1%.</p>

</div><div id="tip"></div><script>
const tip=document.getElementById('tip');
function show(e,html){{tip.innerHTML=html;tip.style.opacity=1;
  tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-10)+'px';
  tip.style.top=(e.clientY-34)+'px';}}
function hide(){{tip.style.opacity=0;}}
document.querySelectorAll('.seg').forEach(s=>{{
  s.addEventListener('mousemove',e=>show(e,`<b>${{s.dataset.n}}</b> ${{s.dataset.p}}`));
  s.addEventListener('mouseleave',hide);}});
document.querySelectorAll('.pt').forEach(p=>{{
  p.addEventListener('mousemove',e=>show(e,`<b>${{p.dataset.d}}</b> ${{p.dataset.v}} {base}`));
  p.addEventListener('mouseleave',hide);}});
document.querySelectorAll('.tip').forEach(t=>{{
  t.addEventListener('mousemove',e=>show(e,t.dataset.tip));
  t.addEventListener('mouseleave',hide);
  t.addEventListener('focus',()=>{{const r=t.getBoundingClientRect();
    show({{clientX:r.left,clientY:r.bottom}},t.dataset.tip);}});
  t.addEventListener('blur',hide);}});
</script></body></html>
"""
