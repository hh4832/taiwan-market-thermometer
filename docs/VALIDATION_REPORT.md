# vNext Validation Report

## Scope completed

- Four-source research registry with 30 rows: 24 RETAINED, 5 RETEST, 1 REJECTED.
- RETAINED-only voting and `economic_signal_id` deduplication.
- Canonical long-form signal events and trading-date Forecast Calendar.
- O1→C1/C3/C5/C10/C20 outcome calculation from adjusted open/close.
- Streamlit five-tab vNext interface, canonical email, normalized Sheets, run manifest and archive.
- GitHub Actions final manifest validation and audit artifact.
- Colab Run All path and Google Drive archive.

## Commands and results

```text
python -m compileall dashboard tests-python                    PASS
python -m pytest tests-python -q                               38 passed
python -m unittest discover -s tests-python -v                 22 passed
Notebook JSON load + compile every code cell                  PASS (8 cells)
Streamlit module import smoke test                             PASS
npm test                                                       1 passed; build passed
npm run build                                                  PASS
npm run validate:artifact                                      PASS
git diff --check                                               PASS
```

Baseline before migration: 22 Python unittest tests and 1 Node test passed.

## Not fully validated

- No production FinLab credential was used in this environment. Exact live availability of `etl:adj_open`, dealer futures field, current spot tables and data freshness must be checked by a manual `workflow_dispatch` run.
- No production Google Sheet was mutated. Automatic creation of `signal_events`, `forecast_calendar`, and `run_audit` tabs needs live validation.
- No real Gmail was sent.
- GitHub Actions archive is an immutable workflow artifact. Colab writes the same research outputs into Google Drive; Actions does not yet upload directly into the canonical Drive research folder.
- Margin/short master summaries validate continuous Prior5D interactions but not a production hard cutoff. These rows are visible with `UNVALIDATED_ACTIVATION_RULE` and cannot vote.
- The checked-in TWSE closure calendar currently covers 2026. A future-year schedule must be added from the official TWSE market holiday table before that year begins.

## Bias / robustness migration audit

- Look-ahead: signals are d0 post-close; tradable benchmark starts O1. Rolling definitions preserve each master summary's inclusive/strict-prior convention.
- Survivorship/liquidity: inherited from source studies; production integration does not eliminate these risks.
- Data snooping/selection/multiple testing: registry preserves FDR fields and RETEST/REJECTED status; no new parameter search was performed.
- Costs/slippage: signal calendar is evidence monitoring, not a strategy backtest; no cost-free PnL claim is made.
- Sample/year/event concentration and overlapping outcomes remain visible risks.
- Untouched OOS validation remains incomplete.

## Overall decision

**修改後再測** — source migration and local validation are complete, but production credentials, live datasets, new Sheet tabs and one real scheduled run have not been validated.
