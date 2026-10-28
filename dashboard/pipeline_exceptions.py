"""Idempotent exception/review state machine for calendar incidents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .official_market import EvidenceStatus, OfficialEvidence

TAIPEI = ZoneInfo("Asia/Taipei")
PIPELINE_EXCEPTION_HEADERS = [
    "event_id", "target_data_date", "detected_at", "expected_date", "actual_date",
    "affected_source", "official_market_evidence", "exception_type", "retry_count",
    "review_decision", "review_confirm", "processing_status", "last_run_url", "resolved_at",
]
CALENDAR_OVERRIDE_HEADERS = [
    "event_id", "date", "market", "session", "status", "reason", "official_source",
    "approved", "approved_at", "calendar_version", "reconciliation_status",
]
REVIEW_DECISIONS = {"", "CONFIRM_CLOSED", "CONFIRM_OPEN", "INVESTIGATE", "CANCEL"}


class ExceptionType(str, Enum):
    DATA_DELAYED = "DATA_DELAYED"
    UNKNOWN = "UNKNOWN"
    SUSPECTED_CLOSED = "SUSPECTED_CLOSED"
    CALENDAR_CONFLICT = "CALENDAR_CONFLICT"


@dataclass(frozen=True)
class PipelineException:
    event_id: str
    target_data_date: str
    detected_at: str
    expected_date: str
    actual_date: str
    affected_source: str
    official_market_evidence: str
    exception_type: str
    retry_count: int
    review_decision: str = ""
    review_confirm: bool = False
    processing_status: str = "PENDING_REVIEW"
    last_run_url: str = ""
    resolved_at: str = ""


def event_id_for(target_date: str, source: str, market: str, session: str) -> str:
    raw = f"{target_date}|{source}|{market}|{session}".encode()
    return "cal-" + hashlib.sha256(raw).hexdigest()[:20]


def classify_exception(
    *,
    expected_date: str,
    actual_date: str,
    evidence: OfficialEvidence,
    configured_is_session: bool,
) -> ExceptionType:
    if evidence.status == EvidenceStatus.TRADED:
        return ExceptionType.DATA_DELAYED
    if evidence.status == EvidenceStatus.API_FAILED:
        return ExceptionType.UNKNOWN
    if configured_is_session:
        # No trading row is evidence for review, never proof of a closure.
        return ExceptionType.UNKNOWN
    # An official/no-record result that agrees with a configured closure is
    # still only suspected until a human approves the cited announcement.
    return ExceptionType.SUSPECTED_CLOSED


def build_exception(
    target_date: str,
    actual_date: str,
    affected_source: str,
    evidence: OfficialEvidence,
    *,
    configured_is_session: bool,
    retry_count: int,
    last_run_url: str = "",
    now: datetime | None = None,
) -> PipelineException:
    kind = classify_exception(
        expected_date=target_date,
        actual_date=actual_date,
        evidence=evidence,
        configured_is_session=configured_is_session,
    )
    return PipelineException(
        event_id_for(target_date, affected_source, evidence.market, evidence.session),
        target_date,
        (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat(),
        target_date,
        actual_date,
        affected_source,
        evidence.as_json(),
        kind.value,
        retry_count,
        last_run_url=last_run_url,
    )


def upsert_exception_records(
    existing: list[dict[str, Any]], incoming: PipelineException
) -> list[dict[str, Any]]:
    records = [dict(row) for row in existing]
    for index, row in enumerate(records):
        if str(row.get("event_id")) == incoming.event_id:
            preserved = {
                name: row.get(name, "")
                for name in ("review_decision", "review_confirm", "processing_status", "resolved_at")
            }
            updated = asdict(incoming)
            updated.update({name: value for name, value in preserved.items() if value not in ("", None, False)})
            records[index] = updated
            return records
    records.append(asdict(incoming))
    return records


def claim_review_event(row: dict[str, Any], worker_id: str) -> dict[str, Any]:
    decision = str(row.get("review_decision", "")).strip().upper()
    confirmed = str(row.get("review_confirm", "")).strip().upper() in {"TRUE", "1", "YES"} or row.get("review_confirm") is True
    status = str(row.get("processing_status", "PENDING_REVIEW"))
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"unknown review decision: {decision}")
    if not confirmed or decision in {"", "INVESTIGATE"}:
        raise PermissionError("review event is not approved for automated processing")
    if status not in {"PENDING_REVIEW", "RETRY_PENDING"}:
        raise RuntimeError(f"review event cannot be claimed from {status}")
    result = dict(row)
    result["processing_status"] = f"CLAIMED:{worker_id}"
    return result


def write_staging_exception(path: str | Path, event: PipelineException) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(event), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target
