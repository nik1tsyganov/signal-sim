# Daily paper ops

Weekday loop for Alpaca **paper** only. This is not live money. Do not point the client at a live host. Kill remote paper POSTs at any time with `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

The research artifact is the next rebalance book. It is not a decorative JSON.

## Morning (before the open)

```bash
python3 -m signal_sim research --live
```

Pulls Quiver (congress, insider, gov contracts, news) and World Monitor, expands the operating universe to the frozen fixture set union top-N allowlisted intel tickers, then ranks with declared **score'** (un-lumped congress vs insider; small signed `sent_term` only when there is news; not fitted) and writes a **conviction-weighted** `docs/research/YYYY-MM-DD.json`. Higher score' gets more of `max_gross_invest=0.80` (cash reserve / dry powder), capped at paper `max_name_frac=0.20`. Each target stamps `target_frac`. No person names, headlines, or URLs. See [research-conviction.md](research-conviction.md).

Safe to run every weekday morning. Missing intel keys exit 2.

## After the open (print the grown book first)

```bash
python3 -m signal_sim rebalance --fixtures --live
```

Loads today's research artifact when present (otherwise computes the same book). Diffs that conviction book against paper positions: **buys and sells**. Sell priority when several exits fire: **soft_stop ≥ horizon_exit ≥ score_decay ≥ trim**. Also close leftovers that drop out of the book. Tickets stamp `sell_reason`. Qty prefers an observed paper IEX last trade or snapshot `latestTrade` when one exists. Soft-stop MTM uses that decision-time mark versus paper entry — no future bars. Fixture `$36`/`$40` QQQ/SPY marks are not used to size a live or `--submit-paper` ticket when a paper mark is available. Never invents a price.

Print-only. No POST.

If paper orders from earlier in the day are still open, **do not spray another full-book submit**. Wait for fills or cancel them, then rerun the print. The position-aware diff will only ticket what is still off-target.

**2026-09-04 print smoke (PR 12):** clock closed (08:21 ET; next open 09:30 ET). Positions `n=0`. **11 open paper orders** still working (`accepted`/`new`, `filled_qty=0`), including oversized fixture-priced QQQ ~278 and SPY ~250 plus the one-share SPY. Those working orders reserved most paper buying power. Print-only live sizing used paper IEX last trades (SPY ~$773 → ~13 shares, not fixture $40 → ~250). No `--submit-paper` in that PR.

**2026-09-04 cancel (follow-up):** [paper order cancel](paper-order-cancel.md) DELETEd all 11 via `paper-cancel --open --limit 11`. Read-back `canceled` / `filled_qty=0`. Open list empty. Buying power `$400000`. Reprint `research --live` + print-only `rebalance --fixtures --live` (live-sized SPY ~13 @ ~$771). **No new `--submit-paper`.** Next submit waits for the open, then uses the position-aware diff.

**2026-09-04 live-session submit:** [paper live submit](paper-live-submit.md). Clock open. Print-only live-sized (SPY ~13 @ ~$773). First `--submit-paper` only filled MSFT — canceled morning client ids blocked the rest. Date-scoped `rb:{YYYYMMDD}:…` keys, then a second `--submit-paper --limit 20`, posted and filled the remaining live-sized book (10 positions).

**2026-09-04 conviction submit:** [paper conviction submit](paper-conviction-submit.md). Owner-authorized. `research --live` wrote the score' book (NVDA 0.20 cap). Print-only 14 live-sized tickets, then `--submit-paper --limit 30`. All 14 filled: close NFLX/CMCSA/CVX/DIS/SPY/XOM; open GOOGL/HD/ABT/AMAT/UNH/AMZN; add NVDA and XLE. Reprint empty. Do not spray another full-book submit while those conviction names are already held.

## Cancel open paper orders before the next submit

Do not `--submit-paper` while leftover day orders are still working. List them with `paper-performance` (read-only). Cancel on the paper host only when you mean to, with the same rails as submit (`SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1`, paper-api host, keys, explicit CLI):

```bash
python3 -m signal_sim paper-performance
python3 -m signal_sim paper-cancel --order-id <uuid>
python3 -m signal_sim paper-cancel --open --limit 11
```

`--open` uses `--limit` (default 1). There is no `--all`. Flag `0` never DELETEs. A non-paper host is refused. After cancel (or fill), reprint `rebalance --fixtures --live` before any new `--submit-paper`. You can also cancel in the Alpaca paper UI.

## Submit (paper host, flag=1, explicit)

```bash
python3 -m signal_sim rebalance --fixtures --live --submit-paper --limit 4
```

Requires `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1`, the paper-api host, keys, and `--submit-paper`. Default `--limit` is 1 (smallest notional first). Raise the limit only after the print looks sane. A non-paper host is refused. Flag `0` never POSTs. Paper `client_order_id` is date-scoped (`rb:{YYYYMMDD}:…`). A same-day cancel no longer permanently burns the book.

## After fills

```bash
python3 -m signal_sim paper-performance --write
```

Writes `docs/performance/YYYY-MM-DD.json`: sanitized paper equity/cash/positions, open orders, and fills. Not alpha.

Morning-brief cite for “what data moved the book?” / “how did paper PnL move vs yesterday?”:

```bash
python3 -m signal_sim telemetry --write
```

Writes `docs/telemetry/YYYY-MM-DD.json` (optional `--md`): equity/cash/gross/`cash_reserve_frac`, research score'/`target_frac`, feed counts, score' term drivers, sell reasons, and equity Δ vs the prior day's paper snapshot. Same-day artifacts only. Read-only. Paper only. Not alpha.

## Kill switch

```bash
# omit the name or:
SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0
```

Remote `/v2/orders` POSTs stay off. Print-only research and rebalance still run.

See [operate readiness](operate-readiness.md) and [research-conviction.md](research-conviction.md). The research book is still labeled **not fitted / not alpha**.
