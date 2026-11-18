#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a static GitHub Pages site from the Markdown reports in ``reports/``.

Used by the optional ``publish-site`` job in ``.github/workflows/00-daily-analysis.yml``
(enabled with the repository variable ``PUBLISH_REPORTS_SITE=true``). The job checks
out the ``gh-pages`` branch into ``--site-dir``, runs this script, then commits and
pushes the result, so earlier days stay in the branch history and on the site.

Layout written into ``--site-dir``::

    index.html                      # all days, newest first
    .nojekyll                       # serve files as-is
    reports/YYYY-MM-DD/<name>.html  # one page per Markdown report

Only files named like the reports the pipeline writes are published:
``report_YYYYMMDD.md``, ``report_YYYYMMDD_<code>.md`` and ``market_review_YYYYMMDD.md``.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.formatters import markdown_to_html_document  # noqa: E402

REPORT_NAME_RE = re.compile(
    r"^(?P<kind>report|market_review)_(?P<date>\d{8})(?:_(?P<code>[A-Za-z0-9.\-]+))?\.md$"
)
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SITE_TITLE = "每日股票分析报告"
VIEWPORT_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'


@dataclass(frozen=True)
class ReportFile:
    source: Path
    date: str  # YYYY-MM-DD
    slug: str  # output file stem
    label: str
    order: int  # sort key within a day


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
        return ReportFile(path, date, "market_review", "大盘复盘", 1)
    if code:
        return ReportFile(path, date, f"stock_{code}", code, 2)
    return ReportFile(path, date, "summary", "每日汇总", 0)


def collect_reports(reports_dir: Path) -> List[ReportFile]:
    if not reports_dir.is_dir():
        return []
    found = (parse_report_name(p) for p in sorted(reports_dir.glob("*.md")))
    return [r for r in found if r is not None]


def _extract_title(markdown_text: str, fallback: str) -> str:
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def render_report_page(markdown_text: str, title: str) -> str:
    """Render one report with the shared Markdown styling plus a title and back link."""
    document = markdown_to_html_document(markdown_text)
    head_extra = f"{VIEWPORT_META}\n<title>{html.escape(title)}</title>"
    nav = '<p><a href="../../index.html">← 全部报告</a></p>'
    document = document.replace('<meta charset="utf-8">', f'<meta charset="utf-8">\n{head_extra}', 1)
    document = document.replace("<body>", f"<body>\n{nav}", 1)
    return document


def _page_label(page: Path) -> tuple:
    stem = page.stem
    if stem == "summary":
        return (0, "每日汇总")
    if stem == "market_review":
        return (1, "大盘复盘")
    if stem.startswith("stock_"):
        return (2, stem[len("stock_"):])
    return (3, stem)


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

    sections = []
    for day in sorted(days, reverse=True):
        links = " · ".join(
            f'<a href="reports/{day}/{html.escape(p.name)}">{html.escape(_page_label(p)[1])}</a>'
            for p in days[day]
        )
        sections.append(f"<li><strong>{day}</strong><br>{links}</li>")
    body = "\n".join(sections) if sections else "<li>暂无报告</li>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
{VIEWPORT_META}
<title>{SITE_TITLE}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       color: #24292e; background: #fff; max-width: 900px; margin: 0 auto; padding: 16px; line-height: 1.6; }}
h1 {{ font-size: 22px; color: #0366d6; border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
ul {{ list-style: none; padding: 0; }}
li {{ padding: 10px 0; border-bottom: 1px solid #eaecef; }}
a {{ color: #0366d6; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.note {{ color: #6a737d; font-size: 13px; }}
</style>
</head>
<body>
<h1>{SITE_TITLE}</h1>
<p class="note">由 GitHub Actions 每日自动生成，内容仅供参考，不构成投资建议。</p>
<ul>
{body}
</ul>
</body>
</html>
"""


def publish(reports_dir: Path, site_dir: Path) -> int:
    """Write report pages and rebuild the index. Returns the number of pages written."""
    site_dir.mkdir(parents=True, exist_ok=True)
    reports = collect_reports(reports_dir)
    for report in reports:
        markdown_text = report.source.read_text(encoding="utf-8")
        title = _extract_title(markdown_text, f"{report.date} {report.label}")
        out_dir = site_dir / "reports" / report.date
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{report.slug}.html").write_text(
            render_report_page(markdown_text, title), encoding="utf-8"
        )
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (site_dir / "index.html").write_text(render_index(site_dir), encoding="utf-8")
    return len(reports)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports-dir", type=Path, default=ROOT_DIR / "reports")
    parser.add_argument("--site-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    count = publish(args.reports_dir, args.site_dir)
    print(f"Published {count} report page(s) to {args.site_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
