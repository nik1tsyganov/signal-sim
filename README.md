# Signal Sim

Signal Sim is a paper-only signal desk for US-listed technology, energy, media, and ETF names.

The project will join market data with time-stamped news-intensity features. It will test event-driven and stochastic methods against strict forward-only evaluation.

Start with [the paper-trading and quant research](docs/paper-trading-and-quant.md). For what the paper loop can run today, what is still blocked on the owner, and what must never be claimed, see [operate readiness](docs/operate-readiness.md). The paper operate loop is summarized in [CHANGELOG.md](CHANGELOG.md).

The initial execution target is a local simulated ledger. Alpaca paper is the preferred later broker adapter for cash equities and ETFs. IBKR remains a later option when broader instruments justify its desktop gateway and account requirements.

This repository must not connect to live money. It contains no live trading engine and no credentials. With owner paper keys, it can read an Alpaca paper account, pull live intel, write a daily research book (`research --live`), and print a position-aware paper rebalance that buys and sells. Local fills stay on the ledger. Remote Alpaca paper POSTs and DELETEs stay off unless `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1` and an explicit `paper-submit` / `--submit-paper` / `paper-cancel` flag. Weekday commands: [daily ops](docs/daily-ops.md).

There is no third-party install step. The package uses the Python standard library. From a repo checkout, `pip install -e .` makes `python -m signal_sim` work without a `PYTHONPATH` hack. Do not add Yahoo, Stooq, or broker SDKs. On Windows the launcher is usually `python`; on Linux it is often `python3`.

## Test

From the repository root (no extra packages):

```powershell
python -m unittest discover -s tests -v
```

```bash
python3 -m unittest discover -s tests -v
```

CI (`.github/workflows/test.yml`) runs that suite, then `rails --fixtures` and `smoke --fixtures`. No secrets. Local only.

## Paper operate

All of these require `--fixtures`. Omitting that flag exits with status 2. Every `total_pnl` / `ending_equity` is **fixture-mark PnL**, not a live or vendor-bar result. There is no live broker and no Yahoo/Stooq execution mark.

- `replay --fixtures` — paper ledger on the liquid sector mark book
- `drift --fixtures` — cluster-drift target book (not alpha)
- `walkforward --fixtures` — two expanding folds plus comparisons
- `shadow --fixtures` — frozen operate report of that harness (`GET /api/shadow` is the same JSON without writing artifacts)
- `rails --fixtures` — local rails only: live-host construct, a temp `KILL`, and a research/vendor mark (`GET /api/rails` is the same JSON). No live HTTP. No repo-root `KILL`.
- `smoke --fixtures` — one frozen-params pass of rails / rank / diagnose / intensity / drift / replay / walkforward / shadow (`GET /api/smoke` is the same JSON without writing artifacts). Rails assert live-host construct, a temp `KILL`, and a research/vendor mark without live calls.
- `diagnose --fixtures` — Hawkes / clusters / intel / confirms (not a rank input)
- `intensity --fixtures` — declared Hawkes intensity at the same `decision_at` cut (`GET /api/intensity` is the same JSON)
- `research --live` — weekday intel book (expanded universe + proposed targets) written to `docs/research/YYYY-MM-DD.json`. Drives `rebalance --fixtures --live`. See [daily ops](docs/daily-ops.md).
- `paper-cancel` — gated paper-host DELETE of one order UUID or `--open --limit <n>` working orders. Same rails as submit. Flag `0` never DELETEs. The 11 oversized 2026-09-04 orders were canceled; the live-session equal-weight submit is in [paper live submit](docs/paper-live-submit.md); the later conviction-weight submit is in [paper conviction submit](docs/paper-conviction-submit.md).

`rank`, `intensity`, and `marks` also require `--fixtures`. Those commands read checked-in files under `fixtures/`. `rank --fixtures`, `intensity --fixtures`, `diagnose --fixtures`, and `GET /api/rank` cut at the default mark-book `decision_at`, the same window replay uses. Prints first seen after that decision do not change the rank or the intensity. Every operate JSON (diagnose, drift, intensity, walkforward, shadow, replay) carries `params` plus `params_sha256` from `fixtures/params.json`. `GET /api/params` is that stamp alone. Each R8 audit line cites the same digest; a fill whose provenance digest does not match the manifest fails closed. `filled_at` must be after `decision_at`. There is no live-money broker and no vendor-bar execution mark. Constructing a live Alpaca host or IBKR live ports raises. The Alpaca paper host without keys is still a stub (`NotImplementedError`) and never opens a socket. With `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET`, `paper-account` is a read-only paper smoke. `feeds --live` pulls Quiver and World Monitor counts when those keys are set. Local fills still require `fixture_mark` on the ledger. Remote paper POSTs need `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1` plus `paper-submit` or `--submit-paper`. A present or unreadable `KILL` file refuses `submit_paper_order`. Tests fail if known GPL/AGPL source trees or verbatim TrendRadar/WorldMonitor README copies appear in the repo.

Every name in `fixtures/universe.json` either has a real fixture mark or cannot fill. Default `replay --fixtures` sizes the liquid sector book in `fixtures/marks/liquid.json`: tagged `fixture_mark` rows for NVDA/MSFT (tech), XLE/XOM (energy), DIS/NFLX (media), and SPY/QQQ (ETF). `--marks liquid` is the same book. `--marks two-name` (or `fixtures/marks/universe.json`) is the older NVDA/XLE book. Other ranked names are refused with `no_mark`. That skip is honest: the allocator does not invent a 100.0 fill. AAPL, CVX, CMCSA, and XLK have checked-in fixture news so each sector gap can enter the rank cut; they still have no fixture mark. AMZN, GOOGL, and META have no checked-in print at `decision_at` and are listed as `no_print` by `marks --fixtures` — they cannot rank until a fixture print exists. These are research fixtures, not Yahoo/Stooq/vendor bars. Prints are admitted on `observed_at` / `first_seen_at` only; `occurred_at` and congress trade dates do not fill.

```bash
python3 -m signal_sim replay --fixtures
python3 -m signal_sim replay --fixtures --drift
python3 -m signal_sim drift --fixtures
python3 -m signal_sim diagnose --fixtures
python3 -m signal_sim walkforward --fixtures
python3 -m signal_sim shadow --fixtures
python3 -m signal_sim rails --fixtures
python3 -m signal_sim smoke --fixtures
```

Also:

```bash
python3 -m signal_sim replay --fixtures --ledger paper-replay.sqlite
python3 -m signal_sim replay --fixtures --marks liquid
python3 -m signal_sim replay --fixtures --marks two-name
python3 -m signal_sim replay --fixtures --path
python3 -m signal_sim replay --fixtures --path --drift
python3 -m signal_sim drift --fixtures --intensity
python3 -m signal_sim replay --fixtures --drift --intensity
```

`drift --fixtures` is the first directional baseline stub (docs method #3). It scores online news clusters at the mark-book `decision_at` and emits a signed `target_frac` + horizon. The half-life is declared, not fitted. The output is a target book for the paper ledger. It is not alpha and not a fitted return model. `rank` is unchanged. `replay --fixtures --drift` sizes that book; unmarked names are still `no_mark`. `--intensity` attaches the declared Hawkes intensity from `diagnose` / `intensity_at` (same baseline, excitation, and decay; not a fit) so the sizer can shrink size as a risk overlay. It never raises size and does not change `rank_candidates`.

`--path --drift` walks the same liquid mark path, but sizes each step from cluster drift at that step's `decision_at`. Mid-path fixture prints (after the default 10:15Z cut) can add or reduce names across sectors. `position_history` keeps the held book per step. Default `--path` still uses the checked-in candidate list. PnL on both paths is fixture-mark PnL.

`walkforward --fixtures` runs two expanding fixture-mark folds with a purge/embargo that covers each fold's label horizon plus `decision_delay_hours`. Each fold reports its own fixture-mark PnL for the declared drift stub, plus no-news, shuffled-news, and news-only comparisons on the same clocks. Those numbers are not a search target and are not combined into a fitted score.

`shadow --fixtures` is the frozen operate path for that same harness. It writes `shadow-paper-walkforward.json` under `$SIGNAL_SIM_ARTIFACTS`, `/opt/cursor/artifacts`, or a repo `artifacts/` directory when one of those exists; otherwise it prints the report to stdout only. Params in the report are declared constants. Do not search them. `GET /api/shadow` returns the same JSON without writing artifacts and without placing desk orders.

Recorded World Monitor JSON under `fixtures/recorded/worldmonitor/` attaches as `intel_brief` / `wm_intel` / `chokepoint` flags on the drift book and diagnose. The checked-in TrendRadar hotspot fixture attaches as `trendradar` on the same `observed_at` rule. Filed insider/congress prints also attach `insider_lag_hours` / `congress_lag_hours` (hours from `filed_at` to `observed_at` on the latest admitted filing). Gov-contract fixtures attach as `gov_confirm` on the same filed/observed rule. Those flags and lags are feature-only on the drift book and diagnose; they do not change size. `rank_candidates` already counts `gov_confirm` in score and is left unchanged. Daily `research --live` uses a separate declared score' plus conviction weights ([research-conviction.md](docs/research-conviction.md)); that path is not fitted and is not alpha. Declared operate constants live in `fixtures/params.json`. Live World Monitor still raises without a key and does not open HTTP. There is no TrendRadar live client and no GPL/AGPL vendoring.

`--path` walks `fixtures/marks/path.json`: three fixture steps on one ledger across the sector mark set (open NVDA/XOM/DIS/QQQ → rotate in MSFT/NFLX → hold MSFT/SPY). AAPL is `no_mark` on every step. Rankings on that path are a test input. Marks stay fixtures. Ordering is `observed_at` / `decision_at`. This is not a market and not a live result. After the run, `account` and `positions` are the latest snapshot (last step). `account_history` keeps one row per step; those `ending_equity` values match `equity_curve`. `position_history` keeps the held book per step so a mid-path open and later reduce/close stay visible. `<ledger>.run.jsonl` still appends each step JSON.

Desk (same paper loop as `replay --fixtures`):

```bash
python3 -m signal_sim serve
```

Then `POST /api/replay` against the loopback desk (default port 8765) to run the liquid sector book. Example:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/replay
```

Read-only desk diagnostics (same JSON as `diagnose --fixtures` and `drift --fixtures`):

```bash
curl -sS http://127.0.0.1:8765/api/rails
curl -sS http://127.0.0.1:8765/api/params
curl -sS http://127.0.0.1:8765/api/diagnose
curl -sS http://127.0.0.1:8765/api/intensity
curl -sS http://127.0.0.1:8765/api/drift
curl -sS http://127.0.0.1:8765/api/walkforward
curl -sS http://127.0.0.1:8765/api/shadow
curl -sS http://127.0.0.1:8765/api/smoke
```

Three-step paper path (same loop as `replay --fixtures --path`):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/path
```

Sector mark book (same loop as `replay --fixtures --marks fixtures/marks/liquid.json`):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/liquid
```

`GET /api/replay`, `GET /api/path`, and `GET /api/liquid` return 405 and do not place orders. `GET /api/walkforward` is the same fixture harness as `walkforward --fixtures` and does not place desk orders. `GET /api/shadow` is the same frozen report as `shadow --fixtures` without writing artifacts. `GET /api/params` is the frozen operate stamp. `GET /api/intensity` matches `intensity --fixtures`. `GET /api/rails` is the same local rails check as `rails --fixtures`. `GET /api/smoke` is the same frozen-params operate pass as `smoke --fixtures` and does not write artifacts or place desk orders. The browser page at that loopback URL loads `GET /api/rank`, `GET /api/marks`, `GET /api/params`, `GET /api/diagnose`, `GET /api/intensity`, `GET /api/drift`, `GET /api/walkforward`, `GET /api/shadow`, and `GET /api/rails`, and has buttons for `POST /api/replay` (default liquid sector book), `POST /api/liquid` (same book), `POST /api/path`, and `GET /api/smoke`. The rank table labels default-fill vs `no_mark` before anyone posts. The Marks section lists the frozen universe, who can fill, `no_print` names that never ranked, and who is not in the rank cut. Diagnose shows Hawkes intensity and intel flags at `decision_at`. Intensity uses the same cut. Drift targets show the cluster-drift book plus insider/congress confirms and recorded intel flags. Bind is loopback only. Paper only.

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
python3 -m signal_sim drift --fixtures
```

`marks` lists who can fill on the default liquid book (same as `--marks liquid`), the older two-name book, who stays `no_mark`, and who is `no_print` (no checked-in print at `decision_at`). It does not rank or place orders.

`diagnose` prints Hawkes intensity and online clusters cut at the default mark-book `decision_at`, the same window replay uses. Prints first seen after that decision are counted in `n_events_after_decision` and excluded from the intensity. It is not a ranking input and not a return. Do not change `rank_candidates` to chase fixture-mark PnL.

`replay` uses `rank_candidates` as-is (unless a mark book supplies an explicit `candidates` list, as the path fixture does). Names without a fixture mark are refused `no_mark` before sizing, so they do not consume `max_gross_frac`. A sizer turns each remaining positive-score name into a signed long target of `size_frac` with a horizon equal to the fixture `decision_at`→`exit_at` window. The local ledger opens, adds, reduces, or closes to that book, subject to cash and a prior-run drawdown halt. Rebalance is share-accurate at the decision mark: a close sells held shares, not the sum of prior `size_frac` tickets. Ending equity is cash plus remaining shares at `exit_px`, so intra-path sells keep realized PnL. `cost_bps` (default 0) is a declared bid-ask fee on each fill. `decision_delay_hours` (default 1) sets `fill_at` after `decision_at`; the fill price is still the fixture `entry_px`. Replay stamps that fixture `fill_at` onto the ledger fill row. It does not use `occurred_at`, a congress trade date, or wall-clock `now()` for the paper clock. Online news clusters in replay `stats` are rebuilt at `decision_at` and are not a ranking input. The JSON `stats` object is from the run (hit rate, turnover, winner/loser counts, Hawkes arrivals in the decision→exit window). Every PnL number is fixture-mark PnL, not a market backtest. The frozen ticker list is `fixtures/universe.json`. The sizer has no 3-name ceiling; `max_name_frac` and `max_gross_frac` are the size rails. World Monitor / Quiver live adapters stay stubbed without keys; recorded JSON under `fixtures/recorded/` maps offline. With owner keys, `feeds --live` prints live counts and a ticker histogram only.

### Live intel and Alpaca paper read (owner keys, optional)

These commands are not part of `smoke --fixtures`. They exit 2 when the required env names are missing. They never print secret values.

Cursor Cloud agents should launch with the saved environment **`signal-sim-paper`** and Dashboard Runtime Secrets (`ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET`, `QUIVER_API_KEY`, `WORLD_MONITOR_KEY`). Set `ALPACA_PAPER_API_BASE_URL` to the paper HTTPS origin. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` defaults to `0`. Do not commit a `.env`.

```bash
python3 -m signal_sim runtime-env
python3 -m signal_sim feeds --live
python3 -m signal_sim research --live
python3 -m signal_sim paper-account
python3 -m signal_sim paper-account --dry-run
python3 -m signal_sim rebalance --fixtures
python3 -m signal_sim rebalance --fixtures --apply-local --ledger paper-rebalance.sqlite
python3 -m signal_sim ledger --ledger paper-rebalance.sqlite --fixtures
python3 -m signal_sim paper-submit --symbol SPY --qty 1
python3 -m signal_sim paper-cancel --open --limit 1
python3 -m signal_sim rebalance --fixtures --submit-paper --limit 1
python3 -m signal_sim paper-performance --write
python3 -m signal_sim telemetry --write
```

`rebalance --fixtures` prints intended tickets from the existing fixture / drift target book versus the paper account. It does not POST. Qty prefers fixture `entry_px`, then a paper IEX last trade or snapshot if one is returned. `--live` pulls Quiver / World Monitor into the Hawkes overlay without submitting. `--apply-local` records only `mark_source=fixture` tickets on the local ledger through `submit_paper_order`. Paper IEX marks size qty but never become fills. Default stays print-only. `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1` plus `paper-submit` or `rebalance --fixtures --submit-paper` POSTs on the paper host only (default `--limit 1`; pass a high `--limit` to cover the print-only book). `--fixtures --live --submit-paper` is allowed. Set the flag to `0` to kill remote paper POSTs. There is still no live-money trading. `ledger --ledger` is the read-only morning-brief inspect of the local sqlite. `paper-performance --write` is the morning-brief cite of the Alpaca paper account (equity/cash/positions/orders/fills into `docs/performance/YYYY-MM-DD.json`). `telemetry --write` is the morning-brief cite that joins that snapshot to today's research book (`docs/telemetry/YYYY-MM-DD.json`). Those inspect paths do not POST. A 2026-09-04 local book smoke with cite counts is in [local book smoke](docs/local-book-smoke.md). A 2026-09-04 one-share paper POST (`SPY` x1, status `new`, unfilled, market closed) is in [paper submit smoke](docs/paper-submit-smoke.md). Strategy-book paper POSTs and the snapshot command are in [paper strategy submit](docs/paper-strategy-submit.md). The same-day cancel of those working day orders (including fixture-priced QQQ/SPY) is in [paper order cancel](docs/paper-order-cancel.md).

The desk refuses to start unless the paper-only flag is on and no `KILL` file sits in the repository root.

See [operate readiness](docs/operate-readiness.md), [intel sources](docs/intel-sources.md), [paper trading and quant research](docs/paper-trading-and-quant.md), and [alternative data and safety](docs/alt-data-and-safety.md) for the source and safety rules.
