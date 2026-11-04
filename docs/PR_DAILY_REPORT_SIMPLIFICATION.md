# PR: Simplify the daily market report presentation

## Summary

- Replaces the detail-heavy daily email with four short sections: market conclusion, due formal forecasts, grouped RETEST watchlist, and spreadsheet details.
- Keeps the formal `forecast_calendar` output and all signal, threshold, research-status, FDR, and voting logic unchanged.
- Adds a configurable economic-signal-to-mechanism grouping and deduplicates watchlist rows by `economic_signal_id`.
- Adds episode-based, O1-to-Cn live tracking and an idempotently synchronized `live_track_record` worksheet.
- Retains variant and run-level detail in the existing `signal_events` and `run_audit` sheets, adding columns at the end of their schemas.

## 2026-11-04 snapshot

The old and new report snapshots are in `tests/fixtures/daily-report-2026-11-04/old-report.txt` and `new-report.txt`. The new report body through sections ①–③ is 15 lines and 577 characters; the complete message is 21 lines. The 0050 position fields show `—` because the archived run artifact does not contain a 60/120-session price series. The snapshot preserves the available official state, vote count, due-validation summary, historical hit rates, watchlist values, spreadsheet URL, write statistics, and disclaimer.

`signal_events` in the 2026-11-04 run contains dates from 2026-08-31 through 2026-11-04. Episode counts are explicitly scoped to that available ledger interval. Full-sample episode totals before 2026-08-31 are not inferred. To extend that history, load the matched-date/event-date outputs from each row's `canonical_registry.research_repo` (including `hh4832/-institutional-spot-flow-study`, `hh4832/options_market_return`, and `hh4832/taiwan-margin-short-0050-research`) into the historical event ledger. Until those dates are available, the report states its current coverage interval.

## Manual live-record check

For `spot_combined_foreign_sell10_z_ge25_c5`, the 2026-10-29 matched observation belongs to the episode beginning 2026-10-27: matched signal dates 10/27–10/30 and 11/02–11/04 are no more than five official trading sessions apart. The episode is counted once, using its first date. With O1 entry on 10/28 and C5 target on 11/03:

```text
adjusted_close[2026-11-03] / adjusted_open[2026-10-28] - 1
= 100.60 / 108.35 - 1
= -0.071527... = -7.2%
```

The event direction is bullish, so the due result is a miss (`hit=false`). The hand-check inputs are saved in `tests/fixtures/daily-report-2026-11-04/ohlc_manual_check.csv`. They are a public OHLC cross-check; production uses the FinLab adjusted OHLC series and the official Taiwan trading-session calendar.

## Validation

- `python -m pytest tests-python --ignore=tests-python/test_finlab_auth.py -q`: 225 passed.
- The full test command also exercises `test_finlab_auth.py`, but this checkout environment does not have the declared `finlab>=2.2.0,<3.0` dependency installed, so those nine auth tests cannot import the package here.
- The fixture test compares the generated formal forecast calendar with the frozen 2026-11-04 `forecast_calendar.csv` and asserts unchanged columns and records.
- The fixture tests assert one watchlist row per economic signal, old-detail field availability in sheet schemas, compact body length, and the 10/29 episode/live-return hand calculation.

## Files / sheet schema

- New `live_track_record` worksheet: required episode, direction, realized return, hit, and DUE/OPEN columns, plus mark-to-market and calculation-availability fields.
- Existing `signal_events`: adds rolling window, annual/non-overlap robustness, research flag, and `PARAMETER_ISLAND` marker columns.
- Existing `run_audit`: adds publication status and sheet-write statistics.
- Existing tabs and columns are retained; new columns are appended.
