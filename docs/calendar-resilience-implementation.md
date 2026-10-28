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

The pure reconciliation planner verifies persisted targets against the old
calendar, recalculates by trading-session ordinal, records row-level diffs,
checks negative controls, and blocks unresolved mappings. The in-memory apply is
idempotent and rejects duplicate canonical identities.

Production apply remains gated until all of the following are supplied and
verified in the deployment environment:

1. immutable Google Sheet backup identifier
2. matching calendar/source checksums
3. staging plan checksum and row-level diff
4. rebuilt forecast calendar and historical validation
5. post-write readback
6. rollback/reversal event path

Because Google Sheets has no cross-tab transaction, the intended release is
backup → staging artifacts → canonical tabs → readback → publication pointer.
The previous publication stays active until final readback passes.
