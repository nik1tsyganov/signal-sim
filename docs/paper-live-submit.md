# Paper live-session strategy submit (2026-09-04)

First live-sized Alpaca **paper** book submitted while the US cash session was open. Paper host only. No live money. Not alpha. Secret values were never printed or written into this file.

**Morning-brief cite:** `python3 -m signal_sim paper-performance --write` → [2026-09-04.json](performance/2026-09-04.json)

**Submit cite:** `rebalance --fixtures --live --submit-paper --limit 20` on `paper-api.alpaca.markets` after PR 13 canceled the oversized fixture-priced day orders. Clock `is_open=true` (09:55–10:02 ET).

## Environment

- Checkout: `main` @ `84a0549` for the first POST, then this branch for the date-scoped retry.
- Install: `pip install -e .`
- Python: 3.12.3
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1`
- Resolved origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo)

Presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (exactly `1`) |

## Gates (refused otherwise)

`runtime-env` + resolved host check: `submit_flag=1`, hostname `paper-api.alpaca.markets`.

`paper-account` at `2026-09-04T09:55:29-04:00`: `ACTIVE`, USD, not trading-blocked, cash / equity `$100000`, buying power `$400000`, positions `n=0`, clock **open**.

`paper-performance` (read-only): open orders `n=0`. The 11 canceled morning tickets were history only.

## Research + print-only (no POST)

`research --live` wrote [2026-09-04.json](research/2026-09-04.json). 27-name operating universe. Proposed book: XLE, MSFT, NFLX, NVDA, AAPL, CMCSA, CVX, DIS, SPY, XOM.

Print-only `rebalance --fixtures --live`: `prefer_paper_marks=true`. Every ticket `mark_kind=last_trade` / `mark_source=alpaca_paper_data`. `$10000` notional each. **SPY ~13 @ ~$773**, not fixture `$40` → ~250. QQQ not in the book.

| Symbol | Side | Qty | Mark px | Action |
|---|---|---:|---:|---|
| XLE | buy | 157.46 | 63.51 | open |
| MSFT | buy | 19.82 | 504.64 | open |
| NFLX | buy | 122.59 | 81.57 | open |
| NVDA | buy | 42.64 | 234.51 | open |
| AAPL | buy | 30.93 | 323.31 | open |
| CMCSA | buy | 376.51 | 26.56 | open |
| CVX | buy | 48.11 | 207.86 | open |
| DIS | buy | 94.36 | 105.98 | open |
| SPY | buy | 12.94 | 772.54 | open |
| XOM | buy | 62.68 | 159.54 | open |

## First POST (old client ids)

`rebalance --fixtures --live --submit-paper --limit 20` at `13:56:18Z`.

Nine tickets reused `rebalance:2026-09-02T10:15:00Z:<SYM>:buy:open` and matched this morning’s **canceled** orders (`duplicate=true`). Only MSFT had no prior client id (earlier 403).

| Symbol | Qty | Order id | Immediate | Outcome |
|---|---:|---|---|---|
| MSFT | 19.822784 | `db73cbdc-38f0-4cc1-8b06-d994e291a83c` | `pending_new` | **filled** @ $504.66 |
| XLE, NFLX, NVDA, AAPL, CMCSA, CVX, DIS, SPY, XOM | live-sized | canceled morning ids | `canceled` | `duplicate=true`; no new POST |

That is why this PR date-scopes paper `client_order_id` to `rb:{YYYYMMDD}:{SYM}:{side}:{action}` and retries canceled keys with `:rN`.

## Remaining book + second POST

After the MSFT fill: cash `$89996.23`, equity `$100002.97`, positions `{MSFT: 19.822784}`, open `n=0`. Reprint used session `20260904`. MSFT became a tiny `adjust`. Other names used new `rb:20260904:…` keys. All `last_trade`. SPY still ~13 @ ~$772.

`rebalance --fixtures --live --submit-paper --limit 20` at `14:01:47Z`. `n_paper_submitted=10` / `n_submit_skipped=0` / `duplicate=false`. Clock still open.

| Symbol | Side | Qty posted | Order id | Client order id | Status after fill |
|---|---|---:|---|---|---|
| XLE | buy | 156.780146 | `79055cd7-6dcd-4260-8185-9c5506f27805` | `rb:20260904:XLE:buy:open` | `filled` |
| MSFT | buy | 0.036938 | `e4c63221-46d2-4cac-a283-18f12b736242` | `rb:20260904:MSFT:buy:adjust` | `filled` |
| NFLX | buy | 123.147154 | `72559ede-7680-4fdb-98cd-e2bb81d58754` | `rb:20260904:NFLX:buy:open` | `filled` |
| NVDA | buy | 42.721677 | `492077e1-6c07-4a4b-9c78-be7a0beb8934` | `rb:20260904:NVDA:buy:open` | `filled` |
| AAPL | buy | 30.978544 | `3ef0be98-1367-49f8-a789-2c30788eea39` | `rb:20260904:AAPL:buy:open` | `filled` |
| CMCSA | buy | 377.39094 | `955cdca3-71d6-41d7-ae65-e373559cab77` | `rb:20260904:CMCSA:buy:open` | `filled` |
| CVX | buy | 48.082563 | `fee32e36-bbe9-4ac2-b3cc-3a9b8a2e8770` | `rb:20260904:CVX:buy:open` | `filled` |
| DIS | buy | 94.450243 | `a49cb456-b07d-4c81-b7a8-7946806ff60f` | `rb:20260904:DIS:buy:open` | `filled` |
| SPY | buy | 12.948755 | `6088a577-9a2f-4852-a55d-f5ea94216f2b` | `rb:20260904:SPY:buy:open` | `filled` |
| XOM | buy | 62.505227 | `da68103b-f583-4ff1-b075-0c9e8ffb457d` | `rb:20260904:XOM:buy:open` | `filled` |

Prior MSFT open: `db73cbdc-38f0-4cc1-8b06-d994e291a83c` filled 19.822784. Combined MSFT position `19.859722`.

## Snapshot after fills

`paper-performance --write` at `2026-09-04T14:02:14Z`. Clock still open. Open orders `n=0`. Positions `n=10`. Fills `n=45` (partial prints). Labeled `paper`, `alpha=false`, `live_money=false`.

| Check | Result |
|---|---|
| Equity | `99968.61` |
| Cash | `-21.31` (market fills vs $10k marks; already-held MSFT plus nine new $10k opens) |
| Buying power | `279886.53` |
| Positions | 10 longs, each ~$10k market value |
| SPY | 12.948755 @ avg `772.088455` |
| Open orders | `n=0` |

Compact copy at submit time listed those ten equal-weight names. The dated file [2026-09-04-paper.json](research/2026-09-04-paper.json) was later overwritten by the [conviction submit](paper-conviction-submit.md).

## What this is not

- Not live money. Host stayed `paper-api.alpaca.markets`.
- Not alpha. Live IEX last trades are sizing marks; fills are paper.
- Not permission to point the client at a live host.
- Kill remote paper POSTs with `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

See [daily ops](daily-ops.md) and [paper order cancel](paper-order-cancel.md).
