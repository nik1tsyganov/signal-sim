# Operate readiness

What the paper loop can do today, how to run it, what is still blocked on the owner, and what must never be claimed.

This is not a live desk. Every `total_pnl` / `ending_equity` is **fixture-mark PnL**. That is not alpha, not a broker fill, and not a search target.

## Owner wake checklist

1. Review [PR 1](https://github.com/nik1tsyganov/signal-sim/pull/1). The agent will not merge it.
2. Run `python3 -m signal_sim smoke --fixtures` locally (`python -m` on Windows). Confirm `ok=True` and that every PnL is fixture-mark.
3. Decide merge yourself. Do not ask the agent to merge.
4. Only after you merge, consider an Alpaca paper signup and keys. Not before. No keys in this repo.

## What the paper loop can do today

Without an owner-created broker account or a paid intel key, the repo can:

- Rank the frozen universe at the mark-book `decision_at` (`rank --fixtures`, `GET /api/rank`).
- Diagnose Hawkes intensity, clusters, intel flags, and filed confirms at that same cut (`diagnose --fixtures`, `GET /api/diagnose`).
- Emit declared Hawkes intensity at the same cut (`intensity --fixtures`, `GET /api/intensity`).
- Emit the cluster-drift target book (`drift --fixtures`, `GET /api/drift`).
- Replay the liquid sector mark book onto a local SQLite ledger (`replay --fixtures`, `POST /api/replay`).
- Walk two expanding fixture-mark folds plus no-news / shuffled-news / news-only comparisons (`walkforward --fixtures`, `GET /api/walkforward`).
- Freeze that harness as a shadow report (`shadow --fixtures`, `GET /api/shadow`).
- Assert local rails without live calls (`rails --fixtures`, `GET /api/rails`).
- Run one frozen-params pass of rails + rank + diagnose + intensity + drift + replay + walkforward + shadow (`smoke --fixtures`, `GET /api/smoke`).
- Serve the same loop on loopback only (`serve`). The desk smoke button is click-only; it does not auto-run on page load.

Fills go through `submit_paper_order` only. A fill must be `kind=fixture_mark` and `source=fixture`. Research or vendor mark kinds refuse. Constructing a live Alpaca host or IBKR live ports raises and does not open a socket. A present or unreadable `KILL` file refuses the order. Every fill writes an R8 provenance line that cites `params_sha256`.

Declared operate constants live in `fixtures/params.json`. Mark books may not override the locked policy fields. `size_frac` stays book-level so a path step can allocate differently from the liquid book.

## How to run

From the repository root. On Windows the launcher is usually `python`; on Linux it is often `python3`. All of these require `--fixtures`.

```bash
python3 -m signal_sim rails --fixtures
python3 -m signal_sim smoke --fixtures
python3 -m signal_sim replay --fixtures
python3 -m signal_sim drift --fixtures
python3 -m signal_sim walkforward --fixtures
python3 -m signal_sim shadow --fixtures
python3 -m signal_sim diagnose --fixtures
python3 -m signal_sim intensity --fixtures
```

Desk (loopback only; default port 8765):

```bash
python3 -m signal_sim serve
```

Then `GET /api/params`, `GET /api/rails`, `GET /api/smoke`, `GET /api/drift`, `GET /api/walkforward`, `GET /api/shadow`, or `POST /api/replay`. `GET /api/replay` returns 405 and does not place orders.

`rails --fixtures` and `GET /api/rails` are the fast local check: live host construct raises, a temp `KILL` refuses an order, a research/vendor mark refuses a fill. `smoke --fixtures` and `GET /api/smoke` run that rails step first and then the rest of the frozen-params pass. They do not place live calls and do not write the repo-root `KILL` file. The desk loads rails on page load; smoke stays click-only. CI runs both commands after unittest on ubuntu-latest with no secrets.

## Locked policy vs book fields

Locked in `fixtures/params.json`. A mark book that sets a different value fails to parse:

- `cost_bps`
- `decision_delay_hours`
- `starting_cash`
- `max_drawdown`
- `max_gross_frac`
- `max_name_frac`

Book-level on purpose:

- `size_frac` — allocation for that book or path step
- `decision_at` / `exit_at` / `marks` / optional `candidates`

Tests may mutate a parsed book in memory. That does not change the checked-in manifest.

Adding a key to `frozen_operate_params()` changes `params_sha256`. Do not retune any of these to move fixture-mark PnL.

## Blocked on the owner

These are not missing code paths to invent. They are owner actions:

| Blocker | Why it is blocked | What the repo does today |
|---|---|---|
| Alpaca paper account and keys | Alpaca requires signup and separate paper keys. We will not create that account here. | Live Alpaca host construct raises. The paper host is a stub (`NotImplementedError` / missing keys) and never opens a socket. |
| Paid Quiver key and commercial-use terms | No verified key + terms. | Live Quiver raises `NotImplementedError: no verified key + terms` and does not open HTTP. Congress / insider prints are fixtures. |
| Paid World Monitor key | `WORLD_MONITOR_KEY` is absent. | Live WM raises `ValueError: WORLD_MONITOR_KEY is missing` and does not open HTTP. Recorded JSON under `fixtures/recorded/worldmonitor/` attaches as feature flags only. |
| Honest vendor bars | Yahoo / Stooq / yfinance are not execution marks. | Fills require `fixture_mark`. Research or vendor kinds refuse. Jump-diffusion stays documentation-only until honest intraday bars exist. |

There is no TrendRadar live client and no GPL/AGPL vendoring.

## What must never be claimed

- Fixture-mark PnL is not alpha.
- Fixture-mark PnL is not a live result, a broker fill, or evidence of execution quality.
- Fixture-mark PnL is not a parameter-search target. Do not retune `rank_candidates`, Hawkes, drift, or `fixtures/params.json` to move it.
- Walk-forward fold numbers are the same class of number. They are not a fitted score.
- Intel flags (gov-contract, World Monitor recorded JSON, TrendRadar fixture, filing lags) are feature-only on the drift book and diagnose unless a test already documents a rank count. They are not a live intel feed.
- The desk is paper-only and loopback-only. It is not a production broker UI.

See [the changelog](../CHANGELOG.md), [paper trading and quant research](paper-trading-and-quant.md), and [alternative data and safety](alt-data-and-safety.md).
