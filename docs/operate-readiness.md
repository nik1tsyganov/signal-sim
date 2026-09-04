# Operate readiness

What the paper loop can do today, how to run it, what is still blocked on the owner, and what must never be claimed.

This is not a live desk. Every `total_pnl` / `ending_equity` is **fixture-mark PnL**. That is not alpha, not a broker fill, and not a search target. There is still no live-money trading.

## Owner wake checklist

1. Review this PR. The agent will not merge it.
2. Run `python3 -m signal_sim smoke --fixtures` locally (`python -m` on Windows). Confirm `ok=True` and that every PnL is fixture-mark.
3. On the machine that already has intel and Alpaca **paper** keys, or a Cursor Cloud run launched from **`signal-sim-paper`** with Runtime Secrets, run the optional live checks below. Do not paste keys into chat or the repo.
4. Decide merge yourself. Do not ask the agent to merge.

## What the paper loop can do today

Without keys, the repo can:

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
- Inspect a local sqlite ledger (`ledger --ledger <path>` or `paper-ledger`). Default is read-only: no POST, no write. `--fixtures` labels mark kinds and prints fixture-mark MTM versus `fixtures/marks` (not alpha). `--write` is refused.

With owner keys on a local machine (never committed):

- **Daily research book:** `python3 -m signal_sim research --live` pulls Quiver (congress, insider, gov contracts, news) and World Monitor, expands the operating universe beyond the frozen fixture list (allowlisted US liquid names only, capped), and writes `docs/research/YYYY-MM-DD.json`. The file is the next rebalance target book: counts, ranked tickers, sized targets. No person names, headlines, or URLs. Safe every weekday morning.
- **Live intel:** `python3 -m signal_sim feeds --live` pulls Quiver and World Monitor, then prints event counts and a ticker histogram only. It does not dump person names, headlines, URLs, or raw payloads. Missing `QUIVER_API_KEY` or `WORLD_MONITOR_KEY` exits 2 with the missing env names. `QUIVER_USERNAME` is unused by this path.
- **Alpaca paper read smoke:** `python3 -m signal_sim paper-account` constructs the paper-host client when `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` are set. It GETs `/v2/account`, `/v2/positions`, and `/v2/clock` on the paper host. `ALPACA_PAPER_API_BASE_URL` is optional and defaults to the paper HTTPS origin. A non-paper Alpaca host or IBKR live port still raises `LiveEndpointError`. Add `--dry-run` to validate a sample order payload in memory. This path does not POST an order.
- **Proposed rebalance dry-run:** `python3 -m signal_sim rebalance --fixtures` reads that same paper account and positions, sizes the existing fixture cluster-drift target book (or `rank_candidates` with `--rank`), and prints intended tickets: symbol, side, qty, notional, rationale. Offline fixture-only qty prefers fixture `entry_px`. `--live` and `--submit-paper` prefer an observed paper IEX last trade or snapshot `latestTrade` when one exists so QQQ/SPY are not sized at the fixture $36/$40 marks. It never invents a quote. Names still unmarked stay `no_mark`. `--live` loads today's research book (or computes it) so new intel names can enter; tickets are a full target-versus-positions diff (buys, sells, leftover closes). Default is print-only: no ledger write and no broker POST. Paper last-trade marks are not execution marks.
- **Local apply of those tickets:** `python3 -m signal_sim rebalance --fixtures --apply-local --ledger <path>` records the same dry-run tickets on the local SQLite ledger through `submit_paper_order`. Only tickets with `mark_kind=fixture_mark` and `mark_source=fixture` fill. Paper IEX sizing marks are skipped (`paper_mark_not_execution`) and must not be claimed as broker fills. This still does not POST `/v2/orders`.
- **Alpaca paper submit (off by default):** `python3 -m signal_sim paper-submit --symbol SPY --qty 1` or `python3 -m signal_sim rebalance --fixtures --submit-paper` (default `--limit 1`, smallest notional first) POST to the paper host only when `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1`, the resolved base URL is the paper host, keys are present, and the CLI flag is explicit. Live hosts and any other base URL are refused. Sells and leftover closes POST as well as buys. This is paper only. It is not live-money trading. If earlier paper orders are still open, print the book and wait; do not spray another full-book submit.
- **Paper performance snapshot:** `python3 -m signal_sim paper-performance --write` writes a sanitized paper account/positions/open-order summary to `docs/research/YYYY-MM-DD-paper.json`. Not alpha.

Fills go through `submit_paper_order` only. A fill must be `kind=fixture_mark` and `source=fixture`. Research or vendor mark kinds refuse. Constructing a live Alpaca host or IBKR live ports raises and does not open a socket. A present or unreadable `KILL` file refuses the order. Every fill writes an R8 provenance line that cites `params_sha256`. The local ledger fill gate is unchanged: Alpaca paper reads do not become execution marks.

Declared operate constants live in `fixtures/params.json`. Mark books may not override the locked policy fields. `size_frac` stays book-level so a path step can allocate differently from the liquid book.

## How to run

From the repository root. On Windows the launcher is usually `python`; on Linux it is often `python3`. Fixture commands require `--fixtures`.

```bash
python3 -m signal_sim rails --fixtures
python3 -m signal_sim smoke --fixtures
python3 -m signal_sim replay --fixtures
python3 -m signal_sim drift --fixtures
python3 -m signal_sim walkforward --fixtures
python3 -m signal_sim shadow --fixtures
python3 -m signal_sim diagnose --fixtures
python3 -m signal_sim intensity --fixtures
python3 -m signal_sim ledger --ledger paper-rebalance.sqlite --fixtures
```

`ledger --ledger` is read-only and does not require paper keys. `--fixtures` is optional and only used to label mark kinds and print fixture-mark MTM.

Desk (loopback only; default port 8765):

```bash
python3 -m signal_sim serve
```

Then `GET /api/params`, `GET /api/rails`, `GET /api/smoke`, `GET /api/drift`, `GET /api/walkforward`, `GET /api/shadow`, or `POST /api/replay`. `GET /api/replay` returns 405 and does not place orders.

`rails --fixtures` and `GET /api/rails` are the fast local check: live host construct raises, a temp `KILL` refuses an order, a research/vendor mark refuses a fill. `smoke --fixtures` and `GET /api/smoke` run that rails step first and then the rest of the frozen-params pass. They do not place live calls and do not write the repo-root `KILL` file. The desk loads rails on page load; smoke stays click-only. CI runs both commands after unittest on ubuntu-latest with no secrets.

### Optional owner-machine live checks

These need keys in the process environment. They are skipped in CI and in unittest when the env names are absent. Do not put keys in the repo.

```bash
python3 -m signal_sim runtime-env
python3 -m signal_sim feeds --live
python3 -m signal_sim research --live
python3 -m signal_sim paper-account
python3 -m signal_sim paper-account --dry-run
python3 -m signal_sim rebalance --fixtures
python3 -m signal_sim rebalance --fixtures --live
python3 -m signal_sim rebalance --fixtures --apply-local --ledger paper-rebalance.sqlite
python3 -m signal_sim ledger --ledger paper-rebalance.sqlite --fixtures
python3 -m signal_sim paper-performance --write
python3 -m signal_sim paper-submit --symbol SPY --qty 1
python3 -m signal_sim rebalance --fixtures --live --submit-paper --limit 1
```

Weekday command order is in [daily ops](daily-ops.md). Print `rebalance --fixtures --live` before any submit. If open paper orders from earlier in the day are still working, skip `--submit-paper`.

`runtime-env` prints presence booleans only. It never prints secret values.

Unittest integration cases are marked with `skipUnless` the relevant env names are set. To run them on the owner machine:

```bash
python3 -m unittest tests.test_live_feeds tests.test_alpaca_paper tests.test_rebalance -v
```

`SIGNAL_SIM_ALPACA_PAPER_SUBMIT` defaults to `0`. That is the kill switch: omit the name or set `0` and remote paper POSTs are refused. Set `1` **and** pass `paper-submit` or `rebalance --fixtures --submit-paper` to POST on the paper host only. A non-paper `ALPACA_PAPER_API_BASE_URL` is refused. Default is still print-only / read-only. `--apply-local` writes fixture-mark fills to `--ledger` only and cannot be combined with `--submit-paper`. `ledger --ledger` is the morning-brief read of that file (counts, sides, qtys, mark kinds, optional fixture-mark MTM). It does not POST and does not write. `--live` needs intel keys and still does not POST. Local-ledger fills stay on `submit_paper_order`.

A 2026-09-04 Cloud Runtime Secrets pass (presence only; no secret values) is recorded in [paper smoke results](paper-smoke-results.md). That run kept the submit flag at `0`.

A 2026-09-04 local book smoke (print-only, then `--apply-local` onto `/tmp/signal-sim-paper.sqlite`) is recorded in [local book smoke](local-book-smoke.md). Morning-brief cite, **not alpha**: print-only `n_tickets=10` / `n_skipped=2`; apply `n_applied=7` / `n_apply_skipped=3` (`paper_mark_not_execution`); fixture-mark MTM `total_pnl=-265.07`. Local book state: `python3 -m signal_sim ledger --ledger /tmp/signal-sim-paper.sqlite --fixtures`. Submit stayed `0`. No `/v2/orders` POST. Do not apply-local a `--live` paper-mark ticket as a fill.

A 2026-09-04 one-share paper POST (`paper-submit --symbol SPY --qty 1` once, flag exactly `1`, host `paper-api.alpaca.markets`) is recorded in [paper submit smoke](paper-submit-smoke.md). Cite: order `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a`, status `pending_new` then `new`, `filled_qty=0`, clock closed. The full rebalance book was not submitted. Not a fill and not alpha.

## Cursor Cloud Runtime Secrets

Cloud agents do not read a repo `.env`. Paper keys belong in **Dashboard Runtime Secrets** on a saved environment.

Prefer launching the agent with the saved environment named **`signal-sim-paper`**.

Runtime Secrets (values never go in the repo, PR, or logs):

- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_API_SECRET`
- `QUIVER_API_KEY`
- `WORLD_MONITOR_KEY`

Plain env on that same environment (not secrets, but still not committed):

- `ALPACA_PAPER_API_BASE_URL=https://paper-api.alpaca.markets`
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0` (default; omit or `0` keeps paper POSTs off). Set `1` to allow an explicit `paper-submit` / `--submit-paper`. Set back to `0` to kill remote paper POSTs.

GitHub Actions CI stays secret-free and only runs fixture rails/smoke. A cloud run without Runtime Secrets is expected to skip live integration tests and exit 2 on `feeds --live` / `paper-account` / `rebalance --fixtures`. A run that is not on `signal-sim-paper` should not invent keys or write them into files.

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

These are not missing code paths to invent. They are owner actions or later product work:

| Blocker | Why it is blocked | What the repo does today |
|---|---|---|
| Alpaca paper keys on GitHub CI | Keys stay in Cursor Runtime Secrets or the owner machine. Never commit them. | Paper host without keys still raises `NotImplementedError` and does not open a socket. With keys, `paper-account` is a read-only GET smoke. Live Alpaca hosts and IBKR live ports raise. |
| Remote Alpaca paper submits | Safer default is local-ledger fills and print-only. | Flag defaults to `0`. `1` plus `paper-submit` or `rebalance --fixtures --submit-paper` POSTs on the paper host only. Live / non-paper URLs refuse. `--apply-local` stays local. Kill by setting the flag to `0`. |
| Paid Quiver commercial-use terms | Key presence is not a terms review. | `feeds --live` calls Quiver when `QUIVER_API_KEY` is set. Without the key it exits 2 / raises `NotImplementedError` and does not open HTTP. |
| Paid World Monitor key on CI | Same as Quiver: owner-machine only. | `feeds --live` calls WM when `WORLD_MONITOR_KEY` is set. Without the key it exits 2. Recorded JSON under `fixtures/recorded/worldmonitor/` still attaches as feature flags only. |
| Honest vendor bars | Yahoo / Stooq / yfinance are not execution marks. | Fills require `fixture_mark`. Research or vendor kinds refuse. Jump-diffusion stays documentation-only until honest intraday bars exist. |

There is no TrendRadar live client and no GPL/AGPL vendoring.

## What must never be claimed

- Fixture-mark PnL is not alpha.
- Fixture-mark PnL is not a live result, a broker fill, or evidence of execution quality.
- Fixture-mark PnL is not a parameter-search target. Do not retune `rank_candidates`, Hawkes, drift, or `fixtures/params.json` to move it.
- Walk-forward fold numbers are the same class of number. They are not a fitted score.
- Intel flags (gov-contract, World Monitor recorded JSON, TrendRadar fixture, filing lags) are feature-only on the drift book and diagnose unless a test already documents a rank count.
- `feeds --live` counts are a connectivity check, not a rank input and not a live trading signal.
- `paper-account` is a paper-host read smoke. It is not a live-money balance, not a fill, and not permission to trade live.
- `rebalance --fixtures` tickets are a proposed book versus paper positions. They are not a broker fill, not alpha, and not permission to POST. A paper last trade or snapshot used for qty is a sizing mark only, not a `fixture_mark` fill.
- `rebalance --fixtures --apply-local` grows a local simulated book from those same tickets. It is a fixture-mark ledger fill, not a broker submit and not a live-money trade. Paper IEX marks never become claimed fills.
- `paper-submit` / `rebalance --fixtures --submit-paper` is an Alpaca **paper** POST. It is not live money, not alpha, and not permission to point the client at a live host.
- `ledger --ledger` is a read of that local simulated book. Fixture-mark MTM is plumbing, not alpha.
- The desk is paper-only and loopback-only. It is not a production broker UI.

See [the changelog](../CHANGELOG.md), [paper trading and quant research](paper-trading-and-quant.md), and [alternative data and safety](alt-data-and-safety.md).
