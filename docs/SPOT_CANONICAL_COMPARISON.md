# Spot-flow Canonical Comparison

Authority: `hh4832/-institutional-spot-flow-study`, branch `phase25-prior-return-c20`, commit `353fa7037505c8022db5a5b01601d0cdec370e6f`, run `20260913_174226_353fa703`, reconciled against the archived Spotflow Master Summary.

`Production Eligible` means `RETAINED + activation_ready + absolute evidence`; a row still needs to be `MATCHED` with valid current data before it becomes a vote.

| Signal | Horizon | Master Status | Registry Status | Formula Match | Threshold Match | Stats Match | Production Eligible | Result |
|---|---:|---|---|---|---|---|---|---|
| OTC total institutional Sell5 W504 PR(5,20] | C3 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| OTC total institutional Sell5 W504 PR(5,20] | C5 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| OTC total institutional Sell5 W504 PR(5,20] | C10 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| OTC total institutional Sell5 W504 PR(5,20] | C20 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| OTC dealer Sell10 W504 Z[-2.5,-1.5) | C3 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| OTC dealer Sell10 W504 Z[-2.5,-1.5) | C5 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| OTC dealer Sell10 W504 Z[-2.5,-1.5) | C10 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| OTC dealer Sell10 W504 Z[-2.5,-1.5) | C20 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| Combined foreign Sell10 W756 Z>=2.5 | C5 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| Combined foreign Sell10 W756 Z>=2.5 | C10 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| Combined foreign Sell10 W756 Z>=2.5 | C20 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| Listed dealer Net10 W756 PR(95,100] | C5 | REJECTED | REJECTED | YES | YES | YES | NO | Present; no vote |
| Listed dealer Net10 W756 PR(95,100] | C10 | RETAINED | RETAINED | YES | YES | YES | YES | Exact match |
| Listed dealer Net10 W756 PR(95,100] | C20 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| OTC total institutional Sell5 W756 PR(60,80] | C10 | RETEST | RETEST | YES | YES | YES | NO | Present; no vote |
| OTC foreign Buy5 W756 Z[0.5,1.5), relative | C5 | REJECTED | REJECTED | YES | YES | YES | NO | Present; relative; no vote |
| OTC foreign Buy5 W756 Z[0.5,1.5), relative | C10 | RETEST | RETEST | YES | YES | YES | NO | Present; relative; no vote |
| OTC foreign Buy5 W756 Z[0.5,1.5), relative | C20 | RETEST | RETEST | YES | YES | YES | NO | Present; relative; no vote |
| Listed foreign Net5 W756 PR(95,100] | C5 | REJECTED | REJECTED | YES | YES | YES | NO | Present; Level C; no vote |
| Listed foreign Net5 W756 PR(95,100] | C10 | REJECTED | REJECTED | YES | YES | YES | NO | Present; Level C; no vote |
| Listed foreign Net5 W756 PR(95,100] | C20 | REJECTED | REJECTED | YES | YES | YES | NO | Present; Level C; no vote |

## Formula audit

- `total_institutional = foreign + investment_trust + dealer`
- `dealer = dealer_self + dealer_hedge`
- `net` comes from the official FinLab `買賣超` table and is reconstructed by component; it is not recomputed as `buy - sell`.
- Predictors use `rolling sum(flow amount) / rolling sum(scope turnover)`.
- Listed, OTC and combined denominators are TAIEX, OTC and TAIEX+OTC turnover respectively.
- Spot PR and Z normalization are current-inclusive; Z uses `ddof=0`.
- PR intervals are right-closed and lower-open for the registered non-lowest bins; Z intervals are left-closed and right-open.
