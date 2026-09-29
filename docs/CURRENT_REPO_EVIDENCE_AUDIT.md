# Current Repository Evidence Audit (pre-migration)

| Source | Old production evidence | Status | Reason |
|---|---|---|---|
| Breadth | `delta_down_ratio_1d`, limit-up/down, high down-ratio rebound | LEGACY | Not in latest retained set; down-ratio is RETEST/regime-dependent |
| Breadth | `up_ratio` PR252 extreme | REPLACED | Canonical representative is rolling PR60 ≥95, O1→C1 |
| Futures | single-day `foreign_oi_change_ratio`, rolling 252 | REPLACED | Canonical formula is 3-day change, W120 strict-prior |
| Futures | cumulative OI level PR252 | REMOVED | Not retained by latest master summary |
| Futures | Foreign–Dealer divergence | MISSING | Canonical retained O1→C1 signal was absent |
| Spot | Phase 2 hard-coded A-grade candidates | REPLACED | Canonical Phase 2.5 has O1→C3/C5/C10/C20 and status separation |
| Margin/Short | absent | MISSING | Conditional interactions must be registered; hard cutoff remains unvalidated |

Old historical statistics were preserved in Git history and the old-to-new table; they are no longer allowed to vote merely because they remain in legacy compatibility code.
