# Calendar resilience development plan

## Scope

This branch starts from production `main` after the 2026-10-23 closure hotfix. The work must preserve the existing pipeline and canonical research contract. It will be delivered as small reviewable changes; it will not rewrite `dashboard.cloud_daily`.

## Current architecture

- Calendar rules and session arithmetic: `dashboard/trading_calendar.py` plus `config/twse_closed_dates.csv`.
- Signal target generation: `dashboard/signal_engine.py`; cumulative merge/backfill: `dashboard/signal_ledger.py`.
- Outcome and historical validation: `dashboard/outcomes.py` and `dashboard/historical_validation.py`.
- Production orchestration and freshness validation: `dashboard/cloud_daily.py`.
- Google Sheet persistence: `dashboard/google_sheet_service.py`.
- Current-run artifacts, manifest and publication gates: `dashboard/dashboard_source.py`, `dashboard/run_manifest.py`, `dashboard/archive_service.py`.
- Scheduled production entrypoint: `.github/workflows/daily-cloud-report.yml`.

## Minimal design

### 1. Centralized trading calendar

Extend `dashboard/trading_calendar.py` behind a typed `TradingCalendar` interface. Keep the existing helper functions as compatibility wrappers. The interface will expose:

- `is_session(date, market, session_type)`
- `sessions(start, end, market, session_type)`
- `target_session(signal_date, horizon, market, session_type)`
- `expected_latest_session(now, publication_cutoff, market, session_type)`
- calendar version and source metadata

Use an explicit versioned calendar data file. Do not infer closures from missing market observations.

### 2. Closure versus data-delay state

Add a small typed status model at the data-fetch boundary:

- `OPEN_COMPLETE`
- `OPEN_DELAYED`
- `OFFICIAL_CLOSED`
- `MAPPING_ERROR`
- `DATA_MISSING`

`cloud_daily.py` will consume this result rather than comparing two dates directly. Existing manifest states remain backward compatible.

### 3. Calendar version and change detection

Store a deterministic checksum/version in the run manifest and immutable archive. On startup, compare it with the previous successful run. A changed past session must create a bounded reconciliation report and stop automatic historical writes until impact analysis passes.

### 4. Affected-row dependency analysis

Add a pure analysis module that accepts old/new calendars and the cumulative ledger. It returns exact canonical event keys affected in:

- target date
- entry/exit mapping
- outcome/label
- forecast calendar
- historical validation
- materialized publication artifacts

Rows without an explained calendar difference are `UNRESOLVED` and block backfill.

### 5. Idempotent backfill

Reuse the existing signal identity `(signal_date, signal_id, horizon)`, production-over-backfill precedence, canonical outcome calculation and exact forecast-calendar replacement. Add a two-phase operation:

1. dry-run with row-level diff and checksums
2. atomic/retryable apply with backup reference and post-write readback

The apply phase must be a no-op when rerun with the same calendar version and input checksums.

### 6. Regression coverage

Add focused tests for:

- temporary day-session closure across C1/C2/C3/C5/C10/C20
- a closure added after signals were persisted
- normal open day with delayed/missing data
- TWSE day session versus TAIFEX day/night session identity
- Asia/Taipei rollover and overnight session date
- negative controls outside the changed interval
- idempotent backfill and production precedence
- unresolved mapping blocks writes
- downstream rebuild and manifest calendar-version consistency

### 7. Staging and recoverable release

Add a staging validation command used by Actions. It creates artifacts and diffs but cannot write the production Sheet. Production apply requires:

- verified staging manifest
- backup identifier
- matching source/calendar checksums
- atomic write or explicit rollback
- post-write readback
- final production manifest

## Delivery sequence

1. Typed calendar interface and compatibility tests.
2. Version/checksum metadata and status separation.
3. Dependency analyzer and dry-run report.
4. Idempotent apply/rollback boundary.
5. TAIFEX night-session regression fixtures.
6. Staging workflow and production acceptance gates.

Each step must keep the existing 227-test baseline green and add only the tests required for that step. No production Sheet write is part of the planning commit.

## Implemented staging boundary

The feature branch now contains the first incremental implementation:

- three checkpoint target resolver with persisted-completion guard
- exact-date TWSE and product/session-specific TAIFEX evidence parsers
- deterministic exception identity and review state machine
- opt-in Google Sheet review tabs with validation controls
- optimistic event claiming plus workflow concurrency
- pure calendar reconciliation dry-run with negative-control and unresolved-row gates
- calendar version metadata in the run manifest

The review workflow is inert unless `CALENDAR_REVIEW_ENABLED=true`. A confirmed
closure creates an approved staging override only; it cannot mutate the configured
calendar or production ledger. The guarded production apply command now enforces
backup, source/calendar/plan/diff checksums, approval version, negative controls,
atomic canonical Sheet replacement, readback, publication switching, rollback,
and separately audited reversal operations. It is intentionally not wired to an
automatic workflow until production acceptance enables the review feature.
