from datetime import date, datetime, timezone
import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "us_close_schedule_gate",
    Path(__file__).resolve().parent.parent / "scripts" / "us_close_schedule_gate.py",
)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)

EDT_CRON = "0 4 * * 2-6"
EST_CRON = "0 5 * * 2-6"


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_est_regular_session_runs_on_est_cron_only():
    # Tue 2026-12-01 05:00 UTC = 00:00 EST, reporting Mon 2026-11-30.
    now = _utc(2026, 12, 1, 5, 2)
    est = gate.decide(EST_CRON, now)
    assert est.run is True
    assert est.session_date == date(2026, 11, 30)
    # The EDT twin fired an hour earlier (23:00 EST) and must skip.
    assert gate.decide(EDT_CRON, _utc(2026, 12, 1, 4, 1)).run is False


def test_edt_regular_session_runs_on_edt_cron_only():
    # Wed 2026-07-08 04:00 UTC = 00:00 EDT, reporting Tue 2026-07-07.
    edt = gate.decide(EDT_CRON, _utc(2026, 7, 8, 4, 0))
    assert edt.run is True
    assert edt.session_date == date(2026, 7, 7)
    assert gate.decide(EST_CRON, _utc(2026, 7, 8, 5, 0)).run is False


def test_friday_session_reported_on_saturday_utc():
    # Sat 2026-11-28 05:00 UTC reports Fri 2026-11-27 (early-close day, still a session).
    decision = gate.decide(EST_CRON, _utc(2026, 11, 28, 5, 0))
    assert decision.run is True
    assert decision.session_date == date(2026, 11, 27)


def test_market_holiday_is_skipped():
    # Thanksgiving Thu 2026-11-26 is an NYSE holiday.
    decision = gate.decide(EST_CRON, _utc(2026, 11, 27, 5, 0))
    assert decision.run is False
    assert decision.session_date == date(2026, 11, 26)


def test_delayed_start_keeps_decision():
    # GitHub may start a scheduled run late; 05:00 cron starting at 06:40 UTC still runs once.
    assert gate.decide(EST_CRON, _utc(2026, 12, 1, 6, 40)).run is True
    assert gate.decide(EDT_CRON, _utc(2026, 12, 1, 6, 40)).run is False


def test_weekend_is_skipped_even_if_cron_fires():
    # Sunday 2026-11-29 05:00 UTC would report Saturday.
    assert gate.decide(EST_CRON, _utc(2026, 11, 29, 5, 0)).run is False


def test_unsupported_cron_raises():
    with pytest.raises(ValueError):
        gate.decide("*/5 * * * *", _utc(2026, 12, 1, 5, 0))


def test_main_writes_github_output(tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("SCHEDULE", EST_CRON)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert gate.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "run=" in text and "session_date=" in text


def test_workflow_wires_gate_to_scheduled_runs_only():
    import yaml

    workflow_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "00-daily-analysis.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    crons = {entry["cron"] for entry in workflow[True]["schedule"]}
    assert crons == {EDT_CRON, EST_CRON}

    jobs = workflow["jobs"]
    assert jobs["schedule-gate"]["if"] == "github.event_name == 'schedule'"
    gate_step = next(s for s in jobs["schedule-gate"]["steps"] if s.get("id") == "gate")
    assert "us_close_schedule_gate.py" in gate_step["run"]
    assert gate_step["env"]["SCHEDULE"] == "${{ github.event.schedule }}"

    analyze = jobs["analyze"]
    assert analyze["needs"] == "schedule-gate"
    # manual dispatch must not depend on the gate; scheduled runs need run == 'true'
    assert "github.event_name != 'schedule'" in analyze["if"]
    assert "needs.schedule-gate.outputs.run == 'true'" in analyze["if"]
    assert "!cancelled()" in analyze["if"]
