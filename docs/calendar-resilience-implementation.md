# Calendar resilience implementation

## Production checkpoints

| Taipei time | Trigger | Target rule | Duplicate guard |
| --- | --- | --- | --- |
| 20:13 | existing `repository_dispatch` | current configured session | validated publication date |
| 20:47 | GitHub schedule `47 12 * * 1-5` | current configured session | validated publication date |
| 05:00 | GitHub schedule `0 21 * * *` | oldest unfinished date among the last three expected sessions through the prior day | persisted latest successful publication |

Manual dispatch selects checkpoint semantics but uses the same calendar and
completion gates. It cannot force a non-session or already-completed date.

## Failure classification

Official evidence is fetched only after the final FinLab attempt fails. The TWSE
parser requires the requested Gregorian date to exist in the returned ROC-date
rows. The TAIFEX parser independently filters `Contract=TX` and the requested
`TradingSession`; TWSE evidence is never used to infer night-session state.

Live smoke test on 2026-10-28:

- TWSE `FMTQIK` returned 16 October rows through 2026-10-27. Exact 2026-10-27
  was `TRADED`; exact 2026-10-28 was `NO_RECORD` at test time.
- TAIFEX `DailyMarketReportFut` returned exact 2026-10-27 TX rows for both
  `一般` and `盤後` sessions.

`NO_RECORD` is never `CLOSED`. It becomes `UNKNOWN` for a configured session and
`SUSPECTED_CLOSED` only when the configured calendar already says closed; either
state still requires human review.

Exact-date official trading evidence that contradicts a configured closure is
`CALENDAR_CONFLICT`, not `DATA_DELAYED`. Conflict rows are forced to `ON_HOLD`;
neither the review worker nor the apply approval reader can promote them.

## Review sheets

The opt-in review connector creates:

- `pipeline_exceptions`: deterministic event ID, exact-date evidence, retry and
  review state; retries update the same event and preserve human fields.
- `calendar_overrides`: confirmed exception metadata and reconciliation state.

The review workflow is disabled until `CALENDAR_REVIEW_ENABLED=true`. It uses a
workflow concurrency group and an optimistic claim token. `CONFIRM_CLOSED`
creates only an `APPROVED_STAGING` / `STAGING_REQUIRED` override. It does not
edit the configured calendar or production ledger.

## Write-safety boundary

The reconciliation planner verifies persisted targets against the old
calendar, recalculates by trading-session ordinal, records row-level diffs,
checks negative controls, and blocks unresolved mappings.

`dashboard.calendar_apply_bundle` builds the immutable bundle from the rebuilt
canonical CSVs and validated Work Site payload, embeds every dataset checksum,
and verifies a whole-bundle checksum on round-trip. `dashboard.calendar_apply`
is the production-capable executor. It accepts only that bundle and has no
force/bypass flag. Immediately
before the single Sheets visibility change it revalidates:

1. accessible immutable backup ID and projection checksum
2. source revision/checksum and calendar version/checksum
3. reconciliation-plan and row-level-diff checksums
4. target schemas, event approval version and negative controls
5. publication metadata against all rebuilt dataset checksums

The executor writes `signal_events` and `forecast_calendar` in one atomic
`spreadsheets.batchUpdate`, verifies readback, then atomically replaces the
existing fixed-path Work Site payload. Thus the reader contract remains
backward compatible. A pointer/readback failure restores the pre-apply Sheet
snapshot and previous publication. Failed recovery becomes `RECOVERY_REQUIRED`
and blocks reuse of that operation ID.

Successful operations are immutable/idempotent no-ops on retry. A reversal is
a distinct operation that uses the same gates and records `superseded_by` on
the original manifest; it never restores an old whole-Sheet backup over newer
market data.

The executor remains unscheduled while `CALENDAR_REVIEW_ENABLED` is disabled.
No production apply or backfill is performed merely by merging this code.
