# Paper strategy submit (2026-09-04)

Alpaca **paper** submit of the fixture drift rebalance book, plus a repeatable paper performance snapshot. Paper only. No live money. Not alpha. Secret values were never printed or written into this file.

**Morning-brief cite:** `python3 -m signal_sim paper-performance --write`

**Submit cite (not alpha):** `rebalance --fixtures --live --submit-paper --limit 20` once on the paper host. `n_paper_submitted=10` / `n_submit_skipped=2`. XLK and MSFT refused `HTTP 403: insufficient buying power` after pending QQQ/SPY day orders reserved paper BP (`buying_power=51.64`). Clock closed. Positions `n=0`. Fills `n=0`. Pending/`new`/`accepted` is the accepted closed-session outcome.

## Environment

- Checkout: this branch, after `pip install -e .`.
- Python: 3.12.3.
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1` (`paper_submit_enabled` is true). The flag was already in the saved environment.
- Resolved paper origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo). Hostname equals `paper-api.alpaca.markets`.

`runtime-env` presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (exactly `1`) |

Reproduce (paper host + flag `1` + keys required; do not point at a live host):

```bash
python3 -m pip install -e .
python3 -m signal_sim runtime-env
python3 -m signal_sim rebalance --fixtures
python3 -m signal_sim rebalance --fixtures --live
python3 -m signal_sim rebalance --fixtures --live --submit-paper --limit 20
python3 -m signal_sim paper-performance --write
```

There is no `--all` flag. `--limit 20` covers the print-only live book (12 tickets). Do not combine `--submit-paper` with `--apply-local`. A second `--submit-paper` is idempotent (`client_order_id` GET, `duplicate=true`). Kill remote paper POSTs by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

## Print-only books (no POST)

Positions were empty. The earlier SPY x1 smoke `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` (`client_order_id=ps:SPY:buy:q:1`) was still `new` / `filled_qty=0`. Rebalance GETs positions, so it did not net that pending share (it is not a long-1 position yet). Clock `is_open=false`.

### `rebalance --fixtures`

`n_tickets=10` / `n_skipped=2` (`MSFT`, `AAPL` `gross_frac_cap`). Allocation `100000`. Signal `cluster-drift-stub`.

| Symbol | Side | Qty | Mark kind | Action |
|---|---|---:|---|---|
| XLE | buy | 111.111111 | fixture_mark | open |
| QQQ | buy | 277.644101 | fixture_mark | open |
| SPY | buy | 249.759440 | fixture_mark | open |
| XLK | buy | 53.708820 | last_trade | open |
| DIS | buy | 179.920178 | fixture_mark | open |
| CMCSA | buy | 374.372259 | last_trade | open |
| NFLX | buy | 356.455873 | fixture_mark | open |
| NVDA | buy | 55.887739 | fixture_mark | open |
| CVX | buy | 47.196292 | last_trade | open |
| XOM | buy | 302.156380 | fixture_mark | open |

### `rebalance --fixtures --live`

`n_tickets=12` / `n_skipped=0`. Same signal. Live intensity changed size; MSFT and AAPL entered the book. This is the book that was submitted.

| Symbol | Side | Qty | Mark kind | Action |
|---|---|---:|---|---|
| XLE | buy | 1.058759 | fixture_mark | open |
| QQQ | buy | 277.644101 | fixture_mark | open |
| SPY | buy | 249.759440 | fixture_mark | open |
| XLK | buy | 53.708820 | last_trade | open |
| DIS | buy | 7.202156 | fixture_mark | open |
| CMCSA | buy | 14.986019 | last_trade | open |
| NFLX | buy | 20.983270 | fixture_mark | open |
| NVDA | buy | 0.462237 | fixture_mark | open |
| CVX | buy | 1.889255 | last_trade | open |
| XOM | buy | 12.095237 | fixture_mark | open |
| MSFT | buy | 1.466284 | fixture_mark | open |
| AAPL | buy | 0.268793 | last_trade | open |

Qty prefers fixture `entry_px`, then a paper IEX last trade. Fixture marks for QQQ/SPY size at the checked-in `$36`/`$40` marks, not the live tape. Those pending market orders still reserve real paper buying power. These are not execution marks and not alpha.

## Submit (`--fixtures --live --submit-paper --limit 20`)

Exit 1 because `ok=false` (two broker 403s). `mode=paper-rebalance-submit-paper`. `submitted=true`. `order_post=paper`. `submit_flag=1`. `--apply-local` was not used. Host remained `paper-api.alpaca.markets`. Clock still `is_open=false`.

Live intensity at submit time moved a few qtys by a fraction versus the print-only table. Same 12 names.

| Symbol | Side | Qty posted | Immediate status | Order id | Notes |
|---|---|---:|---|---|---|
| XLE | buy | 1.058664 | `pending_new` | `e8e16189-ff98-4885-b0b0-a05de1a1a037` | queued |
| QQQ | buy | 277.644101 | `pending_new` | `94e7349e-0391-48f2-9134-1a41f9694141` | queued |
| SPY | buy | 249.759440 | `pending_new` | `f614eadd-aa3c-44d2-9f04-19b4dc69c595` | queued; not the smoke x1 |
| XLK | buy | 53.708820 | — | — | `HTTP 403: insufficient buying power` |
| DIS | buy | 7.201601 | `pending_new` | `8c9b2713-3937-44b5-8a93-57d5a529ae96` | queued |
| CMCSA | buy | 14.984865 | `pending_new` | `53b2f6ad-a24f-42c5-9415-45e2cf4ee7ed` | queued |
| NFLX | buy | 20.981685 | `pending_new` | `ba0aabd2-10b5-4c26-b8fa-eb8f31004010` | queued |
| NVDA | buy | 0.462200 | `accepted` | `66d07fac-67be-4a36-a276-c5294379d324` | queued |
| CVX | buy | 1.889109 | `pending_new` | `795a127e-f339-4640-b15a-a60e04dff5a9` | queued |
| XOM | buy | 12.094306 | `pending_new` | `fd653012-beb8-4599-a3ed-dd81387dda3c` | queued |
| MSFT | buy | 1.466167 | — | — | `HTTP 403: insufficient buying power` |
| AAPL | buy | 0.268771 | `accepted` | `fd21fd91-8799-4639-ae9f-f0b427f2e4b1` | queued |

A second `--submit-paper --limit 20` treated the ten accepted client ids as `duplicate=true` and did not POST them again. XLK/MSFT still 403 insufficient buying power. `filled_qty` stayed `0` on every accepted order.

## Read-back (GET only)

| Check | Result |
|---|---|
| Positions | `n=0` (nothing filled; session closed) |
| Fills | `n=0` |
| Open orders | `n=11` (10 strategy + smoke SPY x1) |
| Account | `ACTIVE`, USD, not trading-blocked, not account-blocked |
| Cash / equity | `100000` / `100000` (unfilled) |
| Buying power | `51.64` (pending day orders reserved the rest) |
| Clock | `is_open=false`; `next_open=2026-09-04T09:30:00-04:00`; `next_close=2026-09-04T16:00:00-04:00` |

Every order id + read-back status:

| Order id | Symbol | Qty | Status | `filled_qty` | Client order id |
|---|---|---:|---|---:|---|
| `fd21fd91-8799-4639-ae9f-f0b427f2e4b1` | AAPL | 0.268771 | `accepted` | 0 | `rebalance:2026-09-02T10:15:00Z:AAPL:buy:open` |
| `fd653012-beb8-4599-a3ed-dd81387dda3c` | XOM | 12.094306 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:XOM:buy:open` |
| `795a127e-f339-4640-b15a-a60e04dff5a9` | CVX | 1.889109 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:CVX:buy:open` |
| `66d07fac-67be-4a36-a276-c5294379d324` | NVDA | 0.4622 | `accepted` | 0 | `rebalance:2026-09-02T10:15:00Z:NVDA:buy:open` |
| `ba0aabd2-10b5-4c26-b8fa-eb8f31004010` | NFLX | 20.981685 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:NFLX:buy:open` |
| `53b2f6ad-a24f-42c5-9415-45e2cf4ee7ed` | CMCSA | 14.984865 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:CMCSA:buy:open` |
| `8c9b2713-3937-44b5-8a93-57d5a529ae96` | DIS | 7.201601 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:DIS:buy:open` |
| `f614eadd-aa3c-44d2-9f04-19b4dc69c595` | SPY | 249.75944 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:SPY:buy:open` |
| `94e7349e-0391-48f2-9134-1a41f9694141` | QQQ | 277.644101 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:QQQ:buy:open` |
| `e8e16189-ff98-4885-b0b0-a05de1a1a037` | XLE | 1.058664 | `new` | 0 | `rebalance:2026-09-02T10:15:00Z:XLE:buy:open` |
| `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` | SPY | 1 | `new` | 0 | `ps:SPY:buy:q:1` (prior smoke) |

SPY is two open paper day orders: the strategy ticket (~250 shares) plus the earlier unfilled smoke x1. That is not a doubled long-1 position. The sizer saw `positions n=0`.

## Performance snapshot

```bash
python3 -m signal_sim paper-performance --write
```

Wrote [docs/performance/2026-09-04.json](performance/2026-09-04.json). Labeled `paper`, `alpha=false`, `live_money=false`. Read-only GETs of `/v2/account`, `/v2/positions`, `/v2/clock`, `/v2/orders`, and `/v2/account/activities/FILL`. Re-run the same command to refresh the dated file after the session opens.

This snapshot: cash `100000`, equity `100000`, buying power `51.64`, positions `n=0`, open orders `n=11`, fills `n=0`, clock closed. Not a live-money score.

## What failed

XLK and MSFT paper POSTs returned `HTTP 403: insufficient buying power`. The rest of the live book queued. Closed-session `pending_new` / `new` / `accepted` with zero fill is expected, not a blocker.

## What this is not

- Not live money. The resolved host was the paper host.
- Not a fill. Every `filled_qty` stayed `0`. Positions stayed empty.
- Not alpha. Fixture-mark and paper IEX sizing marks are plumbing.
- Not permission to point the client at a live host.

See [operate readiness](operate-readiness.md) and [paper submit smoke](paper-submit-smoke.md).

**Later the same day (market open):** those 11 working orders were canceled, then a live-sized book was submitted and filled. See [paper live submit](paper-live-submit.md). Do not re-run this closed-session `--submit-paper`.
