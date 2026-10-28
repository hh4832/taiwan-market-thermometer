from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from dashboard.calendar_reconciliation import apply_plan_in_memory, build_reconciliation_plan
from dashboard.official_market import EvidenceStatus, OfficialEvidence, fetch_taifex_evidence, fetch_twse_evidence
from dashboard.pipeline_exceptions import (
    ExceptionType,
    build_exception,
    claim_review_event,
    classify_exception,
    upsert_exception_records,
)
from dashboard.pipeline_schedule import RunPhase, decide_run, phase_for_github_event
from dashboard.review_processor import process_review_row
from dashboard.calendar_review import process_pending_reviews
from dashboard.google_sheet_service import (
    CALENDAR_OVERRIDE_HEADERS,
    PIPELINE_EXCEPTION_HEADERS,
    sync_pipeline_exception,
)
from dashboard.signal_engine import SignalEvent
from dashboard.trading_calendar import TradingCalendar


def calendar(*closed: str) -> TradingCalendar:
    holidays = pd.DatetimeIndex(list(closed) or ["2026-01-01"])
    return TradingCalendar(holidays, "test", "test-v1")


def event(**changes) -> SignalEvent:
    values = dict(
        signal_date="2026-10-22", signal_id="a", economic_signal_id="family",
        source="test", subject="test", direction="bullish", horizon=1,
        target_date="2026-10-23", matched=True, evaluation_status="MATCHED",
        research_status="RETAINED", evidence_grade="A", raw_value=1.0,
        normalized_value=99.0, threshold="x", historical_mean_return=None,
        historical_median_return=None, historical_win_rate=None,
        relative_mean_return=None, sample_size=None, global_fdr=None,
        family_fdr=None, plain_definition="x", market_mechanism="x", risks="x",
        research_commit="abc", research_run="run", calculation_timestamp="now",
        source_data_date="2026-10-22",
    )
    values.update(changes)
    return SignalEvent(**values)


def evidence(status=EvidenceStatus.NO_RECORD, market="TWSE", session="DAY"):
    return OfficialEvidence(
        market, session, "2026-10-23", status, (), "https://official.example", "2026-10-24T05:00:00+08:00"
    )


def test_2013_success_makes_later_checkpoints_skip():
    cal = calendar("2026-10-09", "2026-10-26")
    primary = decide_run("2026-10-22 20:13+08:00", RunPhase.PRIMARY, set(), calendar=cal)
    assert primary.should_run and primary.target_data_date == "2026-10-22"
    completed = {primary.target_data_date}
    assert not decide_run("2026-10-22 20:47+08:00", RunPhase.FALLBACK, completed, calendar=cal).should_run
    assert not decide_run("2026-10-23 05:00+08:00", RunPhase.FINAL, completed, calendar=cal).should_run


def test_2013_failure_2047_retries_same_target():
    cal = calendar("2026-10-09", "2026-10-26")
    fallback = decide_run("2026-10-22 20:47+08:00", RunPhase.FALLBACK, set(), calendar=cal)
    assert fallback.should_run and fallback.target_data_date == "2026-10-22"


def test_evening_failures_final_checkpoint_retries_previous_session():
    cal = calendar("2026-10-09", "2026-10-26")
    final = decide_run("2026-10-23 05:00+08:00", RunPhase.FINAL, {"2026-10-20", "2026-10-21"}, calendar=cal)
    assert final.should_run and final.target_data_date == "2026-10-22"


def test_friday_failure_is_retried_on_saturday_0500():
    cal = calendar("2026-10-09", "2026-10-26")
    final = decide_run("2026-10-31 05:00+08:00", RunPhase.FINAL, {"2026-10-28", "2026-10-29"}, calendar=cal)
    assert final.target_data_date == "2026-10-30"


def test_consecutive_holidays_and_cross_month_final_target():
    cal = calendar("2026-09-25", "2026-09-28", "2026-10-09")
    result = decide_run("2026-09-29 05:00+08:00", RunPhase.FINAL, {"2026-09-23"}, calendar=cal)
    assert result.target_data_date == "2026-09-24"


def test_manual_dispatch_does_not_create_bypass_phase():
    assert phase_for_github_event("workflow_dispatch", None, "FINAL") == RunPhase.FINAL
    cal = calendar("2026-10-23")
    assert not decide_run("2026-10-23 12:00+08:00", RunPhase.PRIMARY, set(), calendar=cal).should_run


def test_twse_parser_requires_exact_trading_date_not_http_success():
    payload = {"stat": "OK", "data": [["115/10/22", "1", "2"]]}
    result = fetch_twse_evidence("2026-10-23", get_json=lambda *_: payload)
    assert result.status == EvidenceStatus.NO_RECORD
    assert result.observed_dates == ("2026-10-22",)
    traded = fetch_twse_evidence("2026-10-22", get_json=lambda *_: payload)
    assert traded.status == EvidenceStatus.TRADED


def test_twse_failure_is_unknown_not_closed():
    def failure(*_args):
        raise TimeoutError("down")
    result = fetch_twse_evidence("2026-10-23", get_json=failure)
    assert result.status == EvidenceStatus.API_FAILED
    assert classify_exception(
        expected_date="2026-10-23", actual_date="2026-10-22", evidence=result, configured_is_session=True
    ) == ExceptionType.UNKNOWN


def test_taifex_night_session_is_not_inferred_from_twse():
    payload = [
        {"Date": "20261023", "Contract": "TX", "TradingSession": "一般"},
        {"Date": "20261022", "Contract": "TX", "TradingSession": "盤後"},
    ]
    night = fetch_taifex_evidence("2026-10-23", get_json=lambda *_: payload, session="盤後")
    day = fetch_taifex_evidence("2026-10-23", get_json=lambda *_: payload, session="一般")
    assert night.status == EvidenceStatus.NO_RECORD
    assert day.status == EvidenceStatus.TRADED


def test_finlab_missing_official_trade_is_data_delayed():
    assert classify_exception(
        expected_date="2026-10-23", actual_date="2026-10-22",
        evidence=evidence(EvidenceStatus.TRADED), configured_is_session=True,
    ) == ExceptionType.DATA_DELAYED


@pytest.mark.parametrize(("market", "session"), [
    ("TWSE", "DAY"), ("TAIFEX", "一般"), ("TAIFEX", "盤後"),
])
def test_configured_closure_with_official_trade_is_calendar_conflict(market, session):
    assert classify_exception(
        expected_date="2026-10-23", actual_date="2026-10-22",
        evidence=evidence(EvidenceStatus.TRADED, market=market, session=session),
        configured_is_session=False,
    ) == ExceptionType.CALENDAR_CONFLICT


def test_calendar_conflict_cannot_be_human_approved_into_closed_override():
    row = {
        "event_id": "conflict", "target_data_date": "2026-10-23",
        "review_decision": "CONFIRM_CLOSED", "review_confirm": True,
        "exception_type": "CALENDAR_CONFLICT",
        "official_market_evidence": evidence(EvidenceStatus.TRADED).as_json(),
    }
    with pytest.raises(RuntimeError, match="blocks"):
        process_review_row(row)


def test_calendar_conflict_worker_holds_without_claim_or_override():
    row = {
        "event_id": "conflict", "target_data_date": "2026-10-23",
        "review_decision": "CONFIRM_CLOSED", "review_confirm": "TRUE",
        "exception_type": "CALENDAR_CONFLICT", "processing_status": "PENDING_REVIEW",
    }
    exceptions = FakeSheet(
        PIPELINE_EXCEPTION_HEADERS,
        [[row.get(name, "") for name in PIPELINE_EXCEPTION_HEADERS]],
    )
    overrides = FakeSheet(CALENDAR_OVERRIDE_HEADERS)
    assert process_pending_reviews(exceptions, overrides, "worker") == 1
    stored = dict(zip(PIPELINE_EXCEPTION_HEADERS, exceptions.values[1]))
    assert stored["processing_status"] == "ON_HOLD"
    assert len(overrides.values) == 1


def test_no_official_row_never_confirms_closure_without_review():
    assert classify_exception(
        expected_date="2026-10-23", actual_date="2026-10-22",
        evidence=evidence(), configured_is_session=True,
    ) == ExceptionType.UNKNOWN
    assert classify_exception(
        expected_date="2026-10-23", actual_date="2026-10-22",
        evidence=evidence(), configured_is_session=False,
    ) == ExceptionType.SUSPECTED_CLOSED


def test_same_exception_is_idempotently_updated_and_review_is_preserved():
    first = build_exception(
        "2026-10-23", "2026-10-22", "finlab", evidence(), configured_is_session=True, retry_count=3
    )
    stored = [{**first.__dict__, "review_decision": "INVESTIGATE", "review_confirm": True}]
    second = replace(first, retry_count=4)
    result = upsert_exception_records(stored, second)
    assert len(result) == 1
    assert result[0]["retry_count"] == 4
    assert result[0]["review_decision"] == "INVESTIGATE"


def test_repeated_approval_cannot_reclaim_processed_event():
    row = {
        "event_id": "e", "review_decision": "CONFIRM_OPEN", "review_confirm": "TRUE",
        "processing_status": "PENDING_REVIEW",
    }
    claimed = claim_review_event(row, "w1")
    assert claimed["processing_status"] == "CLAIMED:w1"
    with pytest.raises(RuntimeError):
        claim_review_event(claimed, "w2")


def test_cancel_before_write_and_reversal_required_after_publish():
    assert process_review_row({"review_decision": "CANCEL", "processing_status": "PENDING_REVIEW"}).processing_status == "CANCELLED"
    with pytest.raises(RuntimeError, match="reversal"):
        process_review_row({"review_decision": "CANCEL", "processing_status": "PUBLISHED"})


def test_confirm_closed_creates_staging_override_not_applied_override():
    row = {
        "event_id": "e", "target_data_date": "2026-10-23",
        "review_decision": "CONFIRM_CLOSED", "exception_type": "SUSPECTED_CLOSED",
        "official_market_evidence": evidence().as_json(),
    }
    result = process_review_row(row)
    assert result.processing_status == "APPROVED_STAGING"
    assert result.override["approved"] is True
    assert result.override["calendar_version"] == "PENDING_RECONCILIATION"
    assert result.override["reconciliation_status"] == "STAGING_REQUIRED"


def test_calendar_change_recomputes_target_and_negative_control_stays_unchanged():
    events = (
        event(),
        event(signal_date="2026-10-01", signal_id="negative", target_date="2026-10-02"),
    )
    old = pd.bdate_range("2026-10-01", "2026-11-30")
    new = old.drop(pd.Timestamp("2026-10-23"))
    plan = build_reconciliation_plan(events, old, new, calendar_version="v2", source_checksum="source")
    assert plan.changed_rows == 1
    assert plan.unresolved_rows == 0
    assert plan.negative_controls_passed
    revised = apply_plan_in_memory(events, plan)
    assert revised[0].target_date == "2026-10-26"
    assert revised[1].target_date == "2026-10-02"
    assert apply_plan_in_memory(revised, build_reconciliation_plan(
        revised, new, new, calendar_version="v2", source_checksum="source"
    )) == revised


def test_preexisting_bad_target_blocks_formal_write():
    bad = (event(target_date="2026-10-21"),)
    sessions = pd.bdate_range("2026-10-01", "2026-11-30")
    plan = build_reconciliation_plan(bad, sessions, sessions, calendar_version="v", source_checksum="s")
    assert plan.unresolved_rows == 1
    with pytest.raises(RuntimeError, match="safety gate"):
        apply_plan_in_memory(bad, plan)


class FakeSheet:
    def __init__(self, headers, rows=()):
        self.values = [headers] + [list(row) for row in rows]

    def get_all_values(self):
        return self.values

    def update(self, matrix, cell="A1", **_kwargs):
        if cell == "A1":
            self.values = matrix
            return
        row_number = int(cell[1:])
        while len(self.values) < row_number:
            self.values.append([])
        self.values[row_number - 1] = matrix[0]

    def freeze(self, **_kwargs):
        return None


def test_exception_sheet_upsert_is_idempotent_and_preserves_human_review():
    item = build_exception(
        "2026-10-23", "2026-10-22", "finlab", evidence(), configured_is_session=True, retry_count=3
    )
    sheet = FakeSheet(PIPELINE_EXCEPTION_HEADERS)
    assert sync_pipeline_exception(sheet, item) == 1
    row = dict(zip(PIPELINE_EXCEPTION_HEADERS, sheet.values[1]))
    row["review_decision"] = "INVESTIGATE"
    row["review_confirm"] = "TRUE"
    sheet.values[1] = [row.get(name, "") for name in PIPELINE_EXCEPTION_HEADERS]
    assert sync_pipeline_exception(sheet, replace(item, retry_count=4)) == 1
    stored = dict(zip(PIPELINE_EXCEPTION_HEADERS, sheet.values[1]))
    assert stored["review_decision"] == "INVESTIGATE"
    assert stored["retry_count"] == 4


def test_review_worker_claims_then_creates_staging_override():
    item = build_exception(
        "2026-10-23", "2026-10-22", "finlab", evidence(), configured_is_session=False, retry_count=3
    )
    row = item.__dict__ | {"review_decision": "CONFIRM_CLOSED", "review_confirm": "TRUE"}
    exceptions = FakeSheet(
        PIPELINE_EXCEPTION_HEADERS,
        [[row.get(name, "") for name in PIPELINE_EXCEPTION_HEADERS]],
    )
    overrides = FakeSheet(CALENDAR_OVERRIDE_HEADERS)
    assert process_pending_reviews(exceptions, overrides, "worker") == 1
    processed = dict(zip(PIPELINE_EXCEPTION_HEADERS, exceptions.values[1]))
    override = dict(zip(CALENDAR_OVERRIDE_HEADERS, overrides.values[1]))
    assert processed["processing_status"] == "APPROVED_STAGING"
    assert override["status"] == "CLOSED"
    assert override["reconciliation_status"] == "STAGING_REQUIRED"
    assert len(overrides.values) == 2


def test_review_worker_does_not_repeat_applied_or_claimed_event():
    row = {
        "event_id": "e", "target_data_date": "2026-10-23",
        "review_decision": "CONFIRM_OPEN", "review_confirm": "TRUE",
        "processing_status": "CLAIMED:other",
    }
    exceptions = FakeSheet(
        PIPELINE_EXCEPTION_HEADERS,
        [[row.get(name, "") for name in PIPELINE_EXCEPTION_HEADERS]],
    )
    assert process_pending_reviews(exceptions, FakeSheet(CALENDAR_OVERRIDE_HEADERS), "worker") == 0


def test_cancel_can_stop_approved_staging_before_formal_write():
    row = {
        "event_id": "e", "target_data_date": "2026-10-23",
        "review_decision": "CANCEL", "review_confirm": "TRUE",
        "processing_status": "APPROVED_STAGING",
    }
    exceptions = FakeSheet(
        PIPELINE_EXCEPTION_HEADERS,
        [[row.get(name, "") for name in PIPELINE_EXCEPTION_HEADERS]],
    )
    assert process_pending_reviews(exceptions, FakeSheet(CALENDAR_OVERRIDE_HEADERS), "worker") == 1
    stored = dict(zip(PIPELINE_EXCEPTION_HEADERS, exceptions.values[1]))
    assert stored["processing_status"] == "CANCELLED"
