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
sent_term = signed_mean(news prints)              # 0 unless news_breakout >= 1
                                                 # and a tone exists; may be negative

score' = 0.75*news_term
       + 3.0*congress_confirm
       + 3.0*insider_confirm
       + 2.0*gov_confirm
       + 3.0*q_term
       + 2.0*wm_term
       + 2.0*rec_term
       + 0.5*sent_term
```

`news_breakout` is the same observed-time news / intel_brief count as fixture
rank. `congress_confirm`, `insider_confirm`, and `gov_confirm` are filed/observed
binaries (un-lumped). `quiver_count` is the number of `source=quiver` events at
or before the cut. `lag_h` is the smallest known `insider_lag_hours` /
`congress_lag_hours` (filing delay). Intensity is stamped on the artifact and
does **not** enter score' or size.

`sent_term` is a cheap signed mark on **news/intel prints only** (cut at
`decision_at` / `observed_at <= cut`). Vendor polarity is used when Quiver
already carries `Sentiment` or a purchase/sale flag. Otherwise a tiny declared
lexicon scores the in-memory headline. Unknown tone is skipped — it is **not**
forced to +1. `sent_term` is applied only when `news_breakout >= 1` so it does
not double-count empty names or World Monitor 0/1 flags. No LLM firehose.
Numeric tones only land on the artifact. Fixture cluster-state still uses +1
so locked replay PnL does not move.

Weights live in `fixtures/params.json` → `conviction`. They are declared
constants. Do not retune them to move fixture-mark PnL.

## Conviction sizing (one lever)

```
K = top 10 by score' among names with score' >= min_score (1.0)
target_frac_i = min(max_name_frac, max_gross_invest * score'_i / sum_{j in K} score'_j)
```

`max_gross_invest` is **0.80** (declared dry powder). The locked replay rail
`max_gross_frac` stays **1.0** — same pattern as locked `max_name_frac=1.0` vs
paper `conviction.max_name_frac=0.20`. Research/rebalance do **not** post-hoc
shrink every name after this formula. If the name cap binds, book gross may
be below 0.80; that leftover is the cash reserve, not a second rescale.

Paper research `max_name_frac` is **0.20**. `params_sha256` does not include
these conviction keys.

## Sell rules (print-only default)

Rebalance `--live` diffs the conviction book against paper positions. Tickets
stamp `sell_reason`. When more than one exit fires, priority is:

**soft_stop ≥ horizon_exit ≥ score_decay ≥ trim**

1. **soft_stop** (declared 0.08): decision-time MTM from a fixture_mark or
   paper IEX sizing mark versus paper `avg_entry_price`. Close if
   `pnl_frac <= -soft_stop`. No post-decision prints or future bars.
2. **horizon_exit**: `now >= entry_decision_at + horizon_hours`. `now` is the
   research cut. `entry_decision_at` is the first research-live decision that
   booked the name (entry clock), not a future label.
3. **score_decay**: `score'_t < min_score` (`below_min_score`) **or**
   `score'_t / score'_entry < decay_floor` (0.50). Recomputed from events with
   `observed_at <= decision_at` only.
4. **trim** (`overweight_band`): held frac exceeds target by more than
   `trim_band` (0.02). Moves inside the band are not ticketed.

A name that drops out of K is `drop_from_book`. If a higher-priority exit also
fires, that reason wins.

Print-only planner tests emit a sample of each reason (`soft_stop`,
`horizon_exit`, `score_decay`, `below_min_score`, `drop_from_book`, trim).
This change does **not** `--submit-paper`.

`--submit-paper` stays flag-gated. Do not spray another full-book submit while
today's conviction names are already held.

## 2026-09-04 equal-weight vs score'

The A/B below was computed against the **pre-submit** equal-weight artifact
(XLE/MSFT/NFLX/NVDA/AAPL/CMCSA/CVX/DIS/SPY/XOM at 0.10). That snapshot is
frozen at [2026-09-04-equal-weight.json](research/2026-09-04-equal-weight.json)
and is the only "before" the score' A/B test reads. The dated ops file
[2026-09-04.json](research/2026-09-04.json) stays the live conviction book
written by `research --live` before the authorized paper submit.

| | Equal-weight stub (held / proposed) | score' conviction |
|---|---|---|
| Book | XLE, MSFT, NFLX, NVDA, AAPL, CMCSA, CVX, DIS, SPY, XOM at 0.10 each | NVDA, XLE, MSFT, AAPL, GOOGL, ABT, HD, AMAT, UNH, AMZN |
| Enter | — | GOOGL, HD, ABT, AMAT, UNH, AMZN |
| Exit | — | NFLX, CMCSA, CVX, DIS, SPY, XOM |
| NVDA | 0.10 | highest frac; with `max_gross_invest=0.80` about 0.14 (was ~0.18 at full gross) |
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

- Locked replay `max_name_frac` / `max_gross_frac` stay 1.0. Paper guards are
  `conviction.max_name_frac=0.20` and `conviction.max_gross_invest=0.80`.
- Legacy `rank_candidates` is unchanged so fixture-mark PnL does not move.
- Intensity is not a size input. Math Eng's formula has no intensity term.
- `lag_h` is filing lag, not event age. Unknown → `rec_term=0`, as specified.
- Sentiment is a small declared `w_sent=0.5`, not a fitted tone model. Missing
  tone does not default to +1.

This is still **not fitted / not alpha** until walkforward says otherwise.
