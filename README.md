# Signal Sim

Signal Sim is a paper-only signal desk for US-listed technology, energy, media, and ETF names.

The project will join market data with time-stamped news-intensity features. It will test event-driven and stochastic methods against strict forward-only evaluation.

Start with [the paper-trading and quant research](docs/paper-trading-and-quant.md).

The initial execution target is a local simulated ledger. Alpaca paper is the preferred later broker adapter for cash equities and ETFs. IBKR remains a later option when broader instruments justify its desktop gateway and account requirements.

This repository must not connect to live money. It contains no trading engine, broker account, broker software, or credentials.

There is no third-party install step. The package uses the Python standard library. On Windows the launcher is usually `python`; on Linux it is often `python3`.

## Test

From the repository root (no extra packages):

```powershell
python -m unittest discover -s tests -v
```

```bash
python3 -m unittest discover -s tests -v
```

## Run

`rank`, `intensity`, and `replay` require `--fixtures`. That flag is the only supported input; omitting it exits with status 2. Those commands read the checked-in files under `fixtures/`.

Rank local fixture events as paper-trade candidates:

```powershell
python -m signal_sim rank --fixtures
```

```bash
python3 -m signal_sim rank --fixtures
```

Hawkes intensity at the latest fixture `observed_at` (one number per universe ticker; not a return or Sharpe):

```powershell
python -m signal_sim intensity --fixtures
```

```bash
python3 -m signal_sim intensity --fixtures
```

Replay the existing rank signal through the local paper ledger (fixture entry marks, fixture exit marks, mark-to-market PnL). This is not a vendor price feed and not a live broker:

```powershell
python -m signal_sim replay --fixtures
```

```bash
python3 -m signal_sim replay --fixtures
```

Optional durable ledger:

```bash
python3 -m signal_sim replay --fixtures --ledger paper-replay.sqlite
```

`replay` uses `rank_candidates` as-is. A sizer turns each positive-score name into a signed long target of `size_frac` with a horizon equal to the fixture `decision_at`→`exit_at` window. The local ledger opens, adds, reduces, or closes to that book, subject to cash and a prior-run drawdown halt. Rebalance is share-accurate at the decision mark: a close sells held shares, not the sum of prior `size_frac` tickets. Ending equity is cash plus remaining shares at `exit_px`, so intra-path sells keep realized PnL. Fills use `fixtures/marks/universe.json` `entry_px` and mark to `exit_px`. `--path` walks `fixtures/marks/path.json` (two fixture steps, still not vendor bars) and reports an equity curve plus worst drawdown from those steps. The JSON `stats` object is from the run (hit rate, turnover, winner/loser counts, Hawkes arrivals in the decision→exit window). It is not a Sharpe or a market backtest. The frozen ticker list is `fixtures/universe.json` (more than three names). Tests may pass a smaller basket. This is not a vendor price feed and not a live broker.

Two-step fixture path:

```bash
python3 -m signal_sim replay --fixtures --path
```

### Desk

Start the local paper-only desk:

```powershell
python -m signal_sim serve
```

```bash
python3 -m signal_sim serve
```

Then open http://127.0.0.1:8765/ in a browser. The desk is paper-only: it binds only to 127.0.0.1, ranks the same local fixture events as `rank --fixtures`, and connects to no live broker. `GET /api/rank` is read-only. `POST /api/replay` runs the paper ledger loop; `GET /api/replay` returns 405 and does not place orders. It refuses to start unless the paper-only flag is on and no `KILL` file sits in the repository root.

See [intel sources](docs/intel-sources.md), [paper trading and quant research](docs/paper-trading-and-quant.md), and [alternative data and safety](docs/alt-data-and-safety.md) for the source and safety rules.
