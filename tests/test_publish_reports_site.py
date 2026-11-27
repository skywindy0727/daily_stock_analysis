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
    assert (summary.date, summary.slug, summary.label) == ("2026-11-18", "summary", "每日汇总")

    stock = site.parse_report_name(Path("report_20261118_hk00700.md"))
    assert (stock.slug, stock.label) == ("stock_hk00700", "hk00700")

    index = site.parse_report_name(Path("report_20261118_930955.CSI.md"))
    assert index.slug == "stock_930955.CSI"

    review = site.parse_report_name(Path("market_review_20261118.md"))
    assert (review.slug, review.label) == ("market_review", "大盘复盘")

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
    assert sorted(p.name for p in day.iterdir()) == ["market_review.html", "stock_600519.html", "summary.html"]
    summary_html = (day / "summary.html").read_text(encoding="utf-8")
    assert "<title>决策仪表盘</title>" in summary_html
    assert 'name="viewport"' in summary_html
    assert 'href="../../index.html"' in summary_html
    assert "<table>" in summary_html
    assert "<title>2026-11-18 大盘复盘</title>" in (day / "market_review.html").read_text(encoding="utf-8")
    assert (site_dir / ".nojekyll").exists()

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="reports/2026-11-18/summary.html"' in index_html
    # summary first, then market review, then stocks
    assert index_html.index("每日汇总") < index_html.index("大盘复盘") < index_html.index(">600519<")


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
    assert "暂无报告" in (site_dir / "index.html").read_text(encoding="utf-8")


def test_publish_site_job_is_opt_in_and_scoped():
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    job = jobs["publish-site"]

    assert job["needs"] == "analyze"
    assert "vars.PUBLISH_REPORTS_SITE == 'true'" in job["if"]
    assert "needs.analyze.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write"}
    # the analysis job must not gain write permissions
    assert "permissions" not in jobs["analyze"]

    upload = next(s for s in jobs["analyze"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    download = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/download-artifact"))
    assert download["with"]["name"] == upload["with"]["name"]

    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/publish_reports_site.py" in run
    assert "HEAD:gh-pages" in run
