"""Pure dry-run analysis for approved calendar revisions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path

import pandas as pd

from .signal_engine import SignalEvent
from .signal_ledger import LEDGER_KEY, merge_signal_events
from .trading_calendar import normalize_sessions, target_session


@dataclass(frozen=True)
class TargetDiff:
    signal_date: str
    signal_id: str
    horizon: int
    old_target_date: str | None
    new_target_date: str
    reason: str


@dataclass(frozen=True)
class ReconciliationPlan:
    calendar_version: str
    source_checksum: str
    total_rows: int
    changed_rows: int
    unaffected_rows: int
    unresolved_rows: int
    negative_controls_passed: bool
    diffs: tuple[TargetDiff, ...]

    def checksum(self) -> str:
        raw = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()


def build_reconciliation_plan(
    events: tuple[SignalEvent, ...],
    old_sessions: pd.Index | list[object],
    new_sessions: pd.Index | list[object],
    *,
    calendar_version: str,
    source_checksum: str,
) -> ReconciliationPlan:
    old_index = normalize_sessions(old_sessions)
    new_index = normalize_sessions(new_sessions)
    diffs: list[TargetDiff] = []
    unresolved = 0
    negative_controls_passed = True
    changed_session_dates = set(old_index.symmetric_difference(new_index))
    for event in events:
        try:
            old_target = target_session(event.signal_date, event.horizon, old_index)
            new_target = target_session(event.signal_date, event.horizon, new_index)
        except LookupError:
            unresolved += 1
            continue
        persisted = pd.Timestamp(event.target_date).normalize() if event.target_date else None
        if persisted != old_target:
            # Pre-existing target errors are not silently folded into this repair.
            unresolved += 1
            continue
        if old_target != new_target:
            diffs.append(TargetDiff(
                event.signal_date, event.signal_id, event.horizon,
                str(old_target.date()), str(new_target.date()),
                "calendar_session_ordinal_changed",
            ))
        elif not any(pd.Timestamp(event.signal_date) < day <= old_target for day in changed_session_dates):
            negative_controls_passed &= persisted == new_target
    return ReconciliationPlan(
        calendar_version, source_checksum, len(events), len(diffs),
        len(events) - len(diffs) - unresolved, unresolved,
        negative_controls_passed, tuple(diffs),
    )


def apply_plan_in_memory(
    events: tuple[SignalEvent, ...], plan: ReconciliationPlan
) -> tuple[SignalEvent, ...]:
    if plan.unresolved_rows or not plan.negative_controls_passed:
        raise RuntimeError("reconciliation safety gate failed")
    replacements = {
        (row.signal_date, row.signal_id, row.horizon): row.new_target_date
        for row in plan.diffs
    }
    revised = tuple(
        replace(event, target_date=replacements.get(
            (event.signal_date, event.signal_id, event.horizon), event.target_date
        ))
        for event in events
    )
    if len(merge_signal_events((), revised)) != len(revised):
        raise RuntimeError("reconciliation created duplicate canonical events")
    return revised


def write_plan(path: str | Path, plan: ReconciliationPlan) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target
