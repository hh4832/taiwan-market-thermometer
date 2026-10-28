"""Target-date decisions for the three production checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .trading_calendar import TradingCalendar, recent_expected_sessions

TAIPEI = ZoneInfo("Asia/Taipei")


class RunPhase(str, Enum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    FINAL = "FINAL"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class RunDecision:
    phase: RunPhase
    target_data_date: str | None
    should_run: bool
    reason: str


def completed_dates_from_payload(path: str | Path) -> set[str]:
    source = Path(path)
    if not source.is_file():
        return set()
    payload = json.loads(source.read_text(encoding="utf-8"))
    provenance = payload.get("provenance", {})
    health = payload.get("system_health", {})
    if (
        provenance.get("pipeline_status") == "SUCCESS"
        and provenance.get("freshness") == "FRESH"
        and health.get("publication_status") == "SUCCESS"
        and provenance.get("data_date")
    ):
        return {str(provenance["data_date"])}
    return set()


def decide_run(
    now: object,
    phase: RunPhase | str,
    completed_dates: set[str] | None = None,
    *,
    calendar: TradingCalendar | None = None,
) -> RunDecision:
    resolved = phase if isinstance(phase, RunPhase) else RunPhase(str(phase).upper())
    source = calendar or TradingCalendar.configured()
    timestamp = pd.Timestamp(now)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Taipei")
    else:
        timestamp = timestamp.tz_convert("Asia/Taipei")
    completed = completed_dates or set()

    if resolved == RunPhase.FINAL:
        # At 05:00 the checkpoint belongs to the prior market date. Scan the
        # last three expected sessions oldest-first so a Friday can be repaired
        # on Saturday and an older unresolved date is not silently skipped.
        cutoff = timestamp.normalize() - pd.Timedelta(days=1)
        candidates = recent_expected_sessions(cutoff, 3, calendar=source)
        latest_complete = max(completed) if completed else None
        target = next((
            str(day.date()) for day in candidates
            if str(day.date()) not in completed
            and (latest_complete is None or str(day.date()) > latest_complete)
        ), None)
        if target is None:
            return RunDecision(resolved, None, False, "last three expected sessions are complete")
    else:
        day = timestamp.normalize()
        if not source.is_session(day):
            return RunDecision(resolved, None, False, f"{day.date()}: configured non-trading day")
        target = str(day.date())

    if target in completed:
        return RunDecision(resolved, target, False, f"{target}: validated publication already complete")
    return RunDecision(resolved, target, True, f"{target}: pending {resolved.value.lower()} checkpoint")


def phase_for_github_event(event_name: str, schedule: str | None, requested: str | None = None) -> RunPhase:
    if event_name == "repository_dispatch":
        return RunPhase.PRIMARY
    if event_name == "schedule" and schedule == "0 21 * * *":
        return RunPhase.FINAL
    if event_name == "schedule":
        return RunPhase.FALLBACK
    # Manual dispatch is deliberately gated through the same target resolver.
    return RunPhase((requested or "FALLBACK").upper())


def github_output() -> int:
    phase = phase_for_github_event(
        os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch"),
        os.getenv("GITHUB_EVENT_SCHEDULE") or None,
        os.getenv("REQUESTED_RUN_PHASE") or None,
    )
    decision = decide_run(
        datetime.now(TAIPEI),
        phase,
        completed_dates_from_payload(os.getenv("WORK_SITE_PAYLOAD", "public/data/work_site_payload.json")),
    )
    print(decision.reason)
    output = Path(os.environ["GITHUB_OUTPUT"])
    with output.open("a", encoding="utf-8") as handle:
        handle.write(f"should_run={'true' if decision.should_run else 'false'}\n")
        handle.write(f"run_phase={decision.phase.value}\n")
        handle.write(f"target_data_date={decision.target_data_date or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(github_output())
