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

## Operate (paper only)

`rank`, `intensity`, `diagnose`, `marks`, `drift`, and `replay` require `--fixtures`. Omitting that flag exits with status 2. Those commands read checked-in files under `fixtures/`. `rank --fixtures` and `GET /api/rank` cut at the default mark-book `decision_at`, the same window replay uses. Prints first seen after that decision do not change the rank. There is no live broker, no vendor bars, and no Quiver live path.

Every name in `fixtures/universe.json` either has a real fixture mark or cannot fill. Default `replay --fixtures` sizes the liquid sector book in `fixtures/marks/liquid.json`: tagged `fixture_mark` rows for NVDA/MSFT (tech), XLE/XOM (energy), DIS/NFLX (media), and SPY/QQQ (ETF). `--marks liquid` is the same book. `--marks two-name` (or `fixtures/marks/universe.json`) is the older NVDA/XLE book. Other ranked names are refused with `no_mark`. That skip is honest: the allocator does not invent a 100.0 fill. AAPL, CVX, CMCSA, and XLK have checked-in fixture news so each sector gap can enter the rank cut; they still have no fixture mark. AMZN, GOOGL, and META have no checked-in print at `decision_at` and are listed as `no_print` by `marks --fixtures` — they cannot rank until a fixture print exists. These are research fixtures, not Yahoo/Stooq/vendor bars. Prints are admitted on `observed_at` / `first_seen_at` only; `occurred_at` and congress trade dates do not fill.

```bash
python3 -m signal_sim replay --fixtures
python3 -m signal_sim replay --fixtures --ledger paper-replay.sqlite
python3 -m signal_sim replay --fixtures --marks liquid
python3 -m signal_sim replay --fixtures --marks two-name
python3 -m signal_sim replay --fixtures --path
python3 -m signal_sim drift --fixtures
python3 -m signal_sim replay --fixtures --drift
```

`drift --fixtures` is the first directional baseline stub (docs method #3). It scores online news clusters at the mark-book `decision_at` and emits a signed `target_frac` + horizon. The half-life is declared, not fitted. The output is a target book for the paper ledger. It is not alpha and not a fitted return model. `rank` is unchanged. `replay --fixtures --drift` sizes that book; unmarked names are still `no_mark`.

`--path` walks `fixtures/marks/path.json`: three fixture steps on one ledger across the sector mark set (open NVDA/XOM/DIS/QQQ → rotate in MSFT/NFLX → hold MSFT/SPY). AAPL is `no_mark` on every step. Rankings on that path are a test input. Marks stay fixtures. Ordering is `observed_at` / `decision_at`. This is not a market and not a live result. After the run, `account` and `positions` are the latest snapshot (last step). `account_history` keeps one row per step; those `ending_equity` values match `equity_curve`. `position_history` keeps the held book per step so a mid-path open and later reduce/close stay visible. `<ledger>.run.jsonl` still appends each step JSON.

Desk (same paper loop as `replay --fixtures`):

```bash
python3 -m signal_sim serve
```

Then `POST /api/replay` against the loopback desk (default port 8765) to run the liquid sector book. Example:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/replay
```

Read-only desk diagnostics (same JSON as `diagnose --fixtures`):

```bash
curl -sS http://127.0.0.1:8765/api/diagnose
```

Three-step paper path (same loop as `replay --fixtures --path`):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/path
```

Sector mark book (same loop as `replay --fixtures --marks fixtures/marks/liquid.json`):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/liquid
```

`GET /api/replay`, `GET /api/path`, and `GET /api/liquid` return 405 and do not place orders. The browser page at that loopback URL loads `GET /api/rank`, `GET /api/marks`, and `GET /api/diagnose`, and has buttons for `POST /api/replay` (default liquid sector book), `POST /api/liquid` (same book), and `POST /api/path`. The rank table labels default-fill vs `no_mark` before anyone posts. The Marks section lists the frozen universe, who can fill, `no_print` names that never ranked, and who is not in the rank cut. Bind is loopback only. Paper only.

```powershell
python -m signal_sim replay --fixtures
python -m signal_sim serve
```

### Rank, intensity, diagnose

```bash
python3 -m signal_sim rank --fixtures
python3 -m signal_sim intensity --fixtures
python3 -m signal_sim diagnose --fixtures
python3 -m signal_sim marks --fixtures
```

`marks` lists who can fill on the default liquid book (same as `--marks liquid`), the older two-name book, who stays `no_mark`, and who is `no_print` (no checked-in print at `decision_at`). It does not rank or place orders.

`diagnose` prints Hawkes intensity and online clusters cut at the default mark-book `decision_at`, the same window replay uses. Prints first seen after that decision are counted in `n_events_after_decision` and excluded from the intensity. It is not a ranking input and not a return. Do not change `rank_candidates` to chase fixture-mark PnL.

`replay` uses `rank_candidates` as-is (unless a mark book supplies an explicit `candidates` list, as the path fixture does). Names without a fixture mark are refused `no_mark` before sizing, so they do not consume `max_gross_frac`. A sizer turns each remaining positive-score name into a signed long target of `size_frac` with a horizon equal to the fixture `decision_at`→`exit_at` window. The local ledger opens, adds, reduces, or closes to that book, subject to cash and a prior-run drawdown halt. Rebalance is share-accurate at the decision mark: a close sells held shares, not the sum of prior `size_frac` tickets. Ending equity is cash plus remaining shares at `exit_px`, so intra-path sells keep realized PnL. `cost_bps` (default 0) is a declared bid-ask fee on each fill. `decision_delay_hours` (default 1) sets `fill_at` after `decision_at`; the fill price is still the fixture `entry_px`. Replay stamps that fixture `fill_at` onto the ledger fill row. It does not use `occurred_at`, a congress trade date, or wall-clock `now()` for the paper clock. Online news clusters in replay `stats` are rebuilt at `decision_at` and are not a ranking input. The JSON `stats` object is from the run (hit rate, turnover, winner/loser counts, Hawkes arrivals in the decision→exit window). It is a fixture-mark run, not a market backtest. The frozen ticker list is `fixtures/universe.json`. The sizer has no 3-name ceiling; `max_name_frac` and `max_gross_frac` are the size rails. World Monitor / Quiver live adapters stay stubbed without keys; recorded JSON under `fixtures/recorded/` maps offline.

The desk refuses to start unless the paper-only flag is on and no `KILL` file sits in the repository root.

See [intel sources](docs/intel-sources.md), [paper trading and quant research](docs/paper-trading-and-quant.md), and [alternative data and safety](docs/alt-data-and-safety.md) for the source and safety rules.
