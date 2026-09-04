# Paper strategy submit (2026-09-04)

Alpaca **paper** submit of the fixture drift rebalance book, plus a repeatable paper performance snapshot. Paper only. No live money. Not alpha. Secret values were never printed or written into this file.

**Morning-brief cite:** `python3 -m signal_sim paper-performance --write`

## Environment

- Checkout: this branch, after `pip install -e .`.
- Python: 3.12.3.
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1`. Resolved host is `paper-api.alpaca.markets` (HTTPS, no userinfo). Live Alpaca host construct still raises in unit tests.

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

There is no `--all` flag. `--limit 20` covers the print-only live book (12 tickets). Do not combine `--submit-paper` with `--apply-local`. Kill remote paper POSTs by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

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

`n_tickets=12` / `n_skipped=0`. Same signal. Live intensity changed size; MSFT and AAPL entered the book.

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

Qty prefers fixture `entry_px`, then a paper IEX last trade. These are not execution marks and not alpha.

## Submit

Pending this run: `rebalance --fixtures --live --submit-paper --limit 20`. Results (every order id + status) land in the next revision of this file after the paper POST.

## Performance snapshot

```bash
python3 -m signal_sim paper-performance --write
```

Writes `docs/performance/YYYY-MM-DD.json`. Labeled `paper`, `alpha=false`, `live_money=false`. Read-only GETs of account / positions / clock / orders / fills. Not a live-money score.

## What this is not

- Not live money. The resolved host is the paper host.
- Not alpha. Fixture-mark and paper IEX sizing marks are plumbing.
- Not a claim that pending paper day orders while the session is closed are fills.

See [operate readiness](operate-readiness.md) and [paper submit smoke](paper-submit-smoke.md).
