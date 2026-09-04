# Changelog

What landed in the paper operate loop. This is not a live trading log. Every PnL number the loop prints is **fixture-mark PnL**, not alpha.

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
