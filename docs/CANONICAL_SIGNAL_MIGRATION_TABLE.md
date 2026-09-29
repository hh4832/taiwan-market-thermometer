# Canonical Signal Migration Table

Machine-readable authority: `dashboard/research_registry.py`. `null` means the master summary did not validate the field; it is not zero.

| Direction | Horizon | Source | Signal / threshold | N | Mean | Median | Win | Relative | Global FDR | Family FDR | Status |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Bearish | C1 | Breadth | up_ratio 1D, PR60 ≥95 | 256 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | RETAINED |
| Bullish | C3 | Breadth | big_up_ratio 5D, PR60–80 | 722 | UNKNOWN | UNKNOWN | UNKNOWN | 0.199% | UNKNOWN | UNKNOWN | RETAINED |
| Bullish | C5 | Breadth | big_up_ratio 5D, PR60–80 | 722 | UNKNOWN | UNKNOWN | UNKNOWN | 0.362% | UNKNOWN | UNKNOWN | RETAINED |
| Bearish | C1 | Futures | Foreign Net OI Change Ratio 3D, W120 PR0–20 | 935 | -0.121% | -0.073% | 56.79% bearish | -0.134% | .000102022 | .00000514397 | RETAINED |
| Bearish | C3 | Futures | same | 935 | -0.176% | -0.074% | 52.19% bearish | -0.309% | .00301993 | .000444108 | RETAINED |
| Bearish | C5 | Futures | same | 935 | -0.112% | 0.000% | 50.05% bearish | -0.368% | .0628217 | .00435085 | RETAINED |
| Bearish | C10 | Futures | same; relative-only | 935 | 0.049% | 0.174% | 48.24% bearish | -0.504% | .0386016 | .00883042 | RETAINED |
| Bullish | C1 | Futures | Foreign Net OI Change Ratio 3D, W120 PR80–100 | 912 | 0.097% | 0.066% | 51.54% | 0.084% | .0378952 | .00843523 | RETAINED |
| Bearish | C1 | Futures | Foreign–Dealer PR divergence ≤-0.60 | 593 | -0.106% | -0.073% | 57.17% bearish | UNKNOWN | .00155866 | .00124693 | RETAINED |
| Bullish | C1 | Futures | Foreign–Dealer PR divergence ≥0.60 | 602 | 0.095% | 0.059% | 51.00% | UNKNOWN | .0186971 | .0169973 | RETAINED |
| Bullish | C5/C10/C20 | Spot | OTC total institutional Sell5, W504 PR5–20 | 203 | 1.31/2.43/4.08% | 1.07/2.13/4.01% | 71.43/79.31/83.74% | 1.00/1.69/2.45% | see registry | see registry | RETAINED |
| Bullish | C5/C10/C20 | Spot | OTC dealer Sell10, W504 Z[-2.5,-1.5) | 94 | 1.52/3.31/7.17% | 1.14/3.87/6.65% | 74.47/80.85/98.94% | 1.17/2.54/5.57% | see registry | see registry | RETAINED |
| Bullish | C10 | Spot | Listed dealer Net10, W756 PR95–100 | 109 | 3.09% | 2.79% | 83.49% | 2.30% | .0128201 | .00523784 | RETAINED |
| Conditional | C3–C20 | Margin/Short | flow × continuous Prior5D | see registry | see registry | see registry | see registry | see registry | see registry | see registry | RETAINED research; activation not ready |

Robustness variants share `economic_signal_id` and do not create extra votes. RETEST/REJECTED rows remain in the machine-readable registry for audit but never enter production voting.
