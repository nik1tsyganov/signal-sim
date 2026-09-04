# Paper smoke results (2026-09-04)

Cloud Agents run against merged PR 2 (`2edca90` on `main`). Paper only. No live money. Secret values were never printed or written into this file.

## Environment

- Checkout: `main` @ `2edca90`, then this docs branch.
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
| `QUIVER_USERNAME` | yes (unused by `feeds --live`) |
| `WORLD_MONITOR_KEY` | yes |
| `SIGNAL_SIM_ALPACA_PAPER_SUBMIT` | yes |

Resolved paper origin: `https://paper-api.alpaca.markets` (HTTPS, no userinfo). Live Alpaca host construct still raises in unit tests.

## Fixture rails / smoke

Local only. No live HTTP.

```bash
python3 -m signal_sim rails --fixtures
python3 -m signal_sim smoke --fixtures
```

| Command | Exit | Result |
|---|---:|---|
| `rails --fixtures` | 0 | `ok=True`. Live host / temp KILL / research mark / vendor mark all `refused`. |
| `smoke --fixtures` | 0 | `ok=True`. Rails + rank (12) + diagnose + intensity + drift (12 targets) + replay (8 fixture-mark orders) + 2 walk-forward folds + shadow. |

`params_sha256=f74f835690544b976e0ba67243caecdea26719001cbb33bc9ccdac5fc1a38ded`. Replay / walk-forward PnL remain **fixture-mark PnL**, not broker fills.

## Live intel (`feeds --live`)

Connectivity check. Counts and ticker histogram only. No headlines, person names, URLs, or raw payloads.

| Feed | Events | Tickers |
|---|---:|---|
| Quiver | 89 | AAPL 14, AMZN 7, CMCSA 3, CVX 3, DIS 3, GOOGL 12, META 7, MSFT 20, NFLX 2, NVDA 15, XOM 3 |
| World Monitor | 13 | XLE 13 |

Exit 0, `ok=True`. These counts are not a rank input and not a trading signal.

## Alpaca paper read (`paper-account`)

Read-only GETs on the paper host: `/v2/account`, `/v2/positions`, `/v2/clock`. Client mode `alpaca-paper-read`. No `submit` / `place_order` / `submit_order` methods. `order_post=disabled`.

| Check | Result |
|---|---|
| Exit | 0, `ok=True` |
| Base URL | `https://paper-api.alpaca.markets` |
| Account | `ACTIVE`, USD, not trading-blocked, not account-blocked; cash / equity / buying_power present |
| Positions | `n=0` |
| Clock | `is_open=false` at smoke time; next open / close present |
| `--dry-run` | payload validated in memory; `submitted=false`; fills stay on the local ledger |

No `/v2/orders` POST. Flag `0` was not violated. Cash / equity figures are omitted here on purpose.

## Documented integration tests

```bash
python3 -m unittest tests.test_live_feeds tests.test_alpaca_paper -v
```

19 tests, 0 failures, including the `skipUnless` live cases (`LiveIntelIntegrationTests`, `AlpacaPaperIntegrationTests`).

## What failed

Nothing in this pass.

## Next recommended step (still paper-only)

Keep `SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0`. Do not enable remote paper POSTs yet.

The paper account is empty and readable. The next product step is a **proposed rebalance dry-run**: read paper account + positions, compute the existing fixture / drift target book, and print intended tickets without posting. Only after that review should a later change add a paper-host POST behind the explicit submit flag. Fills today stay on the local ledger via `submit_paper_order`.

Do not point this client at a live Alpaca host. Do not treat fixture-mark PnL or live intel counts as alpha.
