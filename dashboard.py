#!/usr/bin/env python3
"""Render history/ + holdings into a self-contained dashboard.html.

No network, no CDN: the file opens offline and contains your data, so it is
gitignored and never published anywhere.
"""
import csv
import json
import math
from datetime import date
from pathlib import Path

import canonical
import paths

ROOT = Path(__file__).parent
OUT = paths.DASHBOARD

# Validated categorical palette (light / dark), fixed order, never cycled.
SERIES = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"),
          ("#eda100", "#c98500"), ("#e87ba4", "#d55181"), ("#008300", "#008300"),
          ("#4a3aa7", "#9085e9")]
OTHER = ("#8a8985", "#6f6e6a")


def load():
    snaps = []
    for f in sorted(paths.HISTORY.glob("*.json")):
        snaps.append(json.loads(f.read_text()))
    held = {h.isin: h for h in canonical.read(paths.HOLDINGS)}
    tmap = {r["isin"]: r for r in csv.DictReader(paths.ISIN_MAP.open())}
    by_ticker = {r["ticker"]: held[i] for i, r in tmap.items() if i in held and r["ticker"]}
    names = {r["ticker"]: (r.get("display_name") or "").strip() for r in tmap.values() if r.get("ticker")}
    return snaps, by_ticker, names


def positions(snap, by_ticker, names):
    rows = []
    total = snap["total_value"]
    for p in snap["positions"]:
        h = by_ticker.get(p["ticker"])
        fx = snap["fx_rates"].get(p["currency"], 1)
        value = p["quantity"] * p["current_price"] * fx
        prev = p["quantity"] * (p.get("previous_close") or p["current_price"]) * fx
        cost = (h.quantity * h.avg_cost) if (h and h.avg_cost) else None
        rows.append({
            "name": names.get(p["ticker"]) or (h.name if h else p["ticker"]),
            "ticker": p["ticker"], "qty": p["quantity"], "price": p["current_price"],
            "currency": p["currency"], "value": value, "weight": value / total * 100,
            "day_eur": value - prev,
            "day_pct": (value / prev - 1) * 100 if prev else 0,
            "pnl": (value - cost) if cost else None,
            "pnl_pct": ((value / cost - 1) * 100) if cost else None,
        })
    rows.sort(key=lambda r: -r["value"])
    return rows


def eur(x, dp=0):
    return f"{x:,.{dp}f}".replace(",", " ")


def line_chart(snaps, w=760, h=220, pad=(16, 56, 28, 8)):
    """Portfolio value over time. One series, so no legend - the title names it."""
    pts = [(s["date"], s["total_value"]) for s in snaps]
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
        f'<text class="axis" x="{left+iw+6}" y="{top+ih*i/3+4:.1f}">{eur(hi-(hi-lo)*i/3)}</text>'
        for i in range(4))
    dots = "".join(
        f'<circle class="pt" cx="{x:.1f}" cy="{y:.1f}" r="4" '
        f'data-d="{pts[i][0]}" data-v="{eur(pts[i][1], 2)}"/>'
        for i, (x, y) in enumerate(coords))
    first, last = pts[0][0], pts[-1][0]
    return f'''<svg viewBox="0 0 {w} {h}" class="chart" role="img"
      aria-label="Portfolio value from {first} to {last}">
      {grid}
      <path class="area" d="{area}"/><path class="line" d="{path}"/>{dots}
      <text class="axis" x="{left}" y="{h-8}">{first}</text>
      <text class="axis end" x="{left+iw}" y="{h-8}">{last}</text>
      <text class="lbl" x="{coords[-1][0]-6:.1f}" y="{coords[-1][1]-12:.1f}">{eur(pts[-1][1])}</text>
    </svg>'''


def donut(rows, size=220, thick=26):
    """Top 7 by weight; the remainder folds into Other. Hues are never cycled."""
    shown = rows[:7]
    rest = sum(r["weight"] for r in rows[7:])
    slices = [(r["name"], r["weight"], SERIES[i]) for i, r in enumerate(shown)]
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


def table(rows):
    body = "".join(
        f'<tr><td class="nm" title="{r["name"]}">{r["name"]}<span class="tk">{r["ticker"]}</span></td>'
        f'<td class="n">{r["qty"]:g}</td>'
        f'<td class="n">{eur(r["price"], 2)} {r["currency"]}</td>'
        f'<td class="n">{eur(r["value"])}</td>'
        f'<td class="n w"><span class="bar" style="--p:{r["weight"]:.1f}%"></span>{r["weight"]:.1f}%</td>'
        f'<td class="n {"up" if r["day_pct"]>=0 else "dn"}">{r["day_pct"]:+.2f}%</td>'
        f'<td class="n {"up" if (r["pnl"] or 0)>=0 else "dn"}">'
        f'{(eur(r["pnl"]) + " (" + format(r["pnl_pct"], "+.1f") + "%)") if r["pnl"] is not None else "—"}</td></tr>'
        for r in rows)
    return f'''<table><thead><tr>
      <th>Position</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Value</th>
      <th class="n">Weight</th><th class="n">Day</th><th class="n">P&amp;L</th>
    </tr></thead><tbody>{body}</tbody></table>'''


def main():
    snaps, by_ticker, names = load()
    if not snaps:
        raise SystemExit("no snapshots in history/ — run snapshot.py first")
    latest = snaps[-1]
    rows = positions(latest, by_ticker, names)
    total = latest["total_value"]
    pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
    cost = sum(r["value"] - r["pnl"] for r in rows if r["pnl"] is not None)
    no_cost = [r for r in rows if r["pnl"] is None]
    top5 = sum(r["weight"] for r in rows[:5])

    OUT.write_text(TEMPLATE.format(
        generated=latest.get("taken_at", latest["date"]),
        days=len(snaps),
        total=eur(total, 2),
        day_eur=f'{latest["daily_change"]:+,.0f}'.replace(",", " "),
        day_pct=f'{latest["daily_change_pct"]:+.2f}',
        day_cls="up" if latest["daily_change"] >= 0 else "dn",
        pnl=f'{pnl:+,.0f}'.replace(",", " "),
        pnl_pct=f'{pnl/cost*100:+.1f}' if cost else "—",
        pnl_cls="up" if pnl >= 0 else "dn",
        n=len(rows), top5=f"{top5:.0f}",
        caveat=(f'<p class="note">{len(no_cost)} position'
                f'{"s" if len(no_cost)>1 else ""} priced in another currency than '
                f'the cost basis ({", ".join(r["ticker"] for r in no_cost)}), so '
                f'P&amp;L excludes {"them" if len(no_cost)>1 else "it"}.</p>')
               if no_cost else "",
        chart=line_chart(snaps),
        donut=donut(rows),
        table=table(rows),
    ), encoding="utf-8")
    print(f"{OUT}  {len(rows)} positions, {len(snaps)} day(s)")


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
.n{{text-align:right}}
.nm{{font-weight:500;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .tk{{display:block;font-size:11px;color:var(--text-muted);font-weight:400}}
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
    <div class="v">{total}<span style="font-size:15px;color:var(--text-muted)"> EUR</span></div>
    <div class="s">{n} positions · top 5 = {top5}%</div></div>
  <div class="tile"><div class="k">Today</div>
    <div class="v {day_cls}">{day_pct}%</div><div class="s">{day_eur} EUR</div></div>
  <div class="tile"><div class="k">Unrealised P&amp;L</div>
    <div class="v {pnl_cls}">{pnl}</div><div class="s">{pnl_pct}% on cost</div></div>
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
  p.addEventListener('mousemove',e=>show(e,`<b>${{p.dataset.d}}</b> ${{p.dataset.v}} EUR`));
  p.addEventListener('mouseleave',hide);}});
</script></body></html>
"""

if __name__ == "__main__":
    main()
