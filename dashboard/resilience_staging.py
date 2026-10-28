"""Deterministic mock/staging acceptance evidence for calendar resilience."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from .official_market import EvidenceStatus, OfficialEvidence
from .pipeline_exceptions import build_exception
from .pipeline_schedule import RunPhase, decide_run
from .review_processor import process_review_row
from .trading_calendar import TradingCalendar


def build_simulation_report() -> dict[str, object]:
    calendar = TradingCalendar(
        pd.DatetimeIndex(["2026-10-09", "2026-10-26"]), "mock", "mock-v1"
    )
    primary = decide_run("2026-10-22 20:13+08:00", RunPhase.PRIMARY, set(), calendar=calendar)
    fallback = decide_run("2026-10-22 20:47+08:00", RunPhase.FALLBACK, set(), calendar=calendar)
    final = decide_run(
        "2026-10-23 05:00+08:00", RunPhase.FINAL,
        {"2026-10-20", "2026-10-21"}, calendar=calendar,
    )
    official = OfficialEvidence(
        "TWSE", "DAY", "2026-10-22", EvidenceStatus.TRADED,
        ("2026-10-22",), "mock://twse", "2026-10-23T05:00:00+08:00",
    )
    exception = build_exception(
        "2026-10-22", "2026-10-21", "finlab", official,
        configured_is_session=True, retry_count=3,
    )
    closure_evidence = OfficialEvidence(
        "TWSE", "DAY", "2026-10-23", EvidenceStatus.NO_RECORD,
        ("2026-10-22",), "mock://twse-announcement", "2026-10-24T05:00:00+08:00",
    )
    closure = build_exception(
        "2026-10-23", "2026-10-22", "finlab", closure_evidence,
        configured_is_session=False, retry_count=3,
    )
    review = process_review_row({
        **asdict(closure),
        "review_decision": "CONFIRM_CLOSED",
        "review_confirm": True,
    })
    return {
        "checkpoints": [asdict(primary), asdict(fallback), asdict(final)],
        "official_trade_classification": exception.exception_type,
        "closure_classification": closure.exception_type,
        "review_processing_status": review.processing_status,
        "override_reconciliation_status": review.override["reconciliation_status"],
        "production_sheet_written": False,
    }


def run(path: str | Path = "outputs/calendar_resilience_simulation.json") -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_simulation_report(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
