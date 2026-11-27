#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a static GitHub Pages site from the Markdown reports in ``reports/``.

Used by the optional ``publish-site`` job in ``.github/workflows/00-daily-analysis.yml``
(enabled with the repository variable ``PUBLISH_REPORTS_SITE=true``). The job checks
out the ``gh-pages`` branch into ``--site-dir``, runs this script, then commits and
pushes the result, so earlier days stay in the branch history and on the site.

Pages are shown in Traditional Chinese (Taiwan wording, OpenCC ``s2twp``) with a
mobile-friendly layout: each report section becomes a card, single-row tables become
a label/value grid, wide tables scroll sideways, and the daily summary links to each
stock's section.

Layout written into ``--site-dir``::

    index.html                      # all days, newest first
    .nojekyll                       # serve files as-is
    reports/YYYY-MM-DD/<name>.md    # original Markdown, kept so later runs re-render with the current layout
    reports/YYYY-MM-DD/<name>.html  # one page per Markdown report

Only files named like the reports the pipeline writes are published:
``report_YYYYMMDD.md``, ``report_YYYYMMDD_<code>.md`` and ``market_review_YYYYMMDD.md``.
Pages published before Markdown sources were kept are converted once from their HTML.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import markdown2
import opencc

REPORT_NAME_RE = re.compile(
    r"^(?P<kind>report|market_review)_(?P<date>\d{8})(?:_(?P<code>[A-Za-z0-9.\-]+))?\.md$"
)
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SITE_TITLE = "每日股票分析報告"
LABEL_SUMMARY = "每日彙總"
LABEL_MARKET = "大盤復盤"
DISCLAIMER = "內容由 AI 依公開資料自動產生，僅供參考，不構成投資建議。"
WEEKDAYS = "一二三四五六日"
# Bump when the page layout changes; pages without a Markdown source and with an older
# version are converted from their existing HTML.
SITE_VERSION = "2"
GENERATOR = f"dsa-report-site {SITE_VERSION}"

_CONVERTER = opencc.OpenCC("s2twp")
# Wording OpenCC leaves in mainland usage, or changes where Taiwan keeps the original.
_TW_FIXUPS = (
    ("茅臺", "茅台"),
    ("只股票", "檔股票"),
    ("支股票", "檔股票"),
    ("儀表盤", "儀表板"),
    ("質量", "品質"),
    ("昇", "升"),
)


@dataclass(frozen=True)
class ReportFile:
    source: Path
    date: str  # YYYY-MM-DD
    slug: str  # output file stem
    label: str
    order: int  # sort key within a day


def to_traditional(text: str) -> str:
    """Convert Simplified Chinese to Traditional Chinese with Taiwan wording."""
    converted = _CONVERTER.convert(text)
    for old, new in _TW_FIXUPS:
        converted = converted.replace(old, new)
    return converted


def parse_report_name(path: Path) -> Optional[ReportFile]:
    """Return report metadata for a pipeline report file, or None for anything else."""
    match = REPORT_NAME_RE.match(path.name)
    if not match:
        return None
    raw = match.group("date")
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    kind, code = match.group("kind"), match.group("code")
    if kind == "market_review":
        if code:
            return None
        return ReportFile(path, date, "market_review", LABEL_MARKET, 1)
    if code:
        return ReportFile(path, date, f"stock_{code}", code, 2)
    return ReportFile(path, date, "summary", LABEL_SUMMARY, 0)


def collect_reports(reports_dir: Path) -> List[ReportFile]:
    if not reports_dir.is_dir():
        return []
    found = (parse_report_name(p) for p in sorted(reports_dir.glob("*.md")))
    return [r for r in found if r is not None]


def _page_label(page: Path) -> tuple:
    stem = page.stem
    if stem == "summary":
        return (0, LABEL_SUMMARY)
    if stem == "market_review":
        return (1, LABEL_MARKET)
    if stem.startswith("stock_"):
        return (2, stem[len("stock_"):])
    return (3, stem)


# ---------------------------------------------------------------------------
# Markdown -> HTML fragment
# ---------------------------------------------------------------------------

_ALLOWED_TAG_RE = r"/?(?:br|b|i|u|em|strong|sub|sup|del|details|summary)\b[^<>]*>"
_STRAY_LT_RE = re.compile(r"<(?!" + _ALLOWED_TAG_RE + r")")
_FENCE_RE = re.compile(r"(^```.*?^```[ \t]*$)", re.S | re.M)


def _escape_stray_lt(markdown_text: str) -> str:
    """Escape ``<`` that does not start a simple inline tag (e.g. ``MA5<MA10``).

    Left raw, browsers read ``<MA10...`` as an unknown tag and hide the rest of the line.
    Fenced code blocks are left alone because markdown2 escapes them itself.
    """
    parts = _FENCE_RE.split(markdown_text)
    for i in range(0, len(parts), 2):
        parts[i] = _STRAY_LT_RE.sub("&lt;", parts[i])
    return "".join(parts)


def markdown_to_fragment(markdown_text: str) -> str:
    # code-friendly: report text such as ``pack_version`` must not turn into italics;
    # the report templates use ``*``/``**`` for emphasis.
    return markdown2.markdown(
        _escape_stray_lt(markdown_text),
        extras=["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists", "code-friendly"],
    )


# ---------------------------------------------------------------------------
# HTML fragment -> readable page body
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(r"<table>(.*?)</table>", re.S)
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_H1_RE = re.compile(r"<h1>(.*?)</h1>", re.S)
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.S)
_HR_RE = re.compile(r"<hr\s*/?>")
_CODE_AT_END_RE = re.compile(r"[(（]\s*([A-Za-z0-9.\-]{1,15})\s*[)）]\s*$")
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"<p>(.*?)</p>", re.S)
_PICK_LINE_RE = re.compile(
    r"^(?P<icon>\S+)\s*<strong>(?P<name>.+?)[(（](?P<code>[A-Za-z0-9.\-]{1,15})[)）]</strong>\s*[:：]\s*"
    r"(?P<action>[^|<]+?)\s*\|\s*評分\s*(?P<score>\d{1,3})\s*\|\s*(?P<trend>[^|<]+?)\s*$"
)
_BR_SPLIT_RE = re.compile(r"<br\s*/?>\s*")


def _plain(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _anchor_for(code: str) -> str:
    return "s-" + re.sub(r"[^A-Za-z0-9.\-]", "", code)


def _table_to_block(match: re.Match) -> str:
    """Single-row tables become a label/value grid; others get a sideways-scroll wrapper."""
    inner = match.group(1)
    thead = re.search(r"<thead>(.*?)</thead>", inner, re.S)
    tbody = re.search(r"<tbody>(.*?)</tbody>", inner, re.S)
    if thead and tbody:
        heads = _CELL_RE.findall(thead.group(1))
        rows = _ROW_RE.findall(tbody.group(1))
        if len(rows) == 1 and len(heads) >= 3:
            cells = _CELL_RE.findall(rows[0])
            if len(cells) == len(heads):
                items = "".join(
                    f'<div class="stat"><dt>{h.strip()}</dt><dd>{c.strip()}</dd></div>'
                    for h, c in zip(heads, cells)
                )
                return f'<dl class="stats">{items}</dl>'
    return f'<div class="table-wrap"><table>{inner}</table></div>'


def _picks_paragraph(match: re.Match) -> str:
    """Turn the summary's "icon **Name(CODE)**: action | 評分 N | trend" lines into a linked list."""
    lines = [line.strip() for line in _BR_SPLIT_RE.split(match.group(1).strip()) if line.strip()]
    parsed = [_PICK_LINE_RE.match(line) for line in lines]
    if len(lines) < 2 or not all(parsed):
        return match.group(0)
    items = []
    for m in parsed:
        score = int(m.group("score"))
        level = "hi" if score >= 60 else ("mid" if score >= 40 else "lo")
        items.append(
            f'<li><a href="#{_anchor_for(m.group("code"))}">'
            f'<span class="pick-icon">{m.group("icon")}</span>'
            f'<span class="pick-name">{m.group("name").strip()}<small>{m.group("code")}</small></span>'
            f'<span class="pick-action">{m.group("action")}</span>'
            f'<span class="pick-score {level}" title="評分">{score}</span>'
            f'<span class="pick-trend">{m.group("trend")}</span>'
            "</a></li>"
        )
    return f'<ul class="picks">{"".join(items)}</ul>'


def enhance_fragment(fragment: str) -> Tuple[str, str, List[Tuple[str, str]]]:
    """Return (h1 text, body html, [(anchor, label)] for stock sections)."""
    h1 = ""
    h1_match = _H1_RE.search(fragment)
    if h1_match:
        h1 = h1_match.group(1).strip()
        fragment = fragment[: h1_match.start()] + fragment[h1_match.end():]

    fragment = _TABLE_RE.sub(_table_to_block, fragment)
    fragment = _PARA_RE.sub(_picks_paragraph, fragment)

    toc: List[Tuple[str, str]] = []
    seen = set()

    def _h2(m: re.Match) -> str:
        text = m.group(1).strip()
        code = _CODE_AT_END_RE.search(_plain(text))
        if code:
            anchor = _anchor_for(code.group(1))
            if anchor not in seen:
                seen.add(anchor)
                toc.append((anchor, code.group(1)))
                return f'<h2 id="{anchor}">{text}</h2>'
        return m.group(0)

    fragment = _H2_RE.sub(_h2, fragment)

    sections = [s.strip() for s in _HR_RE.split(fragment)]
    cards = "\n".join(f'<section class="card">\n{s}\n</section>' for s in sections if _plain(s) or "<img" in s)
    return h1, cards, toc


def _description(body: str) -> str:
    quote = re.search(r"<blockquote>(.*?)</blockquote>", body, re.S)
    source = quote.group(1) if quote else body
    text = re.sub(r"\s+", " ", _plain(re.sub(r"<br\s*/?>", " ", source)))
    return text[:90] + ("…" if len(text) > 90 else "")


def _weekday(date: str) -> str:
    try:
        return "週" + WEEKDAYS[dt.date.fromisoformat(date).weekday()]
    except ValueError:
        return ""


PAGE_CSS = """
:root {
  --bg: #f4f6f9; --card: #ffffff; --text: #1f2933; --muted: #66727f; --line: #e3e8ee;
  --accent: #2563eb; --accent-soft: #e8f0fe; --head: #f7f9fb; --quote: #f1f5ff;
  --hi: #15803d; --mid: #b7791f; --lo: #c53030;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f141a; --card: #171e26; --text: #e4e9ef; --muted: #98a4b3; --line: #2a333e;
    --accent: #6ea8fe; --accent-soft: #1d2a3f; --head: #1c242e; --quote: #1a2433;
    --hi: #4ade80; --mid: #f6c35b; --lo: #f87171;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; scroll-padding-top: 64px; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "PingFang TC", "Noto Sans TC", "Microsoft JhengHei",
    "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 16px; line-height: 1.75; letter-spacing: .01em;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.topbar {
  position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; background: color-mix(in srgb, var(--card) 92%, transparent);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); border-bottom: 1px solid var(--line);
}
.topbar .brand { font-weight: 600; font-size: 15px; color: var(--text); }
.topbar .back { font-size: 15px; white-space: nowrap; }
.topbar .spacer { flex: 1; }
.wrap { max-width: 880px; margin: 0 auto; padding: 16px 16px 40px; }
.page-head { padding: 8px 2px 4px; }
.page-head .meta { color: var(--muted); font-size: 14px; }
.page-head h1 { margin: 4px 0 12px; font-size: 26px; line-height: 1.35; }
.toc { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; padding: 0; list-style: none; }
.toc a {
  display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 14px;
  background: var(--accent-soft); color: var(--accent); font-weight: 500;
}
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 18px 20px; margin: 0 0 16px; box-shadow: 0 1px 2px rgba(15, 23, 42, .04);
  overflow-wrap: anywhere;
}
.card > :first-child { margin-top: 0; }
.card > :last-child { margin-bottom: 0; }
h2 { font-size: 21px; line-height: 1.4; margin: 1.2em 0 .6em; }
h3 {
  font-size: 17px; margin: 1.4em 0 .5em; padding-left: 10px; border-left: 4px solid var(--accent);
  line-height: 1.4;
}
h4 { font-size: 16px; margin: 1.2em 0 .4em; }
p { margin: 0 0 .8em; }
ul, ol { margin: 0 0 .9em; padding-left: 1.4em; }
li { margin: .25em 0; }
li::marker { color: var(--muted); }
strong { font-weight: 650; }
blockquote {
  margin: 0 0 1em; padding: 10px 14px; background: var(--quote); border-left: 4px solid var(--accent);
  border-radius: 0 10px 10px 0;
}
blockquote p:last-child { margin-bottom: 0; }
em { color: var(--muted); font-style: normal; font-size: 14px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em;
  background: var(--head); padding: .1em .35em; border-radius: 4px; }
pre { background: var(--head); padding: 12px; border-radius: 10px; overflow-x: auto; }
pre code { background: none; padding: 0; }
img { max-width: 100%; height: auto; }
.table-wrap {
  overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 0 1em;
  border: 1px solid var(--line); border-radius: 10px;
}
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { padding: 8px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: var(--head); font-weight: 600; white-space: nowrap; color: var(--muted); font-size: 14px; }
tbody tr:last-child td { border-bottom: none; }
td:first-child, th:first-child { position: sticky; left: 0; background: var(--card); }
th:first-child { background: var(--head); }
.stats {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; margin: 0 0 1em;
}
.stat { background: var(--head); border-radius: 10px; padding: 8px 12px; }
.stat dt { font-size: 13px; color: var(--muted); }
.stat dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.picks { list-style: none; padding: 0; margin: 0 0 1em; }
.picks li { margin: 0; border-bottom: 1px solid var(--line); }
.picks li:last-child { border-bottom: none; }
.picks a {
  display: grid; grid-template-columns: 1.6em 1fr auto auto; grid-template-areas: "icon name action score" "icon name trend score";
  column-gap: 10px; align-items: center; padding: 10px 2px; color: var(--text);
}
.picks a:hover { text-decoration: none; background: var(--head); }
.pick-icon { grid-area: icon; }
.pick-name { grid-area: name; font-weight: 600; line-height: 1.4; }
.pick-name small { display: block; color: var(--muted); font-weight: 400; font-size: 13px; }
.pick-action { grid-area: action; font-size: 14px; text-align: right; }
.pick-trend { grid-area: trend; font-size: 13px; color: var(--muted); text-align: right; }
.pick-score {
  grid-area: score; min-width: 2.6em; text-align: center; font-weight: 700; font-variant-numeric: tabular-nums;
  border-radius: 8px; padding: 4px 6px; background: var(--head);
}
.pick-score.hi { color: var(--hi); } .pick-score.mid { color: var(--mid); } .pick-score.lo { color: var(--lo); }
.foot { color: var(--muted); font-size: 13px; text-align: center; margin-top: 24px; }
@media (max-width: 600px) {
  body { font-size: 16px; line-height: 1.7; }
  .wrap { padding: 12px 12px 32px; }
  .card { padding: 14px 14px; border-radius: 12px; }
  .page-head h1 { font-size: 22px; }
  h2 { font-size: 19px; }
  table { font-size: 14px; }
  th, td { padding: 7px 10px; }
  .stats { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .stat { padding: 6px 10px; }
  .stat dd { font-size: 15px; }
}
"""

INDEX_CSS = """
.day { padding: 16px 18px; }
.day-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.day-head h2 { margin: 0; font-size: 19px; }
.day-head .wd { color: var(--muted); font-size: 14px; }
.badge { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--accent); color: var(--card); }
.day .desc { color: var(--muted); font-size: 14px; margin: 6px 0 10px; }
.links { display: flex; flex-wrap: wrap; gap: 8px; }
.links a {
  padding: 6px 14px; border-radius: 10px; font-size: 15px; border: 1px solid var(--line);
  background: var(--head); color: var(--text); font-weight: 500;
}
.links a.primary { background: var(--accent); border-color: var(--accent); color: var(--card); }
.intro { color: var(--muted); font-size: 14px; margin: 0 0 16px; }
"""


def _document(title: str, description: str, body: str, extra_css: str = "") -> str:
    desc_meta = f'<meta name="description" content="{html.escape(description)}">\n' if description else ""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="{GENERATOR}">
{desc_meta}<title>{html.escape(title)}</title>
<style>{PAGE_CSS}{extra_css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_page_from_fragment(fragment: str, date: str, label: str, fallback_title: str) -> str:
    h1, cards, toc = enhance_fragment(fragment)
    title = _plain(h1) or fallback_title
    meta = " · ".join(x for x in (f"{date}（{_weekday(date)}）" if date else "", label) if x)
    toc_html = ""
    if len(toc) >= 2:
        toc_html = '<ul class="toc">' + "".join(
            f'<li><a href="#{a}">{html.escape(code)}</a></li>' for a, code in toc
        ) + "</ul>\n"
    body = f"""<header class="topbar">
<a class="back" href="../../index.html">← 全部報告</a><span class="spacer"></span><span class="brand">{SITE_TITLE}</span>
</header>
<main class="wrap">
<div class="page-head"><div class="meta">{html.escape(meta)}</div><h1>{h1 or html.escape(title)}</h1></div>
{toc_html}{cards}
<p class="foot">{DISCLAIMER}</p>
</main>"""
    return _document(title, _description(cards), body)


def render_report_page(markdown_text: str, date: str = "", label: str = "", fallback_title: str = "") -> str:
    """Render one Markdown report (Simplified Chinese) as a Traditional Chinese page."""
    fragment = markdown_to_fragment(to_traditional(markdown_text))
    return render_page_from_fragment(fragment, date, label, to_traditional(fallback_title))


_LEGACY_BODY_RE = re.compile(r"<body>(.*)</body>", re.S)
# Old pages kept text such as ``MA5<MA10`` as raw ``<``; escape anything that is not a tag markdown2 emits.
_LEGACY_STRAY_LT_RE = re.compile(
    r"<(?!/?(?:p|ul|ol|li|h[1-6]|table|thead|tbody|tr|th|td|blockquote|strong|em|b|i|u|code|pre|hr|br|a|img"
    r"|div|span|del|sub|sup|details|summary)\b)"
)
_LEGACY_NAV_RE = re.compile(r"^\s*<p><a href=\"\.\./\.\./index\.html\">[^<]*</a></p>", re.S)


def migrate_legacy_page(page: Path, date: str) -> bool:
    """Re-render a page published before Markdown sources were kept. Returns True if rewritten."""
    text = page.read_text(encoding="utf-8")
    if f'content="{GENERATOR}"' in text or 'name="generator" content="dsa-report-site' in text:
        return False
    body = _LEGACY_BODY_RE.search(text)
    if not body:
        return False
    fragment = _LEGACY_NAV_RE.sub("", body.group(1).strip(), count=1)
    fragment = _LEGACY_STRAY_LT_RE.sub("&lt;", fragment)
    label = _page_label(page)[1]
    page.write_text(
        render_page_from_fragment(to_traditional(fragment), date, label, f"{date} {label}"), encoding="utf-8"
    )
    return True


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

_META_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)">')


def _page_description(page: Path) -> str:
    try:
        match = _META_DESC_RE.search(page.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return html.unescape(match.group(1)) if match else ""


def render_index(site_dir: Path) -> str:
    """Build index.html from every dated folder already present under site_dir/reports."""
    days: Dict[str, List[Path]] = {}
    reports_root = site_dir / "reports"
    if reports_root.is_dir():
        for day_dir in reports_root.iterdir():
            if day_dir.is_dir() and DATE_DIR_RE.match(day_dir.name):
                pages = sorted(day_dir.glob("*.html"), key=_page_label)
                if pages:
                    days[day_dir.name] = pages

    cards = []
    for i, day in enumerate(sorted(days, reverse=True)):
        pages = days[day]
        links = "".join(
            f'<a class="{"primary" if j == 0 else ""}" href="reports/{day}/{html.escape(p.name)}">'
            f"{html.escape(_page_label(p)[1])}</a>"
            for j, p in enumerate(pages)
        )
        desc = _page_description(pages[0])
        desc_html = f'<p class="desc">{html.escape(desc)}</p>' if desc else ""
        badge = '<span class="badge">最新</span>' if i == 0 else ""
        cards.append(
            f'<section class="card day"><div class="day-head"><h2>{day}</h2>'
            f'<span class="wd">{_weekday(day)}</span>{badge}</div>{desc_html}'
            f'<div class="links">{links}</div></section>'
        )
    content = "\n".join(cards) if cards else '<section class="card">暫無報告</section>'

    body = f"""<header class="topbar"><span class="brand">📈 {SITE_TITLE}</span></header>
<main class="wrap">
<div class="page-head"><h1>{SITE_TITLE}</h1></div>
<p class="intro">每個交易日由 GitHub Actions 自動更新。{DISCLAIMER}</p>
{content}
</main>"""
    return _document(SITE_TITLE, "", body, INDEX_CSS)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _render_day_dir(day_dir: Path) -> None:
    """Re-render every page in a day folder: from its Markdown source when kept, else from old HTML."""
    date = day_dir.name
    for md in sorted(day_dir.glob("*.md")):
        label = _page_label(md)[1]
        markdown_text = md.read_text(encoding="utf-8")
        md.with_suffix(".html").write_text(
            render_report_page(markdown_text, date, label, f"{date} {label}"), encoding="utf-8"
        )
    for page in sorted(day_dir.glob("*.html")):
        if not page.with_suffix(".md").exists():
            migrate_legacy_page(page, date)


def publish(reports_dir: Path, site_dir: Path) -> int:
    """Copy new reports in, re-render every page with the current layout and rebuild the index.

    Returns the number of reports taken from ``reports_dir``.
    """
    site_dir.mkdir(parents=True, exist_ok=True)
    reports = collect_reports(reports_dir)
    for report in reports:
        out_dir = site_dir / "reports" / report.date
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report.source, out_dir / f"{report.slug}.md")

    reports_root = site_dir / "reports"
    if reports_root.is_dir():
        for day_dir in sorted(reports_root.iterdir()):
            if day_dir.is_dir() and DATE_DIR_RE.match(day_dir.name):
                _render_day_dir(day_dir)

    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (site_dir / "index.html").write_text(render_index(site_dir), encoding="utf-8")
    return len(reports)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports-dir", type=Path, default=Path(__file__).resolve().parent.parent / "reports")
    parser.add_argument("--site-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    count = publish(args.reports_dir, args.site_dir)
    print(f"Published {count} report page(s) to {args.site_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
