# Scoring reference

## Gates (exclusion, not penalty)

A contract is excluded when any of these fail. `rejected_reasons` counts them.

| Gate | Default | Config key |
|---|---|---|
| Open interest | ≥ 100 | `min_open_interest` |
| Bid/ask spread | ≤ 15% of mid | `max_spread_pct` |
| Bid | > 0 | — |
| Implied volatility | > 0 | — |
| Delta band | profile-dependent | — |
| Collateral | ≤ free cash | `budget` − committed |

## Delta bands

| Profile | Delta range | Min cushion |
|---|---|---|
| conservative | 0.10 – 0.22 | 12% |
| balanced | 0.10 – 0.35 | 5% |
| aggressive | 0.25 – 0.45 | 2% |

## Weights

Score is out of 100. Each sub-score is a normalised 0–1 value times its weight.

| Factor | conservative | balanced | aggressive |
|---|---|---|---|
| annualized yield | 22 | 30 | 38 |
| downside cushion | 34 | 25 | 18 |
| IV vs realised vol | 20 | 20 | 18 |
| liquidity (OI, spread) | 16 | 15 | 14 |
| trend quality | 8 | 10 | 12 |

Normalisation:
- yield: 0% → 0.0, 400%+ → 1.0
- cushion: 0% → 0.0, 25%+ → 1.0
- IV/HV: 0.8× → 0.0, 1.8×+ → 1.0
- liquidity: OI 100 → 0.2, 5000+ → 1.0, scaled down by spread width
- trend: above SMA50 0.7, below 0.35, minus 0.2 near the 52-week high

## Flags

| Flag | Meaning |
|---|---|
| `earnings DATE before expiry` | binary event inside the option's life |
| `ex-div in Nd …` | early-assignment risk on calls; strike-near-spot risk on puts |
| `thin cushion` | put breakeven within 2% of spot |
| `strike close to spot` | call strike within 3% of spot |
| `falling knife (N% from 52w high)` | deep drawdown; puts only |
| `near 52-week low` / `high` | range position extreme; puts only |
| `IV much richer than realised` | IV/HV above 1.8 — priced fear |

## Next-action rules

| Situation | Action |
|---|---|
| Assigned | Sell covered call at/above basis |
| Put, ≥80% captured, ≤7 DTE | Close early or let expire |
| Put ITM, ≤3 DTE | Expect assignment |
| Put ITM, >3 DTE | Roll out 30–60d lower, or accept assignment |
| Put OTM, ≤7 DTE | Let it expire |
| Call ITM + ex-div before expiry | **Early-assignment risk** — roll up/out |
| Call ITM, no dividend | Called away at strike, acceptable if above basis |
| Shares held, no call | Sell a call |
| 2+ puts on one symbol | Concentration warning |

## Cost basis

`basis = strike − net premium per share`. For a $17.50 put sold for $1.17, the
basis on assignment is $16.33. Covered calls are only offered at strikes ≥
basis, so being called away is always a profit versus the shares' cost.
