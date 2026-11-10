# Research Statistics Completeness Audit — 2026-11-10

This is a bounded evidence-registration change, **not** a new signal/alpha study.
No PR/Z formulas, thresholds, direction, evidence grade, FDR fields, signal
eligibility, voting, timing, or 0050 O1→Cn outcomes are altered.

## Coverage before the change

At frozen research-rule registration level:
- 34 explicitly declared breadth/futures/spot variants, plus 14 margin/short
  interaction variants and 19 options candidates loaded from their CSV.
- The 19 options candidates have all five descriptive performance fields.
- Missing performance fields in 8 manually declared variants:
  - **4 RETAINED breadth** variants: all five fields missing.
  - **2 RETAINED futures divergence** variants: Relative Mean missing.
  - **2 RETEST** variants: all five fields missing.

## Backfilled statistics and their precise semantics

**Breadth:** Research-run `20260915_150729_v11_breadth_mechanism_regime_62d7aba6a217`
(Research commit `62d7aba6a2173a52e4b53afbadd1e3327ac8169a`).
Source: [v11 market_breadth_summary.xlsx](https://docs.google.com/spreadsheets/d/1lUjoiTamtc-HayYKUi5Nuljf4DyU6qjC/edit).
Use the `regime_comparison` rows filtered to:
`regime=POST_2015_10PCT`, `overlap_policy=all_events`,
the exact `predictor`, `mean_window`, `transform=level`,
`pr_window`, `pr_bin`, and `target=ret_o1_cN`.

| Signal ID | W / bin / target | N | Mean | Median | P(0050 return > 0) | Mean minus unconditional |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `breadth_up_ratio_1d_pr60_ge95_c1` | 60 / 95-100 / C1 | 193 | -0.0918% | -0.1596% | 36.79% | -0.1056 pp |
| `breadth_big_up_ratio_5d_pr126_60_80_c3` | 126 / 60-80 / C3 | 526 | +0.4423% | +0.4206% | 61.41% | +0.2582 pp |
| `breadth_big_up_ratio_5d_pr126_60_80_c5` | 126 / 60-80 / C5 | 526 | +0.8314% | +0.6622% | 63.50% | +0.4729 pp |
| `breadth_big_up_ratio_5d_pr252_60_80_c3` | 252 / 60-80 / C3 | 497 | +0.4684% | +0.4380% | 59.56% | +0.2843 pp |

All `Relative Mean` entries above use the v11 `bin_minus_unconditional_mean`
(statistical benchmark = same-regime unconditional eligible-date average), **not**
`mean_difference` (bin versus the non-signal group). The benchmark is
labelled explicitly on each ResearchSignal via `evidence_test_scope`.

The bearish C1 study's 36.79% is the **positive-return** rate, not the
bearish directional hit rate, which cannot be reconstructed exactly without
separating zero-return and negative-return observations. Therefore
`win_rate=None` remains intentional for C1, instead of fabricating a
short-direction hit rate. The bullish C3/C5 rows may use positive-return rate
as the registered bullish-direction descriptive Win Rate.

The 2015-06-01 date is the start of Taiwan's 10% daily stock price-limit
regime. These **post-2015, in-sample, overlapping-event** statistics are NOT
full-sample estimates, untouched OOS performance or trading returns. Pre-2015
results can have different signs. The original v11 Global-FDR fields do not
support a broad significant-alpha claim, and this patch does not copy/recompute
FDR into the canonical registry. Existing RETAINED classifications remain
unchanged and mean an evidence monitoring signal, not a trading recommendation.

**Futures:** Source: [v4 foreign_dealer_divergence_surface.csv](https://drive.google.com/file/d/1TPBbnHHVwn3uTv7kDksKQ6V-kjOL1XIM/view).
Rows match `predictor=foreign_percentile_minus_dealer_percentile`,
`accumulation_window=3`, `rolling_window=120`,
`parameter_cell=3D_x_120D`, `outcome=o1_c1`:

- `futures_divergence_le_m60_c1`: N=593; `mean_diff_vs_nongroup=-0.0013568295375945138`.
- `futures_divergence_ge60_c1`: N=602; `mean_diff_vs_nongroup=+0.0009431479092912358`.

The source comparator is the **non-signal group**; it is not the breadth
unconditional baseline. Other previously registered statistics and evidence
flags are untouched.

## Intentionally unresolved

1. `breadth_up_ratio_1d_pr60_ge95_c1` — the bearish directional `win_rate`
   still requires sign-aware source-level reconstruction; positive-rate alone
   is not sufficient.
2. `breadth_down_ratio_high_legacy` — `RETEST`. The broad PR>=80
   definition combines multiple v10 bins; no uniquely verified source row for
   the exact registered condition. Do not infer from one bin.
3. `futures_foreign_change_pr80_100_c3` — `RETEST`. An exact-matched
   canonical O1→C3 statistics row was not verified from the research
   registration. Do not substitute adjacent horizons.

Tests enforce this missingness inventory, source-specific baselines, and the
absence of any changes to global/family FDR for the new breadth statistics.

## Display caveats

- Mean and Median are historical 0050 adjusted O1→Cn returns, **not forecasts**.
- Relative Mean baselines depend on the originating research family.
- Historical win rates are not probabilities of success for the next forecast;
  bullish positive-return rate must not be relabelled as bearish hit rate.
- Research statistics may be selected in-sample, overlapping, regime-specific,
  and exposed to survivorship/data-snooping, execution costs, and structural changes.
- RETEST/REJECTED never count as formal votes. Votes are **not probabilities**.
- Missing evidence must be displayed as `—`, never 0 or an inferred value.
