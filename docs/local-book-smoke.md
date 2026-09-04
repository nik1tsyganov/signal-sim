# Local book smoke (2026-09-04)

End-to-end local simulated book after PR 6 (`rebalance --fixtures --apply-local --ledger`). Paper only. No live money. No Alpaca `/v2/orders` POST. Secret values were never printed or written into this file.

**Morning-brief cite (not alpha):** print-only `n_tickets=10` / `n_skipped=2`; apply `n_applied=7` / `n_apply_skipped=3` (`paper_mark_not_execution`); ledger `7` fixture-mark fills; fixture-mark MTM `total_pnl=-265.07`. Local book state: `python3 -m signal_sim ledger --ledger /tmp/signal-sim-paper.sqlite --fixtures`.

## Environment

- Checkout: `main` @ `6cf4d9f` (merge of PR 6), then this docs branch.
- Install: `pip install -e .` (stdlib package; `python3 -m signal_sim`).
- Python: 3.12.3.
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0` (`paper_submit_enabled` is false). Submit stayed off.

`runtime-env` presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (`0`) |

Resolved paper origin: `https://paper-api.alpaca.markets` (HTTPS). Paper account snapshot used for the dry-run: `ACTIVE`, USD, not trading-blocked, not account-blocked, `positions.n=0`, clock `is_open=false`. Cash / equity / buying_power figures are omitted on purpose. Sizing `allocation` on this run matched fixture `starting_cash` (`100000`).

Reproduce:

```bash
export SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0
python3 -m pip install -e .
python3 -m signal_sim runtime-env
python3 -m signal_sim rebalance --fixtures
python3 -m signal_sim rebalance --fixtures --apply-local --ledger /tmp/signal-sim-paper.sqlite
python3 -m signal_sim ledger --ledger /tmp/signal-sim-paper.sqlite --fixtures
```

`--apply-local` requires `--ledger`. Print-only does not write that path. `ledger --ledger` is read-only inspect (`paper-ledger` is the same command). It does not POST and does not write the sqlite file. `--write` is refused. Pass `--fixtures` to label mark kinds and print fixture-mark MTM versus `fixtures/marks` (not alpha).

## 1. Print-only (`rebalance --fixtures`)

Exit 0. `mode=paper-rebalance-dry-run`. `ok=True`. `submitted=false`. `local_applied=false`. `order_post=disabled`. `submit_flag=0`. Signal: `cluster-drift-stub` at `decision_at=2026-09-02T10:15:00Z`. `params_sha256=f74f835690544b976e0ba67243caecdea26719001cbb33bc9ccdac5fc1a38ded`. Apply gate printed but unused: `mark_kind=fixture_mark and mark_source=fixture`.

| Count | Value |
|---|---:|
| `n_tickets` | 10 |
| `n_skipped` | 2 |
| fixture-mark tickets | 7 |
| paper last-trade tickets | 3 |

Mark kinds on tickets (no secrets):

| `mark_kind` | `mark_source` | Tickets |
|---|---|---|
| `fixture_mark` | `fixture` | DIS, NFLX, NVDA, QQQ, SPY, XLE, XOM |
| `last_trade` | `alpaca_paper_data` | CMCSA, CVX, XLK |

Resolved sizing marks (includes names that did not get a ticket):

| Bucket | Names |
|---|---|
| `marks.fixture` | DIS, MSFT, NFLX, NVDA, QQQ, SPY, XLE, XOM |
| `marks.paper_data` | AAPL, CMCSA, CVX, XLK |
| `marks.unmarked` | (none) |

Plan skips (`n_skipped=2`): MSFT `gross_frac_cap`, AAPL `gross_frac_cap`. Both had a sizing mark; the sizer did not emit a ticket. All 10 tickets were `action=open` / `side=buy` versus an empty paper book. Qty used fixture `entry_px` for the seven fixture names and a paper IEX last trade for CMCSA / CVX / XLK. Paper last-trade marks are sizing only.

## 2. Apply (`rebalance --fixtures --apply-local --ledger /tmp/signal-sim-paper.sqlite`)

Exit 0. `mode=paper-rebalance-apply-local`. `ok=True`. `submitted=false`. `local_applied=true`. `order_post=disabled`. `submit_flag=0`. Same `n_tickets=10` / `n_skipped=2` plan as print-only. Fills went through `submit_paper_order` only.

| Count | Value |
|---|---:|
| `n_applied` | 7 |
| `n_apply_skipped` | 3 |
| ledger `orders` | 7 |
| ledger `fills` | 7 |
| ledger `account` / `positions` tables | not written |

Applied (fixture marks only): DIS, NFLX, NVDA, QQQ, SPY, XLE, XOM. Each `local_filled=true`, `submitted=false`, `status=filled`, `filled_at=2026-09-02T11:15:00Z` (`decision_at` + `decision_delay_hours`). Fill prices match `fixtures/marks/liquid.json` `entry_px`.

Execution skips (`paper_mark_not_execution`): XLK, CMCSA, CVX. Those tickets stayed `local_filled=false` / `submitted=false` and have **no** order or fill row. Paper IEX last-trade marks must not be claimed as broker fills.

Audit: `/tmp/signal-sim-paper.sqlite.audit.jsonl` has 7 `approved` / `filled` lines. Each cites `params_sha256`. No secret names or values.

Re-running apply on the same ledger is idempotent: `n_applied=0`, `n_apply_skipped=10` (7 `duplicate idempotency_key`, 3 `paper_mark_not_execution`), still 7 fills, still no POST.

## 3. Ledger inspect / fixture-mark PnL

Read-only inspect of the apply-local sqlite (do **not** point `replay --fixtures --ledger` at this file; replay would size a different book onto it):

```bash
python3 -m signal_sim ledger --ledger /tmp/signal-sim-paper.sqlite --fixtures
```

That command prints order/fill counts, symbols, sides, qtys, mark kinds, and fixture-mark MTM versus `fixtures/marks/liquid.json`. It is fixture-mark plumbing, not alpha. It does not POST to Alpaca and does not write the ledger. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is unused for this path.

Equivalent sqlite (what inspect reads):

```sql
SELECT o.ticker, o.side, o.size_frac, o.status, f.price, f.cost, f.filled_at
FROM orders o JOIN fills f ON f.order_id = o.order_id
ORDER BY o.ticker;
```

Shares reconstruct as `allocation * size_frac / fill_px` (here `allocation=100000`). Mark-to-market uses `fixtures/marks/liquid.json` `exit_px` (`2026-09-03T21:00:00Z`). `cost_bps=0`.

**This entire table is fixture-mark PnL. It is not alpha, not a broker fill, not a live result, and not a search target.**

| Ticker | Side | `size_frac` | Fill (`entry_px`) | Exit (`exit_px`) | Fixture-mark PnL |
|---|---|---:|---:|---:|---:|
| DIS | buy | 0.099856 | 55.5 | 56.0 | +89.96 |
| NFLX | buy | 0.099808 | 28.0 | 28.4 | +142.58 |
| NVDA | buy | 0.099760 | 178.5 | 180.0 | +83.83 |
| QQQ | buy | 0.099952 | 36.0 | 35.5 | -138.82 |
| SPY | buy | 0.099904 | 40.0 | 39.5 | -124.88 |
| XLE | buy | 0.100000 | 90.0 | 88.5 | -166.67 |
| XOM | buy | 0.099712 | 33.0 | 32.5 | -151.08 |
| **book** | | **0.698990** | | | **-265.07** |

3 winners / 4 losers. Unrealized equals total (no intra-horizon sells). Ending equity minus allocation is the same `-265.07`. Do not retune drift, Hawkes, rank, or `fixtures/params.json` to move this number.

## 4. Optional live print-only (`rebalance --fixtures --live`)

Secrets were present, so this ran print-only. **Not applied.** Do not `--apply-local` a live-intensity ticket set if the point is a fixture-mark book: live overlay can add paper-mark names (AAPL / CMCSA / CVX / XLK) that must stay `paper_mark_not_execution`.

Exit 0. `mode=paper-rebalance-dry-run`. `ok=True`. `submitted=false`. `local_applied=false`. `order_post=disabled`. `intensity_cut=now`.

| Count | Value |
|---|---:|
| `n_tickets` | 12 |
| `n_skipped` | 0 |
| fixture-mark tickets | 8 |
| paper last-trade tickets | 4 |

Live intel (counts and ticker histogram only; no headlines, person names, URLs, or raw payloads):

| Feed | Events | Tickers |
|---|---:|---|
| Quiver | 89 | AAPL 14, AMZN 7, CMCSA 3, CVX 3, DIS 3, GOOGL 12, META 7, MSFT 20, NFLX 2, NVDA 15, XOM 3 |
| World Monitor | 13 | XLE 13 |

Those counts are a connectivity check, not a rank input and not a trading signal. The extra tickets versus the fixture-only dry-run are the Hawkes overlay changing the drift book (MSFT and AAPL cleared `gross_frac_cap` on this live cut). That is still print-only.

## What failed

Nothing in this pass. No `/v2/orders` POST. Flag `0` was not violated.

## Caveats for the morning brief

- Cite the fixture-only apply counts (`n_applied=7`, `n_apply_skipped=3`), not the live print-only `n_tickets=12`.
- Every PnL figure above is **fixture-mark PnL**. Label it as such. It is not alpha.
- Paper last-trade / snapshot marks may size qty. They never fill. Skip reason is `paper_mark_not_execution`.
- Apply needs Alpaca **paper** read keys to snapshot the account. GitHub CI stays secret-free and does not run this path.
- The sqlite file is a local simulated book. It is not an Alpaca paper position and not live money.
- Keep `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`. Do not add a broker POST.

See [operate readiness](operate-readiness.md) and [paper smoke results](paper-smoke-results.md).
