# AGENTS.md --- taiwan-market-thermometer

## Repository purpose

This repository converts validated Taiwan-market research evidence into
auditable daily signal events, target trading dates, a Forecast
Calendar, Google Sheet records, email output, Streamlit views, and
immutable run archives.

It is an evidence-monitoring system, not a trading-recommendation
engine.

## Inherited engineering rules

Follow the project's general quant-pipeline rules: audit repository
state before changes; Python 3.11; GitHub as code source of truth; no
secret hard-coding; explicit typed boundaries; backward-compatible
schema handling; idempotent state; no silent coercion; one core change
at a time; test before commit/push.

## Observability and diagnostics

Every production or long-running pipeline must be observable from CI/Colab logs without attaching a debugger.

For each major stage, emit bounded progress with:

- stage number/name
- `START`
- `DONE` or `FAIL`
- elapsed time
- relevant row/event counts and data date when useful

Long loops that can take more than roughly 30 seconds should emit bounded `x/y` progress updates rather than logging every row.

Diagnostics must be designed from invariants at implementation time, not only after a failure. External boundaries such as FinLab, Google Sheets, CSV, Drive, archives, and persistent ledgers should expose enough non-secret metadata to verify schema/shape, date coverage, missing required fields, optional missingness, duplicate keys, identifier dtype, state consistency, and row counts as applicable.

A successful process exit is not sufficient evidence of business success. Distinguish execution health, data health, research-contract health, and output completion.

Before implementing a new long-running stage, explicitly consider:

`Stage → Input contract → Invariant → Observable → Failure diagnostic`

Never log secrets, tokens, credentials, private raw records, or unnecessarily large datasets. Observability changes must not alter canonical research rules or business-state semantics.

Diagnostic-only code is best-effort and must not become a new production failure mode. Before logging counts/shape/date from a domain object, inspect its actual typed interface; do not assume every loader returns a DataFrame or supports `len()`. A diagnostic formatting/emission failure should degrade to a bounded diagnostic warning (or be skipped if the log sink itself fails), while required stage/business exceptions must still fail loudly.

## Canonical research contract

Canonical research definitions come from the repository research
registry and the referenced formal research outputs.

Do not alter, merely to repair pipeline/UI/storage behavior:

-   `RETAINED` / `RETEST` / `REJECTED`
-   signal thresholds
-   rolling PR/Z definition
-   strict-prior vs current-inclusive normalization
-   accumulation windows
-   `economic_signal_id`
-   O1→Cn horizon definitions
-   evidence grade/FDR/statistics
-   research commit provenance

If production feasibility conflicts with a formal research
specification, report the conflict. Do not silently rewrite the research
rule.

## Timing and no-look-ahead contract

A signal reconstructed for `signal_date=d0` may use only data available
as of d0.

Historical backfill must truncate every input series as-of d0 before
evaluation. Future mutations must not change a past event.

`target_date` must use the Taiwan trading-session index, not
calendar-day arithmetic.

Formal outcome:

`O1→Cn = adjusted_close[d0+n] / adjusted_open[d0+1] - 1`

Do not substitute C0→Cn or forward-filled prices.

## Signal ledger contract

`signal_events` is cumulative evidence history.

Canonical event identity is based on signal vintage, including at least:

`(signal_date, signal_id, horizon)`

Rules:

-   `PRODUCTION` beats `BACKFILL` for the same canonical event.
-   Backfill must never overwrite a real production observation.
-   Backfill and production provenance must remain visible.
-   Reruns must be idempotent.
-   `VALID_NO_SIGNAL` must remain distinguishable from a missing run.
-   A day with no matching signal still requires a run/audit record.

When aggregating to a target date, do not accidentally double-count
robustness variants of the same economic hypothesis. Any
counting/deduplication policy must be explicit and tested.

## Target-Date Forecast Calendar

Two concepts must remain separate:

1.  Forward View: current signal-date events projected to future target
    dates.
2.  Target-Date View: historical signal vintages whose horizons converge
    on the same target trading date.

The Target-Date View must be derived from the cumulative ledger, not
only from the current run.

Vote counts are descriptive evidence counts. They are not probability,
expected return, confidence, or a trading recommendation.

## Persistent-data boundary contract

Google Sheet, CSV, Drive history, and old archives are serialized
external data.

For `SignalEvent` restoration:

-   optional numeric blanks (`None`, `NaN`, `""`, whitespace) → `None`,
    never `0`
-   numeric strings may be parsed numerically
-   required identifiers/dates must not be silently invented
-   legacy missing optional fields may use documented defaults only when
    semantics are unambiguous
-   blank legacy `event_origin` may map to the documented
    backward-compatible origin
-   availability state must not convert missing data into a valid
    no-signal event

Do not use blanket `fillna(0)` or broad `astype(float)` on the event
ledger.

Any event-schema change requires a legacy-row regression test.

## Current-run handoff

Colab/production calculation and Streamlit should consume the same
current-run artifacts.

Source priority is:

`CURRENT_RUN → LIVE_FINLAB → RESEARCH_SNAPSHOT`

A valid current run must not trigger a second live FinLab calculation
merely for display.

`RESEARCH_SNAPSHOT` is fallback/preview data and must never be presented
as current production market data.

## Pipeline-state semantics

Keep these meanings distinct:

-   `SUCCESS`: required stages completed
-   `VALID_NO_SIGNAL`: valid calculation, no retained signal matched
-   `DATA_UNAVAILABLE`: required data unavailable
-   `STALE_DATA`: actual market data is not the expected trading date
-   `PENDING`: an outcome has not matured

Never display `DATA_UNAVAILABLE` as `0 多 / 0 空`.

Production availability must be determined only from production-eligible
signals: `RETAINED` and `activation_ready=True`. Research-only, `RETEST`,
`REJECTED`, and activation-not-ready signals must not make the production
forecast `DATA_UNAVAILABLE` or enter formal vote counts.

`VALID_NO_SIGNAL` means all required production signals were evaluable but
none matched. `DATA_UNAVAILABLE` means at least one required production signal
could not be evaluated. `ROLLING_WARMUP` is not `VALID_NO_SIGNAL`.

A future realized outcome is `PENDING`; it is not signal-data unavailability
and must not invalidate an otherwise complete forward forecast.

GitHub Actions `success` is insufficient by itself. Business completion
must be represented by validated manifest/stage output.

## FinLab and headless execution

CI/headless execution must use the repository's validated headless
authentication path. Do not reintroduce browser login into GitHub
Actions or Streamlit server execution.

Do not expose credential values in logs, diagnostics, exceptions, tests,
or artifacts.

## Colab Streamlit compatibility

Preserve the known Colab proxy-compatible Streamlit launch behavior
unless the task explicitly changes it. A refactor must not silently
remove required WebSocket/CORS/XSRF/file-watcher settings.

## Archive contract

Run archives are immutable and traceable to commit hash.

Do not overwrite prior run directories.

Cumulative history may be updated atomically in its designated history
location, but per-run archives remain immutable snapshots.

## Minimum tests for changes touching signal history

In addition to relevant existing tests, cover as applicable:

-   as-of backfill invariant against future-data mutation
-   exact trading-session target mapping
-   inclusive vs strict-prior normalization
-   blank legacy optional cells
-   production-over-backfill precedence
-   idempotent ledger merge/upsert
-   multiple historical vintages converging on one target date
-   economic-signal deduplication
-   valid no-signal vs missing/stale data
-   current-run handoff without redundant FinLab authentication

## Research conclusion

Until the historical ledger, target-date aggregation, and
realized-outcome path have been validated end-to-end with actual
production runs, the research-system conclusion remains:

`修改後再測`
