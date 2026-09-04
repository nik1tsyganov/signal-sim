# Daily paper ops

Weekday loop for Alpaca **paper** only. This is not live money. Do not point the client at a live host. Kill remote paper POSTs at any time with `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`.

The research artifact is the next rebalance book. It is not a decorative JSON.

## Morning (before the open)

```bash
python3 -m signal_sim research --live
```

Pulls Quiver (congress, insider, gov contracts, news) and World Monitor, expands the operating universe to the frozen fixture set union top-N allowlisted intel tickers, then ranks / diagnoses / intensity on that set. Writes `docs/research/YYYY-MM-DD.json`: counts, top tickers, and the proposed book. No person names, headlines, or URLs.

Safe to run every weekday morning. Missing intel keys exit 2.

## After the open (print the grown book first)

```bash
python3 -m signal_sim rebalance --fixtures --live
```

Loads today's research artifact when present (otherwise computes the same book). Diffs that target book against paper positions: **buys and sells**, including leftover closes. Qty prefers an observed paper IEX last trade or snapshot `latestTrade` when one exists. Fixture `$36`/`$40` QQQ/SPY marks are not used to size a live or `--submit-paper` ticket when a paper mark is available. Never invents a price.

Print-only. No POST.

If paper orders from earlier in the day are still open, **do not spray another full-book submit**. Wait for fills, then rerun the print. The position-aware diff will only ticket what is still off-target.

**2026-09-04 print smoke (this PR):** clock closed (08:21 ET; next open 09:30 ET). Positions `n=0`. **11 open paper orders** still working (`accepted`/`new`, `filled_qty=0`), including oversized fixture-priced QQQ ~278 and SPY ~250 from earlier today plus the one-share SPY. Those working orders have reserved most paper buying power (~$412 left). `research --live` wrote `docs/research/2026-09-04.json` (27-name operating universe; new intel names in the rank: ABT, AMAT, HD, …). Print-only `rebalance --fixtures --live` sized the grown book from paper IEX last trades (SPY ~$773 → ~13 shares, not the fixture $40 → ~250). **No `--submit-paper` in this PR.** Next submit waits for those 11 to fill or cancel, then uses the position-aware diff.

## Submit (paper host, flag=1, explicit)

```bash
python3 -m signal_sim rebalance --fixtures --live --submit-paper --limit 4
```

Requires `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1`, the paper-api host, keys, and `--submit-paper`. Default `--limit` is 1 (smallest notional first). Raise the limit only after the print looks sane. A non-paper host is refused. Flag `0` never POSTs.

## After fills

```bash
python3 -m signal_sim paper-performance --write
```

Writes `docs/performance/YYYY-MM-DD.json`: sanitized paper equity/cash/positions, open orders, and fills. Not alpha.

## Kill switch

```bash
# omit the name or:
SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0
```

Remote `/v2/orders` POSTs stay off. Print-only research and rebalance still run.

See [operate readiness](operate-readiness.md).
