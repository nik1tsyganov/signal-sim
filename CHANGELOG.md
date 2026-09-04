# Changelog

What landed in the paper operate loop. This is not a live trading log. Every PnL number the loop prints is **fixture-mark PnL**, not alpha.

## Unreleased — conviction weights + richer Quiver rank

- **score':** research-live ranks with the declared Math Eng formula (un-lumped congress vs insider, `log1p` news, quiver-count term, World Monitor flags, filing-lag recency). Weights are in `fixtures/params.json` `conviction`. Not fitted. Not alpha. Fixture `rank_candidates` is unchanged.
- **Conviction book:** top 10 by score' share `max_gross_frac` in proportion to score', capped at paper `max_name_frac=0.20`. Research artifacts stamp `target_frac` per name. Equal-weight `0.1` is the old stub.
- **Tighter sells:** `--live` rebalance closes leftovers, closes score' below `min_score`, and trims when held exceeds target by more than `trim_band=0.02`. Print-only default. `--submit-paper` still flag-gated. Do not spray another full-book submit over today's 10 held names.
- **Note:** [research-conviction.md](docs/research-conviction.md). 2026-09-04 print-only A/B vs the equal-weight 10: enter GOOGL/HD/ABT/AMAT/UNH/AMZN; exit NFLX/CMCSA/CVX/DIS/SPY/XOM; NVDA highest frac (~0.18 artifact / 0.20 live with lags). No `--submit-paper` in this change. The checked-in `docs/research/2026-09-04.json` stays the equal-weight before snapshot.

## Unreleased — daily research + live sizing + buy/sell

- **Strategy growth:** `python3 -m signal_sim research --live` pulls Quiver congress/insider/gov/news plus World Monitor, expands the operating universe (fixture set ∪ top-N allowlisted intel tickers), ranks / diagnoses / intensity on that set, and writes `docs/research/YYYY-MM-DD.json`. That file is the next rebalance book, not a decorative dump. No PII.
- **Live-price sizing:** `--live` and `--submit-paper` size qty from an observed paper IEX last trade or snapshot `latestTrade` when one exists. Fixture `entry_px` (including the $36/$40 QQQ/SPY research marks) is the fallback only. Offline fixture-only mode still prefers fixture marks. Never invents a price.
- **Buy and sell:** rebalance diffs the target book against paper positions. Leftover names not in the book get close/sell tickets. Opens and increases stay buys. `--submit-paper` POSTs sells as well as buys.
- **Daily ops:** [daily-ops.md](docs/daily-ops.md) — morning `research --live`, after-open print `rebalance --fixtures --live`, optional `--submit-paper`, then `paper-performance --write` to `docs/performance/YYYY-MM-DD.json`. Kill switch remains `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`. Do not spray another full-book submit while earlier paper orders are still open.
- **Paper cancel:** `python3 -m signal_sim paper-cancel --order-id <uuid>` or `paper-cancel --open --limit <n>` DELETEs paper orders on the same rails as submit (flag=1, paper host, keys, explicit CLI). Default `--limit` is 1. No `--all`. Flag `0` never DELETEs.
- **Paper only.** No live-money trading.
- **2026-09-04 print smoke:** `research --live` wrote today's book (27-name operating universe; new intel names ranked). Print-only `rebalance --fixtures --live` sized from paper IEX last trades (SPY ~$773, not fixture $40). 11 earlier paper orders were still open — no `--submit-paper` and no live `paper-cancel` in that PR.
- **2026-09-04 paper cancel:** [paper order cancel](docs/paper-order-cancel.md) ran `paper-cancel --open --limit 11` on the paper host after PR 12. All 11 working day orders canceled (`filled_qty=0`), including fixture-priced QQQ ~278 / SPY ~250 and the smoke SPY x1. Buying power returned to `$400000`. Reprint: live-sized SPY ~13 @ ~$771. **No new `--submit-paper`.** Snapshot: [2026-09-04.json](docs/performance/2026-09-04.json).
- **2026-09-04 live-session submit:** [paper live submit](docs/paper-live-submit.md). Market open. Print-only book was live-sized (SPY ~13 @ ~$773). First `--submit-paper` only POSTed MSFT because canceled morning `client_order_id`s were treated as duplicates. Date-scoped keys (`rb:YYYYMMDD:…`) plus canceled-key retry unblocked the rest. Second `--submit-paper --limit 20` posted 10 live-sized tickets; all filled. Positions `n=10`. Snapshot: [2026-09-04.json](docs/performance/2026-09-04.json).
- **Client order id:** paper rebalance keys are `rb:{YYYYMMDD}:{SYM}:{side}:{action}` (research date when `--live`). Canceled / expired / rejected ids are not live duplicates; a `:rN` key is POSTed. Working or filled ids stay idempotent.

## Unreleased — paper strategy submit + performance snapshot

- **Strategy submit:** after a print-only capture, `rebalance --fixtures --live --submit-paper --limit <n>` POSTs the sized drift book on the paper host. `--limit` must be high enough to cover the print-only ticket count (no `--all` flag). Do not combine with `--apply-local`. Position-aware sizing GETs paper positions; the earlier SPY x1 smoke is only netted if it has already become a position.
- **Paper performance:** `python3 -m signal_sim paper-performance --write` (alias `paper-snapshot`) is the morning-brief cite. It GETs paper equity/cash/positions/open orders/fills and writes `docs/performance/YYYY-MM-DD.json`. Read-only. Paper-labeled. Not alpha. Not live money.
- **HTTP errors:** paper POST 403/4xx include the sanitized Alpaca `message` (no secrets).
- **Recorded submit:** [paper strategy submit](docs/paper-strategy-submit.md). Cite: 10 paper day orders queued (`pending_new`/`new`/`accepted`, `filled_qty=0`); XLK and MSFT `HTTP 403: insufficient buying power`; smoke SPY x1 still open. Snapshot: [2026-09-04.json](docs/performance/2026-09-04.json). Kill remote paper POSTs by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

## Unreleased — paper submit smoke

- **Recorded one-share paper POST:** [paper submit smoke](docs/paper-submit-smoke.md) runs `paper-account`, then `paper-submit --symbol SPY --qty 1` once, on `main` after PR 9. Cite: order `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a`, `client_order_id=ps:SPY:buy:q:1`, immediate status `pending_new`, read-back `new`, `filled_qty=0`. Clock was closed. Full rebalance book was not submitted. Paper host only.

## Unreleased — Alpaca paper submit (flag-gated)

- **Guarded paper POST:** remote `/v2/orders` POSTs run only when **all** of these hold: `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1`, resolved base URL is the paper host (`paper-api.alpaca.markets`), paper keys are present, and an explicit CLI flag (`rebalance --fixtures --submit-paper` or `paper-submit`). Print-only remains the default. `--apply-local` stays local-ledger only and cannot be combined with `--submit-paper`.
- **CLI:** `python3 -m signal_sim paper-submit --symbol SPY --qty 1` posts one tiny paper order. `rebalance --fixtures --submit-paper` posts sized dry-run tickets, smallest notional first, default `--limit 1` so the first enable cannot spray the book.
- **Rails:** live Alpaca hosts, a non-paper base URL, missing keys, flag omitted, or flag=`0` hard-refuse with no silent fallback. Duplicate `client_order_id` is treated as already submitted (GET before POST). Logs order id / status only; never secret values.
- **Kill switch:** set `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0` (or omit it). That is the off switch. There is still no live-money trading.

## Unreleased — ledger inspect

- **Read-only inspect:** `python3 -m signal_sim ledger --ledger <path>` (`paper-ledger` is the same command) prints order/fill counts, symbols, sides, qtys, and mark kinds from a local sqlite ledger. `--fixtures` adds fixture-mark MTM versus `fixtures/marks`. Labeled plumbing, not alpha.
- **Read-only:** no Alpaca POST and no ledger write. `--write` is refused. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is unused for this path and stays `0` for trading.
- **Morning brief:** [local book smoke](docs/local-book-smoke.md) cites `python3 -m signal_sim ledger --ledger /tmp/signal-sim-paper.sqlite --fixtures`.

## Unreleased — local book smoke

- **Recorded apply:** [local book smoke](docs/local-book-smoke.md) runs print-only `rebalance --fixtures`, then `--apply-local --ledger /tmp/signal-sim-paper.sqlite`, on `main` after PR 6. Cite: `n_tickets=10` / `n_skipped=2`; `n_applied=7` / `n_apply_skipped=3` (`paper_mark_not_execution`). Fixture-mark MTM only; not alpha.
- **Fills:** still local-ledger only. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` stayed `0`. No `/v2/orders` POST.

## Unreleased — rebalance apply-local

- **Local apply:** `python3 -m signal_sim rebalance --fixtures --apply-local --ledger <path>` computes the same tickets as the print-only dry-run, then records fills on the local paper ledger through `submit_paper_order`. Default remains print-only.
- **Fixture-mark gate:** only tickets with `mark_kind=fixture_mark` and `mark_source=fixture` fill. Paper IEX last-trade or snapshot marks may size qty but are skipped as `paper_mark_not_execution`. They are not claimed broker fills.
- **Fills:** still local-ledger only. No `/v2/orders` POST. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` stays `0` and still does not enable a remote submit.

## Unreleased — rebalance marks + live intensity

- **Sizing marks:** `rebalance --fixtures` still prefers fixture `entry_px`. Names without a fixture mark may use a paper IEX last trade or snapshot `latestTrade`. Missing or unreadable prices stay `no_mark`. Paper data marks are print-only and are not execution marks.
- **Live intensity:** `rebalance --fixtures --live` reuses `feeds --live` (Quiver + World Monitor) and feeds those events into the existing Hawkes overlay. Tickets stay print-only. `--live` still requires the drift book (omit `--rank`).
- **Fills:** still local-ledger only. No broker POST. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` stays `0`.

## Unreleased — proposed rebalance dry-run

- **Rebalance dry-run:** `python3 -m signal_sim rebalance --fixtures` reads the Alpaca paper account and positions, sizes the existing fixture cluster-drift target book (or rank with `--rank`), and prints intended tickets. No remote paper POST. No `submit_paper_order`. Qty prefers fixture `entry_px`.
- **Fills:** still local-ledger only. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` stays `0` and still does not enable a POST.

## Unreleased — live intel + Alpaca paper read

- **Live intel:** `python3 -m signal_sim feeds --live` pulls Quiver and World Monitor and prints counts plus a ticker histogram only. Missing `QUIVER_API_KEY` or `WORLD_MONITOR_KEY` exits 2. No raw PII dump.
- **Alpaca paper read:** `paper_broker_client` on the paper host returns a read-only client when `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` are set. `python3 -m signal_sim paper-account` GETs account, positions, and clock. Optional `ALPACA_PAPER_API_BASE_URL` must stay on the paper host. Live Alpaca hosts and IBKR live ports still raise.
- **Fills:** still local-ledger only through `submit_paper_order`. `fixture_mark` is unchanged. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1` does not enable a remote POST in this build.
- **Tests:** mocked HTTP unit tests always run. Integration cases skip unless Runtime Secrets / owner-machine keys are present. No secrets in GitHub CI.
- **Cloud:** prefer saved environment `signal-sim-paper`. Runtime Secrets: `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET`, `QUIVER_API_KEY`, `WORLD_MONITOR_KEY`. Env: `ALPACA_PAPER_API_BASE_URL` (paper HTTPS origin), `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` default `0`. `runtime-env` prints presence only. Never write secret values into the repo, PR, or logs. A 2026-09-04 paper-only live smoke is in [paper smoke results](docs/paper-smoke-results.md).

## Unreleased — PR 1

Paper-only local loop on a frozen universe and checked-in fixture marks.

- **Install:** `pip install -e .` from a repo checkout. Stdlib only. No Yahoo, Stooq, or broker SDKs.
- **Operate:** `rank`, `diagnose`, `intensity`, `drift`, `replay`, `walkforward`, `shadow`, `rails`, and `smoke` all require `--fixtures`. The desk serves the same loop on loopback only.
- **Fills:** `submit_paper_order` is the only order path. Fills must be `kind=fixture_mark` and `source=fixture`. A research or vendor mark refuses. A live Alpaca host or IBKR live ports raise and do not open a socket. A present `KILL` file refuses the order.
- **Rails:** `rails --fixtures` and `GET /api/rails` assert those refusals locally. `smoke --fixtures` runs rails first, then the rest of the frozen-params pass. CI runs both. No secrets.
- **Params:** `fixtures/params.json` is the single source. Locked policy: `cost_bps`, `decision_delay_hours`, `starting_cash`, `max_drawdown`, `max_gross_frac`, `max_name_frac`. `size_frac` stays book-level. Do not retune to move fixture-mark PnL.
- **Blocked on the owner:** Alpaca paper account and keys, paid Quiver / World Monitor key, honest vendor bars. Jump-diffusion stays documentation-only.

See [operate readiness](docs/operate-readiness.md) for how to run it and what must never be claimed.
