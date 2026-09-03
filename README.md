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

`rank` and `intensity` require `--fixtures`. That flag is the only supported input; omitting it exits with status 2. Both commands read the checked-in files under `fixtures/`.

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

### Desk

Start the local paper-only desk:

```powershell
python -m signal_sim serve
```

```bash
python3 -m signal_sim serve
```

Then open http://127.0.0.1:8765/ in a browser. The desk is paper-only: it binds only to 127.0.0.1, ranks the same local fixture events as `rank --fixtures`, and connects to no live broker. It refuses to start unless the paper-only flag is on and no `KILL` file sits in the repository root.

See [intel sources](docs/intel-sources.md), [paper trading and quant research](docs/paper-trading-and-quant.md), and [alternative data and safety](docs/alt-data-and-safety.md) for the source and safety rules.
