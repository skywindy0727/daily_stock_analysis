# -*- coding: utf-8 -*-
"""Tests for scripts/publish_reports_site.py and its optional workflow job."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "scripts" / "publish_reports_site.py"
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "00-daily-analysis.yml"

_spec = importlib.util.spec_from_file_location("publish_reports_site", SCRIPT_PATH)
site = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = site
_spec.loader.exec_module(site)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_parse_report_name_recognizes_pipeline_files_only():
    summary = site.parse_report_name(Path("report_20261118.md"))
    assert (summary.date, summary.slug, summary.label) == ("2026-11-18", "summary", "每日彙總")

    stock = site.parse_report_name(Path("report_20261118_hk00700.md"))
    assert (stock.slug, stock.label) == ("stock_hk00700", "hk00700")

    index = site.parse_report_name(Path("report_20261118_930955.CSI.md"))
    assert index.slug == "stock_930955.CSI"

    review = site.parse_report_name(Path("market_review_20261118.md"))
    assert (review.slug, review.label) == ("market_review", "大盤復盤")

    for name in ("notes.md", "report_2026111.md", "report_20261118.txt", "market_review_20261118_x.md"):
        assert site.parse_report_name(Path(name)) is None


def test_publish_writes_pages_and_index(tmp_path):
    reports = tmp_path / "reports"
    _write(reports / "report_20261118.md", "# 决策仪表盘\n\n| 代码 | 建议 |\n|---|---|\n| 600519 | 持有 |\n")
    _write(reports / "report_20261118_600519.md", "# 贵州茅台\n\n内容 <script>")
    _write(reports / "market_review_20261118.md", "大盘内容")
    _write(reports / "ignored.md", "# should not publish")
    site_dir = tmp_path / "site"

    assert site.publish(reports, site_dir) == 3

    day = site_dir / "reports" / "2026-11-18"
    assert sorted(p.name for p in day.iterdir()) == [
        "market_review.html", "market_review.md", "stock_600519.html", "stock_600519.md", "summary.html", "summary.md",
    ]
    summary_html = (day / "summary.html").read_text(encoding="utf-8")
    assert "<title>決策儀表板</title>" in summary_html
    assert 'lang="zh-Hant-TW"' in summary_html
    assert 'name="viewport"' in summary_html
    assert 'href="../../index.html"' in summary_html
    assert "<table>" in summary_html
    assert "<script>" not in (day / "stock_600519.html").read_text(encoding="utf-8")
    assert "<title>2026-11-18 大盤復盤</title>" in (day / "market_review.html").read_text(encoding="utf-8")
    assert (site_dir / ".nojekyll").exists()

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="reports/2026-11-18/summary.html"' in index_html
    # summary first, then market review, then stocks
    assert index_html.index("每日彙總") < index_html.index("大盤復盤") < index_html.index(">600519<")


def test_publish_keeps_earlier_days_newest_first(tmp_path):
    site_dir = tmp_path / "site"
    _write(site_dir / "reports" / "2026-11-17" / "summary.html", "<html></html>")
    _write(site_dir / "reports" / "not-a-date" / "x.html", "<html></html>")
    reports = tmp_path / "reports"
    _write(reports / "report_20261118.md", "# today")

    site.publish(reports, site_dir)

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert index_html.index("2026-11-18") < index_html.index("2026-11-17")
    assert "not-a-date" not in index_html


def test_publish_with_missing_reports_dir_still_builds_index(tmp_path):
    site_dir = tmp_path / "site"
    assert site.publish(tmp_path / "missing", site_dir) == 0
    assert "暫無報告" in (site_dir / "index.html").read_text(encoding="utf-8")


def test_to_traditional_uses_taiwan_wording():
    assert site.to_traditional("共分析 3 只股票，数据质量良好，贵州茅台软件拉升") == "共分析 3 檔股票，資料品質良好，貴州茅台軟體拉升"


def test_report_layout_cards_stats_and_summary_links(tmp_path):
    markdown_text = (
        "# 🎯 2026-11-18 决策仪表盘\n\n"
        "> 共分析 **2** 只股票 | 🟢买入:1 🔴卖出:1\n\n"
        "## 📊 分析结果摘要\n\n"
        "🟢 **贵州茅台(600519)**: 买入 | 评分 78 | 看多\n"
        "🔴 **Apple Inc.(AAPL)**: 卖出 | 评分 35 | 看空\n\n"
        "---\n\n"
        "## 🟢 贵州茅台 (600519)\n\n"
        "| 收盘 | 昨收 | 涨跌幅 |\n|---|---|---|\n| 1500 | 1480 | 1.35% |\n\n"
        "均线 MA5<MA10 不成立，数据来源 pack_version=1.0\n\n"
        "---\n\n"
        "## 🔴 Apple Inc. (AAPL)\n\n"
        "| 日期 | 收盘 |\n|---|---|\n| 11-17 | 1 |\n| 11-18 | 2 |\n"
    )
    page = site.render_report_page(markdown_text, "2026-11-18", "每日彙總", "2026-11-18 每日彙總")

    assert page.count('<section class="card">') == 3
    # single-row table -> label/value grid; multi-row table keeps a scroll wrapper
    assert '<dl class="stats">' in page and "<dt>收盤</dt><dd>1500</dd>" in page
    assert '<div class="table-wrap"><table>' in page
    # summary lines link to each stock's section, and both appear in the quick-jump list
    assert 'href="#s-600519"' in page and '<h2 id="s-600519">' in page
    assert 'href="#s-AAPL"' in page and '<h2 id="s-AAPL">' in page
    assert '<span class="pick-score hi" title="評分">78</span>' in page
    # "<" in text is escaped instead of swallowing the line; underscores are not italics
    assert "MA5&lt;MA10" in page
    assert "pack_version=1.0" in page
    assert '<meta name="description" content="共分析 2 檔股票' in page
    assert "（週三）" in page


def test_publish_converts_legacy_pages_once_and_keeps_sources(tmp_path):
    site_dir = tmp_path / "site"
    legacy = site_dir / "reports" / "2026-11-17" / "summary.html"
    _write(
        legacy,
        '<html><head><title>x</title></head><body>\n<p><a href="../../index.html">← 全部报告</a></p>'
        "<h1>决策仪表盘</h1><p>均线（MA5<MA10）</p><hr /><h2>腾讯 (hk00700)</h2></body></html>",
    )

    site.publish(tmp_path / "missing", site_dir)
    migrated = legacy.read_text(encoding="utf-8")
    assert "<h1>決策儀表板</h1>" in migrated
    assert "MA5&lt;MA10" in migrated
    assert "全部报告" not in migrated and migrated.count("全部報告") == 1
    assert 'content="dsa-report-site' in migrated

    site.publish(tmp_path / "missing", site_dir)
    assert legacy.read_text(encoding="utf-8") == migrated


def test_publish_site_job_is_opt_in_and_scoped():
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    job = jobs["publish-site"]

    assert job["needs"] == "analyze"
    assert job["if"] == "vars.PUBLISH_REPORTS_SITE == 'true'"
    assert job["permissions"] == {"contents": "write"}
    # the analysis job must not gain write permissions
    assert "permissions" not in jobs["analyze"]

    upload = next(s for s in jobs["analyze"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    download = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/download-artifact"))
    assert download["with"]["name"] == upload["with"]["name"]

    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/publish_reports_site.py" in run
    assert "HEAD:gh-pages" in run
    assert "opencc" in run
