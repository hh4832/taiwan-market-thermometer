# Old → New Signal Migration

| Old production signal | New status | Canonical replacement | Change reason |
|---|---|---|---|
| down_ratio high-PR rebound | RETEST | `breadth_down_ratio_high_legacy` | Regime dependence; no formal vote |
| delta_down_ratio_1d | REMOVED | none | Not retained in latest summary |
| limit_down_ratio / limit_up_ratio | REMOVED | none | Not retained in latest summary |
| up_ratio PR252 | REPLACED | `breadth_up_ratio_1d_pr60_ge95_c1` | Representative window and horizon changed |
| foreign OI single-day / W252 | REPLACED | `futures_foreign_change_*` | 3D accumulation, W120 strict-prior |
| foreign cumulative OI level | REMOVED | none | Not retained |
| no divergence signal | RETAINED new | `futures_divergence_*_c1` | Latest canonical evidence |
| Phase 2 spot triggers | REPLACED | canonical Phase 2.5 registry | O1→Cn, C20 and status/FDR migration |
| no margin/short | RETAINED research | conditional interaction registry | Displayed but cannot activate until cutoff validated |

No legacy evidence was silently deleted from history. Compatibility modules may still expose old structures to old callers, but Streamlit, cloud email, Sheet events and Forecast Calendar consume the canonical registry.
