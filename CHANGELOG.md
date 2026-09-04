# Changelog

What landed in the paper operate loop. This is not a live trading log. Every PnL number the loop prints is **fixture-mark PnL**, not alpha.

## Unreleased — PR 1

Paper-only local loop on a frozen universe and checked-in fixture marks.

- **Install:** `pip install -e .` from a repo checkout. Stdlib only. No Yahoo, Stooq, or broker SDKs.
- **Operate:** `rank`, `diagnose`, `intensity`, `drift`, `replay`, `walkforward`, `shadow`, `rails`, and `smoke` all require `--fixtures`. The desk serves the same loop on loopback only.
- **Fills:** `submit_paper_order` is the only order path. Fills must be `kind=fixture_mark` and `source=fixture`. A research or vendor mark refuses. A live Alpaca host or IBKR live ports raise and do not open a socket. A present `KILL` file refuses the order.
- **Rails:** `rails --fixtures` and `GET /api/rails` assert those refusals locally. `smoke --fixtures` runs rails first, then the rest of the frozen-params pass. CI runs both. No secrets.
- **Params:** `fixtures/params.json` is the single source. Locked policy: `cost_bps`, `decision_delay_hours`, `starting_cash`, `max_drawdown`, `max_gross_frac`, `max_name_frac`. `size_frac` stays book-level. Do not retune to move fixture-mark PnL.
- **Blocked on the owner:** Alpaca paper account and keys, paid Quiver / World Monitor key, honest vendor bars. Jump-diffusion stays documentation-only.

See [operate readiness](docs/operate-readiness.md) for how to run it and what must never be claimed.
