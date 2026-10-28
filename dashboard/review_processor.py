"""Pure review decisions; persistence/claiming is handled by calendar_review."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class ReviewResult:
    processing_status: str
    override: dict[str, Any] | None = None


def process_review_row(row: dict[str, Any], *, now: datetime | None = None) -> ReviewResult:
    decision = str(row.get("review_decision", "")).strip().upper()
    status = str(row.get("processing_status", "PENDING_REVIEW"))
    if str(row.get("exception_type", "")).strip().upper() == "CALENDAR_CONFLICT":
        # A calendar/official-data contradiction requires investigation and a
        # new reconciled evidence version.  Human checkbox state alone cannot
        # turn the contradiction into an approved closure or production write.
        if decision in {"", "INVESTIGATE"}:
            return ReviewResult("ON_HOLD")
        raise RuntimeError("CALENDAR_CONFLICT blocks automated approval and production apply")
    timestamp = (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat()
    if decision == "INVESTIGATE":
        return ReviewResult("ON_HOLD")
    if decision == "CANCEL":
        if status in {"APPLIED", "RECONCILED", "PUBLISHED"}:
            raise RuntimeError("published event requires a reversal event")
        return ReviewResult("CANCELLED")
    if decision == "CONFIRM_OPEN":
        return ReviewResult("RETRY_PENDING")
    if decision != "CONFIRM_CLOSED":
        raise ValueError(f"unsupported review decision: {decision}")

    evidence = json.loads(str(row.get("official_market_evidence", "{}")) or "{}")
    override = {
        "event_id": row["event_id"],
        "date": row["target_data_date"],
        "market": evidence.get("market", "TWSE"),
        "session": evidence.get("session", "DAY"),
        "status": "CLOSED",
        "reason": f"human approved {row.get('exception_type', '')}",
        "official_source": evidence.get("source_url", ""),
        "approved": True,
        "approved_at": timestamp,
        "calendar_version": "PENDING_RECONCILIATION",
        "reconciliation_status": "STAGING_REQUIRED",
    }
    return ReviewResult("APPROVED_STAGING", override)
