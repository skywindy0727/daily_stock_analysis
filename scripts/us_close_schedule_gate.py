#!/usr/bin/env python3
"""Gate for the scheduled daily-analysis run: US close + 8 hours.

GitHub Actions cron only understands UTC, while the US market closes at
16:00 America/New_York and shifts between EDT (UTC-4) and EST (UTC-5).
The workflow therefore schedules two crons (UTC 04:00 and UTC 05:00,
Tuesday to Saturday). Both correspond to 00:00 New York time in one half of
the year, i.e. 8 hours after the 16:00 close of the previous day.

This script decides whether the cron that fired should actually run:

1. Only the cron whose UTC hour equals 00:00 New York time under the
   current DST offset runs; its twin is skipped. The decision uses the
   offset at execution time, so a delayed start does not flip it.
2. The session being reported on is the previous New York calendar day. It
   must be an NYSE trading session (holidays are skipped via
   ``src.core.trading_calendar.is_market_open``, which fails open when the
   calendar is unavailable; weekends are always skipped).

The result is written to ``$GITHUB_OUTPUT`` as ``run=true|false`` and
``session_date=YYYY-MM-DD``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.trading_calendar import MARKET_TIMEZONE, is_market_open  # noqa: E402

MARKET = "us"
CLOSE_HOUR_LOCAL = 16
DELAY_HOURS = 8
# 16:00 + 8h = 00:00 of the next local day.
TARGET_HOUR_LOCAL = (CLOSE_HOUR_LOCAL + DELAY_HOURS) % 24


@dataclass(frozen=True)
class GateDecision:
    run: bool
    session_date: date
    reason: str


def _cron_utc_hour(schedule: str) -> int:
    fields = schedule.split()
    if len(fields) != 5 or not fields[1].isdigit():
        raise ValueError(f"unsupported cron expression: {schedule!r}")
    return int(fields[1])


def expected_utc_hour(now_utc: datetime) -> int:
    """UTC hour that equals TARGET_HOUR_LOCAL in New York at ``now_utc``."""
    local = now_utc.astimezone(ZoneInfo(MARKET_TIMEZONE[MARKET]))
    offset_hours = int(local.utcoffset().total_seconds() // 3600)
    return (TARGET_HOUR_LOCAL - offset_hours) % 24


def decide(schedule: str, now_utc: datetime) -> GateDecision:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(ZoneInfo(MARKET_TIMEZONE[MARKET]))
    session_date = local_now.date() - timedelta(days=1)

    cron_hour = _cron_utc_hour(schedule)
    wanted_hour = expected_utc_hour(now_utc)
    if cron_hour != wanted_hour:
        return GateDecision(
            False,
            session_date,
            f"cron UTC {cron_hour:02d}:00 is the DST twin; "
            f"UTC {wanted_hour:02d}:00 handles the current offset",
        )

    if session_date.weekday() >= 5:
        return GateDecision(False, session_date, f"{session_date} is a weekend")
    if not is_market_open(MARKET, session_date):
        return GateDecision(False, session_date, f"{session_date} is not an NYSE session")
    return GateDecision(True, session_date, f"{session_date} was an NYSE session")


def main() -> int:
    schedule = os.environ.get("SCHEDULE", "").strip()
    if not schedule:
        print("SCHEDULE is empty; this gate only applies to scheduled runs", file=sys.stderr)
        return 2
    decision = decide(schedule, datetime.now(timezone.utc))
    print(f"run={str(decision.run).lower()} session_date={decision.session_date} ({decision.reason})")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            fh.write(f"run={str(decision.run).lower()}\n")
            fh.write(f"session_date={decision.session_date}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
