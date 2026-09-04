# Paper order cancel (2026-09-04)

Canceled the 11 open Alpaca **paper** day orders that were still working after PR 11 / PR 12, including the fixture-priced QQQ ~278 and SPY ~250 tickets that reserved buying power. Paper host only. No live money. **No new `--submit-paper`.** Secret values were never printed or written into this file.

**Morning-brief cite:** `python3 -m signal_sim paper-performance --write` → [2026-09-04.json](performance/2026-09-04.json)

**Cancel cite:** `paper-cancel --open --limit 11` once on `main` after PR 12 (`fc07f4b`). `n_cancelled=11` / `n_errors=0`. GET-by-id read-back: every order `status=canceled`, `filled_qty=0`. Open list empty. Buying power returned to `$400000` (4× `$100000` paper). Clock still closed.

## Environment

- Checkout: `main` @ `fc07f4b` (PR 12 already merged: daily research + live sizing + gated `paper-cancel`), then this docs branch.
- Install: `pip install -e .`
- Python: 3.12.3
- `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` is exactly `1`. Same rails as submit (flag, paper-api host, keys, explicit CLI). Flag `0` never DELETEs.

`runtime-env` presence only (names, not values):

| Name | Present |
|---|---|
| `ALPACA_PAPER_API_KEY` | yes |
| `ALPACA_PAPER_API_SECRET` | yes |
| `ALPACA_PAPER_API_BASE_URL` | yes |
| `QUIVER_API_KEY` | yes |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes (exactly `1`) |

Resolved paper origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo). Hostname equals `paper-api.alpaca.markets`.

Reproduce (paper host + flag `1` + keys; do not point at a live host):

```bash
python3 -m pip install -e .
python3 -m signal_sim runtime-env
python3 -m signal_sim paper-performance
python3 -m signal_sim paper-cancel --open --limit 11
python3 -m signal_sim research --live
python3 -m signal_sim rebalance --fixtures --live
python3 -m signal_sim paper-performance --write
```

There is no `--all` flag. `--limit 11` covered the 11 working day orders. Do not add `--submit-paper` in this sequence. Kill remote paper DELETE/POST by setting `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

## Open book before cancel

`paper-performance` at `2026-09-04T12:32:49Z` (08:32 ET; `is_open=false`; `next_open=2026-09-04T09:30:00-04:00`). Positions `n=0`. Fills `n=0`. Cash / equity `$100000`. Buying power `$1697.69` (pending day orders reserved the rest; earlier snapshots had `$51.64` / `$412.23` as more tickets queued).

| Order id | Symbol | Qty | Status | Class |
|---|---|---:|---|---|
| `94e7349e-0391-48f2-9134-1a41f9694141` | QQQ | 277.644101 | `new` | **oversized** fixture ~$36 |
| `f614eadd-aa3c-44d2-9f04-19b4dc69c595` | SPY | 249.75944 | `new` | **oversized** fixture ~$40 |
| `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` | SPY | 1 | `new` | smoke `ps:SPY:buy:q:1` |
| `fd21fd91-8799-4639-ae9f-f0b427f2e4b1` | AAPL | 0.268771 | `accepted` | small live-sized |
| `66d07fac-67be-4a36-a276-c5294379d324` | NVDA | 0.4622 | `accepted` | small live-sized |
| `e8e16189-ff98-4885-b0b0-a05de1a1a037` | XLE | 1.058664 | `new` | small live-sized |
| `795a127e-f339-4640-b15a-a60e04dff5a9` | CVX | 1.889109 | `new` | small live-sized |
| `8c9b2713-3937-44b5-8a93-57d5a529ae96` | DIS | 7.201601 | `new` | small live-sized |
| `fd653012-beb8-4599-a3ed-dd81387dda3c` | XOM | 12.094306 | `new` | small live-sized |
| `53b2f6ad-a24f-42c5-9415-45e2cf4ee7ed` | CMCSA | 14.984865 | `new` | small live-sized |
| `ba0aabd2-10b5-4c26-b8fa-eb8f31004010` | NFLX | 20.981685 | `new` | small live-sized |

All eleven were this-morning paper day orders (`time_in_force=day`, `type=market`, `filled_qty=0`). The two fixture-priced ETF tickets were the buying-power hogs. This run canceled **all 11** (including the smoke SPY x1) for a clean slate before the open. No second full-book submit.

Client order ids for the ten strategy tickets are `rebalance:2026-09-02T10:15:00Z:<SYM>:buy:open`. The smoke is `ps:SPY:buy:q:1`.

## Cancel

```bash
python3 -m signal_sim paper-cancel --open --limit 11
```

Exit 0. `mode=alpaca-paper-cancel`. `ok=true`. `submitted=false`. `cancelled=true`. `order_post=disabled`. `order_delete=paper`. `submit_flag=1`. `n_cancelled=11`. `n_errors=0`. Alpaca DELETE often returns an empty body; ids were taken from the open list, then confirmed with GET `/v2/orders/{id}`.

| Order id | Symbol | Qty | Status after | `canceled_at` |
|---|---|---:|---|---|
| `fd21fd91-8799-4639-ae9f-f0b427f2e4b1` | AAPL | 0.268771 | `canceled` | `2026-09-04T12:33:02.645218624Z` |
| `fd653012-beb8-4599-a3ed-dd81387dda3c` | XOM | 12.094306 | `canceled` | `2026-09-04T12:33:02.674133662Z` |
| `795a127e-f339-4640-b15a-a60e04dff5a9` | CVX | 1.889109 | `canceled` | `2026-09-04T12:33:02.688867769Z` |
| `66d07fac-67be-4a36-a276-c5294379d324` | NVDA | 0.4622 | `canceled` | `2026-09-04T12:33:02.716736131Z` |
| `ba0aabd2-10b5-4c26-b8fa-eb8f31004010` | NFLX | 20.981685 | `canceled` | `2026-09-04T12:33:02.740462502Z` |
| `53b2f6ad-a24f-42c5-9415-45e2cf4ee7ed` | CMCSA | 14.984865 | `canceled` | `2026-09-04T12:33:02.75746782Z` |
| `8c9b2713-3937-44b5-8a93-57d5a529ae96` | DIS | 7.201601 | `canceled` | `2026-09-04T12:33:02.777697537Z` |
| `f614eadd-aa3c-44d2-9f04-19b4dc69c595` | SPY | 249.75944 | `canceled` | `2026-09-04T12:33:02.796932195Z` |
| `94e7349e-0391-48f2-9134-1a41f9694141` | QQQ | 277.644101 | `canceled` | `2026-09-04T12:33:02.815715192Z` |
| `e8e16189-ff98-4885-b0b0-a05de1a1a037` | XLE | 1.058664 | `canceled` | `2026-09-04T12:33:02.833777479Z` |
| `d7629fcb-ba1a-4c8d-a732-63b0f61cf12a` | SPY | 1 | `canceled` | `2026-09-04T12:33:02.857763156Z` |

`orders(status=open)` is empty. `filled_qty` stayed `0` on every id. No POST to `/v2/orders`.

## Print-only book after cancel (no submit)

Clock still closed (`2026-09-04T08:33` ET). Positions still `n=0`.

### `research --live`

Exit 0. Wrote [2026-09-04.json](research/2026-09-04.json). 27-name operating universe (15 fixture ∪ top-12 allowlisted intel: ABT, HD, AMAT, UNH, INTC, JNJ, PG, PEP, T, CSCO, LLY, MA). Proposed book `n_targets=10` (XLE, MSFT, NFLX, NVDA, AAPL, CMCSA, CVX, DIS, SPY, XOM). QQQ and the remaining intel names skipped `gross_frac_cap`. No PII.

### `rebalance --fixtures --live`

Exit 0. `mode=paper-rebalance-dry-run`. `ok=true`. `submitted=false`. `local_applied=false`. `order_post=disabled`. `prefer_paper_marks=true`. Signal `research-live`. Allocation `100000`. Buying power `$400000`. `n_tickets=10` / `n_skipped=0`. stderr only reminded that flag `1` still requires `--submit-paper`.

Every ticket is a paper IEX `last_trade` (`source=alpaca_paper_data`), `$10000` notional:

| Symbol | Side | Qty | Mark px | Action |
|---|---|---:|---:|---|
| XLE | buy | 154.726907 | 64.63 | open |
| MSFT | buy | 19.615536 | 509.80 | open |
| NFLX | buy | 120.948234 | 82.68 | open |
| NVDA | buy | 43.756016 | 228.54 | open |
| AAPL | buy | 30.587588 | 326.93 | open |
| CMCSA | buy | 375.093773 | 26.66 | open |
| CVX | buy | 47.332797 | 211.27 | open |
| DIS | buy | 93.331467 | 107.145 | open |
| SPY | buy | 12.962435 | 771.46 | open |
| XOM | buy | 61.640880 | 162.23 | open |

SPY is ~13 shares at the live tape (~$771), not the fixture `$40` → ~250. QQQ is not in this grown book. These qtys are live-sized notionals, not the earlier fixture-priced spray. **`--submit-paper` was not run.** Next submit waits for the open (or a later clean print) and uses the position-aware diff.

## Performance snapshot after cancel

```bash
python3 -m signal_sim paper-performance --write
```

Wrote [docs/performance/2026-09-04.json](performance/2026-09-04.json) at `2026-09-04T12:34:07Z`. Labeled `paper`, `alpha=false`, `live_money=false`. Compact copy: [docs/research/2026-09-04-paper.json](research/2026-09-04-paper.json).

| Check | Result |
|---|---|
| Open orders | `n=0` |
| Recent orders | `n=11`, all `canceled` |
| Positions | `n=0` |
| Fills | `n=0` |
| Cash / equity | `100000` / `100000` |
| Buying power | `400000` |
| Clock | `is_open=false`; `next_open=2026-09-04T09:30:00-04:00` |

## What this is not

- Not live money. The resolved host was the paper host.
- Not a fill. Every `filled_qty` stayed `0`. Positions stayed empty.
- Not a new book submit. `--submit-paper` was not passed.
- Not alpha. Live IEX last trades are sizing marks only.

See [daily ops](daily-ops.md), [operate readiness](operate-readiness.md), and [paper strategy submit](paper-strategy-submit.md).
