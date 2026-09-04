# Research-live score' and conviction weights

**Status:** declared stub. **Not fitted. Not alpha. Paper only.**

This note records the 2026-09-04 Math Eng formulas that replaced equal-weight
`target_frac≈0.1` on the daily research book. Replay `rank_candidates` and the
locked operate digest are unchanged. Walk-forward has not blessed this as a
return model.

## Score'

Congress and insider are **not** lumped.

```
news_term = log1p(news_breakout)
q_term    = log1p(quiver_count) / log1p(20)   # 0 if quiver_count missing
wm_term   = intel_brief + wm_intel + chokepoint   # 0/1 flags
rec_term  = exp(-lag_h / half_life_hours)         # 0 if lag unknown

score' = 0.75*news_term
       + 3.0*congress_confirm
       + 3.0*insider_confirm
       + 2.0*gov_confirm
       + 3.0*q_term
       + 2.0*wm_term
       + 2.0*rec_term
```

`news_breakout` is the same observed-time news / intel_brief count as fixture
rank. `congress_confirm`, `insider_confirm`, and `gov_confirm` are filed/observed
binaries (un-lumped). `quiver_count` is the number of `source=quiver` events at
or before the cut. `lag_h` is the smallest known `insider_lag_hours` /
`congress_lag_hours` (filing delay). Intensity is stamped on the artifact and
does **not** enter score' or size.

Weights live in `fixtures/params.json` → `conviction`. They are declared
constants. Do not retune them to move fixture-mark PnL.

## Conviction sizing

```
K = top 10 by score' among names with score' >= min_score (1.0)
target_frac_i = min(max_name_frac, max_gross_frac * score'_i / sum_{j in K} score'_j)
```

Paper research `max_name_frac` is **0.20**. The locked replay rail in the same
manifest stays **1.0** so mark books and `params_sha256` do not churn. The
research book uses `min(locked, 0.20)`.

## Sell rules (print-only default)

Rebalance `--live` diffs the conviction book against paper positions:

1. Name not in K → leftover close.
2. Held name with score' below `min_score` → close.
3. Held frac exceeds target by more than `trim_band` (0.02) → partial trim to
   target. Moves inside the band are not ticketed.

`--submit-paper` stays flag-gated. Do not spray another full-book submit while
today's conviction names are already held.

## 2026-09-04 equal-weight vs score'

The A/B below was computed against the **pre-submit** equal-weight artifact
(XLE/MSFT/NFLX/NVDA/AAPL/CMCSA/CVX/DIS/SPY/XOM at 0.10). The checked-in
[2026-09-04.json](research/2026-09-04.json) is now the live conviction book
written by `research --live` before the authorized paper submit.

| | Equal-weight stub (held / proposed) | score' conviction |
|---|---|---|
| Book | XLE, MSFT, NFLX, NVDA, AAPL, CMCSA, CVX, DIS, SPY, XOM at 0.10 each | NVDA, XLE, MSFT, AAPL, GOOGL, ABT, HD, AMAT, UNH, AMZN |
| Enter | — | GOOGL, HD, ABT, AMAT, UNH, AMZN |
| Exit | — | NFLX, CMCSA, CVX, DIS, SPY, XOM |
| NVDA | 0.10 | ~0.18 (highest) |
| XLE | 0.10 and rank #1 on raw news count (15) | lower score'/frac than NVDA; no longer news-count dominance |

Intel-only names (HD, ABT, AMAT, …) enter on congress count + quiver term
instead of being skipped `gross_frac_cap` behind mega-cap news.

Print-only `research --live` on 2026-09-04 (PR 15; temp out; did **not**
overwrite the equal-weight artifact; **no** `--submit-paper`) produced the
same enter/exit set. Live filing lags lifted NVDA score' to ~13.3 so
`target_frac` hit the 0.20 name cap.

The authorized paper submit ([paper conviction submit](paper-conviction-submit.md))
then overwrote the artifact and POSTed the live-sized diff: leftover **closes**
on NFLX/CMCSA/CVX/DIS/SPY/XOM, an **add** on XLE (held 10% → target 12.3%;
PR 15's A/B had expected a trim), **opens** on GOOGL/HD/ABT/AMAT/UNH/AMZN,
and an NVDA add to the name cap. All 14 filled. Reprint `n_tickets=0`.

## Deviations from Math Eng (none on the formula)

- Locked replay `max_name_frac` stays 1.0. Paper guard is `conviction.max_name_frac=0.20`.
- Legacy `rank_candidates` is unchanged so fixture-mark PnL does not move.
- Intensity is not a size input. Math Eng's formula has no intensity term.
- `lag_h` is filing lag, not event age. Unknown → `rec_term=0`, as specified.

This is still **not fitted / not alpha** until walkforward says otherwise.
