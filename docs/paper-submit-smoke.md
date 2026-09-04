# Paper submit smoke (2026-09-04)

One-share Alpaca **paper** POST after PR 9 (`1b99f32` on `main`). Paper only. No live money. The full rebalance book was **not** submitted. Secret values were never printed or written into this file.

**Cite (not alpha):** `python3 -m signal_sim paper-submit --symbol SPY --qty 1` once. Broker order `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a`, `client_order_id=ps:SPY:buy:q:1`, market `buy` SPY qty `1`. Immediate POST status `pending_new`; read-back status `new`; `filled_qty=0`; `filled_at` empty. Clock `is_open=false`. Positions stayed `n=0`.

## Environment

- Checkout: `main` @ `1b99f32` (merge of PR 9), then this docs branch.
- Install: `pip install -e .` (stdlib package; `python3 -m signal_sim`).
- Python: 3.12.3.
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1` (`paper_submit_enabled` is true). The flag was already in the saved environment. It was not exported in the shell as a workaround.

`runtime-env` presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (exactly `1`) |

Resolved paper origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo). Hostname equals `paper-api.alpaca.markets`. Live Alpaca host construct still raises in unit tests. A non-paper base URL would have stopped this run.

Reproduce (paper host + flag `1` + keys required; do not point at a live host):

```bash
python3 -m pip install -e .
python3 -m signal_sim runtime-env
python3 -m signal_sim paper-account
python3 -m signal_sim paper-submit --symbol SPY --qty 1
```

`paper-submit` was run **exactly once**. Do not re-run it to "confirm." Kill remote paper POSTs by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

## 1. Read (`paper-account`)

Exit 0. stderr noted that flag `1` still requires `paper-submit` or `rebalance --submit-paper`; this command stayed read-only. `mode=alpaca-paper-read`. `ok=True`. `order_post=disabled`. `submit_flag=1`.

| Check | Result |
|---|---|
| Base URL | `https://paper-api.alpaca.markets` |
| Account | `ACTIVE`, USD, not trading-blocked, not account-blocked |
| Positions | `n=0` |
| Clock | `is_open=false` at `2026-09-04T06:17:45-04:00`; `next_open=2026-09-04T09:30:00-04:00`; `next_close=2026-09-04T16:00:00-04:00` |

Cash / equity / buying_power figures are omitted on purpose.

## 2. One-share paper POST (`paper-submit`)

```bash
python3 -m signal_sim paper-submit --symbol SPY --qty 1
```

Exit 0. `mode=alpaca-paper-submit`. `ok=True`. `submitted=true`. `order_post=paper`. `submit_flag=1`. `duplicate=false`. Clock still `is_open=false`.

| Field | Value |
|---|---|
| `id` | `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` |
| `client_order_id` | `ps:SPY:buy:q:1` |
| Immediate status | `pending_new` |
| `symbol` | `SPY` |
| `qty` | `1` |
| `side` | `buy` |
| `type` | `market` |
| `time_in_force` | `day` |
| `filled_qty` | `0` |
| `filled_at` | empty |
| `submitted_at` | `2026-09-04T10:17:54Z` |
| Positions after POST | `n=0` |

Pending / accepted while the session is closed is expected. This is not a fill and not alpha.

## 3. Read-back (no second POST)

GETs only: `/v2/account`, `/v2/positions`, `/v2/clock`, `/v2/orders`, and `order_by_client_id(ps:SPY:buy:q:1)`. Host remained `paper-api.alpaca.markets`. Then `paper-account` again (read-only).

| Check | Result |
|---|---|
| Order id | same `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` |
| Read-back status | `new` |
| `filled_qty` | `0` |
| `filled_at` | empty |
| Open orders | `n=1` (this SPY x1 only) |
| Positions | `n=0` |
| Account | still `ACTIVE`, USD, not trading-blocked, not account-blocked |
| Clock | still `is_open=false`; same next open / close |

No second `/v2/orders` POST. The rebalance book was not submitted.

## What failed

Nothing in this pass. The market was closed, so `pending_new` then `new` with zero fill is the accepted outcome, not a blocker.

## What this is not

- Not live money. The resolved host was the paper host.
- Not a fill. `filled_qty` stayed `0`.
- Not the full rebalance book. `--submit-paper` on `rebalance --fixtures` was not run.
- Not alpha. One accepted paper day order while the session is closed is plumbing.

See [operate readiness](operate-readiness.md) and [paper smoke results](paper-smoke-results.md).
