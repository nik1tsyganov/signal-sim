# Paper conviction-weight submit (2026-09-04)

Authorized Alpaca **paper** submit of the score' conviction book after PR 15. Paper host only. No live money. Not alpha. Secret values were never printed or written into this file.

**Morning-brief cite:** `python3 -m signal_sim paper-performance --write` → [2026-09-04.json](performance/2026-09-04.json)

**Submit cite:** `rebalance --fixtures --live --submit-paper --limit 30` on `paper-api.alpaca.markets`. Clock `is_open=true` (10:40–10:41 ET). `n_paper_submitted=14` / `n_submit_skipped=0`. All fourteen filled. Reprint after fills: `n_tickets=0`.

## Environment

- Checkout: `main` @ `13c1127` (PR 15 merged), then this docs branch.
- Install: `pip install -e .`
- Python: 3.12.3
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1`
- Resolved origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo). Hostname equals `paper-api.alpaca.markets`.

Presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (exactly `1`) |

Reproduce (paper host + flag `1` + keys; do not point at a live host):

```bash
python3 -m pip install -e .
python3 -m signal_sim runtime-env
python3 -m signal_sim research --live
python3 -m signal_sim rebalance --fixtures --live
python3 -m signal_sim rebalance --fixtures --live --submit-paper --limit 30
python3 -m signal_sim paper-performance --write
```

`--limit 30` covered the 14 print-only tickets. Do not combine `--submit-paper` with `--apply-local`. A second `--submit-paper` is idempotent on the same `rb:20260904:…` keys. Kill remote paper POSTs by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

## Gates

`runtime-env`: `submit_flag=1`, all five secret/env names present. `resolve_paper_base_url()` hostname `paper-api.alpaca.markets`.

`paper-account` at `2026-09-04T10:39:52-04:00`: `ACTIVE`, USD, not trading-blocked, equity `$99834.88`, cash `-$21.31`, buying power `$279512.08`, positions `n=10` (the equal-weight live-session book), clock **open**.

`paper-performance` (read-only, before POST): open orders `n=0`.

## Research (overwrote today's artifact)

`research --live` at `2026-09-04T14:40:00Z` wrote [2026-09-04.json](research/2026-09-04.json). This **replaced** the equal-weight stub (`target_frac=0.1` on XLE/MSFT/NFLX/NVDA/AAPL/CMCSA/CVX/DIS/SPY/XOM). The new book is conviction-weighted. Not an equal-weight stub.

| Ticker | score' | target_frac |
|---|---:|---:|
| NVDA | 13.339 | 0.2000 (name cap) |
| XLE | 8.079 | 0.1229 |
| MSFT | 6.776 | 0.1031 |
| AAPL | 6.188 | 0.0942 |
| GOOGL | 5.527 | 0.0841 |
| HD | 5.269 | 0.0802 |
| ABT | 5.165 | 0.0786 |
| AMAT | 5.165 | 0.0786 |
| UNH | 5.165 | 0.0786 |
| AMZN | 5.049 | 0.0768 |

Sum of fracs `0.997`. Unique fracs (not all `0.1`). Intel names enter on congress + quiver term. SPY/QQQ/XLK `below_min_score`; NFLX/CMCSA/CVX/DIS/XOM `outside_top_k`.

## Print-only (`rebalance --fixtures --live`)

`n_tickets=14` / `n_skipped=0`. `prefer_paper_marks=true`. Every ticket `mark_kind=last_trade` / `mark_source=alpaca_paper_data`. Allocation `$99819.08`. Clock open. **No fixture marks. No oversized fixture qtys** (SPY close is 12.95 @ ~$771, not fixture `$40` → ~250).

Held equal-weight 10: AAPL, CMCSA, CVX, DIS, MSFT, NFLX, NVDA, SPY, XLE, XOM.

| Symbol | Side | Qty | Mark px | Action |
|---|---|---:|---:|---|
| CMCSA | sell | 377.39094 | 26.405 | close leftover |
| CVX | sell | 48.082563 | 208.665 | close leftover |
| DIS | sell | 94.450243 | 105.35 | close leftover |
| NFLX | sell | 123.147154 | 80.60 | close leftover |
| SPY | sell | 12.948755 | 771.03 | close leftover |
| XOM | sell | 62.505227 | 160.755 | close leftover |
| NVDA | buy | 42.75 | 233.56 | adjust (10% → 20% cap) |
| XLE | buy | 34.59 | 64.12 | adjust (10% → 12.3%) |
| GOOGL | buy | 24.75 | 339.12 | open |
| HD | buy | 25.06 | 319.325 | open |
| ABT | buy | 71.92 | 109.08 | open |
| AMAT | buy | 17.24 | 454.93 | open |
| UNH | buy | 19.70 | 398.295 | open |
| AMZN | buy | 29.81 | 257.23 | open |

MSFT and AAPL stayed inside `trim_band=0.02` (held ~10% vs targets 10.3% / 9.4%). No tickets.

**XLE was an add, not a trim.** PR 15's print-only A/B expected a trim. Live score' after filing lags puts XLE at 12.3% versus the held 10%, which is outside the 2% band on the high side. That is still conviction-weighted and live-sized.

## Submit

`rebalance --fixtures --live --submit-paper --limit 30` at `2026-09-04T14:40:48Z`. Exit 0. `mode=paper-rebalance-submit-paper`. `ok=true`. `submitted=true`. `order_post=paper`. `submit_flag=1`. `--apply-local` was not used. Host remained `paper-api.alpaca.markets`. Clock still open. Immediate broker status on every POST was `pending_new` / `filled_qty=0` / `duplicate=false`. Session keys `rb:20260904:…`.

All fourteen later read back `filled`. Open list empty.

| Symbol | Side | Qty posted | Order id | Client order id | Status | `filled_at` |
|---|---|---:|---|---|---|---|
| CMCSA | sell | 377.39094 | `e492b70d-3da3-490e-a3b1-3fbca36c389a` | `rb:20260904:CMCSA:sell:close` | `filled` | `2026-09-04T14:40:53.13886Z` |
| CVX | sell | 48.082563 | `73c238db-afa6-40aa-9bb8-2f06f2a4e9f6` | `rb:20260904:CVX:sell:close` | `filled` | `2026-09-04T14:40:50.300907Z` |
| DIS | sell | 94.450243 | `30c321a4-3168-4db2-8844-dba2d0d307ca` | `rb:20260904:DIS:sell:close` | `filled` | `2026-09-04T14:40:51.670964Z` |
| NFLX | sell | 123.147154 | `b153b6ad-21e1-4a73-b334-1470c0e74414` | `rb:20260904:NFLX:sell:close` | `filled` | `2026-09-04T14:40:50.846587Z` |
| SPY | sell | 12.948755 | `e57c3e5c-22d9-491a-bbbe-3fb2ff0fcb43` | `rb:20260904:SPY:sell:close` | `filled` | `2026-09-04T14:40:52.182707Z` |
| XOM | sell | 62.505227 | `fbf2faf1-b648-484d-8b6a-f3f3bf081ffc` | `rb:20260904:XOM:sell:close` | `filled` | `2026-09-04T14:40:52.486653Z` |
| NVDA | buy | 42.725975 | `66375326-736a-4ee7-bc24-0d0dbe41e782` | `rb:20260904:NVDA:buy:adjust` | `filled` | `2026-09-04T14:40:53.173067Z` |
| XLE | buy | 34.542255 | `44fba3d6-855f-4aa6-980c-0c95603c8710` | `rb:20260904:XLE:buy:adjust` | `filled` | `2026-09-04T14:40:53.803069Z` |
| GOOGL | buy | 24.774469 | `a382dfab-4a6a-44f1-a589-dc8c27dc8b0c` | `rb:20260904:GOOGL:buy:open` | `filled` | `2026-09-04T14:40:53.680293Z` |
| HD | buy | 25.061179 | `2e2e2682-7b45-457b-84f7-b6251c2a8c50` | `rb:20260904:HD:buy:open` | `filled` | `2026-09-04T14:40:56.303254Z` |
| ABT | buy | 71.919463 | `c77cd37d-b019-4acb-b3cf-c5132de0edff` | `rb:20260904:ABT:buy:open` | `filled` | `2026-09-04T14:40:55.1104Z` |
| AMAT | buy | 17.235264 | `cc807e98-8d43-4b65-8e43-f7227d67f9ff` | `rb:20260904:AMAT:buy:open` | `filled` | `2026-09-04T14:40:54.141848Z` |
| UNH | buy | 19.720655 | `c623da3f-ad42-452f-a487-fc343a857b86` | `rb:20260904:UNH:buy:open` | `filled` | `2026-09-04T14:40:56.828779Z` |
| AMZN | buy | 29.830002 | `50ef2129-5ac3-473b-ba59-7c0d806f84d3` | `rb:20260904:AMZN:buy:open` | `filled` | `2026-09-04T14:40:55.514348Z` |

Six exits sold. Six intel names bought. NVDA added to the 0.20 cap. XLE added to 12.3%.

## Snapshot after fills

`paper-performance --write` at `2026-09-04T14:41:20Z`. Clock still open. Open orders `n=0`. Positions `n=10`. Fills `n=92` (partial prints across the day). Labeled `paper`, `alpha=false`, `live_money=false`. Compact copy: [2026-09-04-paper.json](research/2026-09-04-paper.json).

| Check | Result |
|---|---|
| Equity | `99864.04` |
| Cash | `79.38` |
| Buying power | `279714.56` |
| Positions | 10 longs matching the conviction names |
| Open orders | `n=0` |
| Reprint | `rebalance --fixtures --live` → `n_tickets=0` |

| Symbol | Qty | Market value | Held frac | Target frac |
|---|---:|---:|---:|---:|
| NVDA | 85.447652 | 19994.75 | 0.200 | 0.200 |
| XLE | 191.322401 | 12268.99 | 0.123 | 0.123 |
| MSFT | 19.859722 | 9975.54 | 0.100 | 0.103 |
| AAPL | 30.978544 | 9931.72 | 0.099 | 0.094 |
| GOOGL | 24.774469 | 8401.77 | 0.084 | 0.084 |
| HD | 25.061179 | 8001.53 | 0.080 | 0.080 |
| ABT | 71.919463 | 7849.29 | 0.079 | 0.079 |
| UNH | 19.720655 | 7846.06 | 0.079 | 0.079 |
| AMAT | 17.235264 | 7842.22 | 0.079 | 0.079 |
| AMZN | 29.830002 | 7671.08 | 0.077 | 0.077 |

Held set is exactly `{AAPL, ABT, AMAT, AMZN, GOOGL, HD, MSFT, NVDA, UNH, XLE}`. NFLX, CMCSA, CVX, DIS, SPY, and XOM are gone.

## What this is not

- Not live money. Host stayed `paper-api.alpaca.markets`.
- Not alpha. Live IEX last trades are sizing marks; fills are paper.
- Not permission to point the client at a live host.
- Not a second full-book spray. Open orders were empty before POST; reprint after fills is empty.
- Kill remote paper POSTs with `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

See [daily ops](daily-ops.md) and [research-conviction.md](research-conviction.md).
