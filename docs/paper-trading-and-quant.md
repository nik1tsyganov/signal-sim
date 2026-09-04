# Paper trading and news-intensity quant research

**Decision date:** 2026-09-02  
**Scope:** paper money only; US-listed cash equities and ETFs; Windows development.  
**Decision:** use a local simulated ledger now. Use Alpaca paper as the first later broker adapter.

## Executive decision

No reviewed paper broker works without an owner-created account. Alpaca needs an email signup and paper keys. IBKR needs an approved, funded live account before paper access. Massive also needs signup for its free data key. We will not create any of these accounts tonight.

The honest v0 is a local ledger. It records orders, fills, cash, positions, prices, and model decisions. Its fill policy must be deterministic and conservative. It is a research simulator, not evidence of broker execution quality.

For a later web application, Alpaca is the primary broker for US-listed stocks and ETFs. It has a direct HTTP and WebSocket surface, separate paper credentials, and an official Python SDK. IBKR becomes the better candidate only if the scope expands to crude-oil or natural-gas futures, options breadth, bonds, or global products.

The phrase “TWS/Gateway port 7497” is not fully correct. IBKR documents port `7497` for paper TWS. It documents port `4002` for paper IB Gateway. [IBKR connection parameters](https://www.interactivebrokers.com/docs/excel/rtd/connection-parameters)

## Execution choices

| Choice | Can run tonight without a new account? | Windows path | Main limits | Verdict |
|---|---:|---|---|---|
| Alpaca paper | No | HTTPS and WebSockets at `paper-api.alpaca.markets`; official `alpaca-py` client | An email signup and paper keys are required. A paper-only account receives IEX data. Simulated fills omit some live effects. | **Primary later web-app adapter** for cash US equities and ETFs. |
| IBKR paper | No | TWS socket on `127.0.0.1:7497`; Gateway paper uses `4002` | IBKR requires an approved, funded regular account. TWS or Gateway must run. Market-data permissions follow the live account. | **Later specialist adapter** for broader asset coverage. |
| Local simulated ledger | Yes | Python standard library plus SQLite; no service or account | It cannot reproduce queue position, hidden liquidity, broker rejects, or venue-specific fills. | **Honest v0** for research and forward paper decisions. |

Alpaca says its paper environment is free, uses the same API shape as live trading, and gives paper-only users IEX data. These are vendor self-claims. Alpaca also lists omitted effects such as market impact, latency slippage, queue position, and dividends. [Alpaca paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading) Its authentication page confirms the separate paper keys and `https://paper-api.alpaca.markets` endpoint. [Alpaca authentication](https://docs.alpaca.markets/us/v1.1/docs/authentication-1) The official SDK exposes trading, data, and streaming clients. [Alpaca-py](https://alpaca.markets/sdks/python/)

IBKR says a regular account must be approved and funded before paper trading becomes available. This is a vendor self-claim and an account gate. [IBKR paper-trading limitations](https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading) IBKR also states that individual Web API use needs a fully open, funded IBKR Pro account. [IBKR Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)

### Recommended Windows shape

Keep one process boundary between signals and execution. The signal process emits an immutable decision record. An execution adapter consumes that record and targets exactly one environment.

Use this order of components:

1. A news intake stores the source timestamp, first-seen timestamp, text hash, entities, topic, and raw payload reference.
2. A market-data intake stores provider timestamps and the local receipt time.
3. An online feature step builds only features available at the decision time.
4. A strategy emits a signed target and a maximum holding horizon.
5. A local ledger applies a declared fill rule and records the fill assumption.
6. A later Alpaca adapter maps the same decision record to paper orders only.

Use a hard environment allowlist. The first adapter must accept only `local`. The later adapter must accept only Alpaca’s paper host. Do not add a live host as a dormant option.

## Market data with no payment tonight

“Free” does not mean “ready tonight.” It can still require signup, credentials, or restrictive use terms.

| Source | Verified state on 2026-09-02 | Point-in-time use | Decision |
|---|---|---|---|
| Yahoo Finance through `yfinance` | `yfinance` is an independent project, not a Yahoo product. Its own notice limits the intended use to research, education, and personal use. | Useful for exploratory daily bars. It is not a contractual production feed. Cache each raw response and its receipt time. | **Use only as a temporary research input.** Do not use it as the execution mark. |
| Stooq | The official symbol pages expose current and historical views. The automated check met a JavaScript verification page on the history/download routes. No stable API or reuse license was verified in this pass. | Suitable as a second daily-bar comparison after a manual terms check. Adjustments and corrections can change later downloads. | **Low confidence.** Do not make it the only source. |
| Massive, formerly Polygon.io | Vendor pricing lists Stocks Basic at `$0/month`, five calls per minute, two years of history, and end-of-day data. Signup is required. | The free plan is useful for daily or minute research after signup. It is not the no-account path and is not the free real-time execution feed. | **Evaluate later. Not available tonight.** |
| Alpaca paper market data | A paper-only account receives IEX data, per Alpaca. Keys and an account are required. | It can align the later paper broker and price feed. IEX is not the full consolidated US market. | **Use with Alpaca paper later.** |
| IBKR data | Paper permissions mirror the live account. Free non-consolidated or delayed data may be available, while many real-time subscriptions cost money. | It is useful only after the owner chooses the IBKR account path. | **Do not use tonight.** |

Sources: [yfinance notice](https://github.com/ranaroussi/yfinance/blob/main/README.md), [Stooq sample US symbol page](https://stooq.com/q/?s=nvda.us), [Massive stock pricing](https://massive.com/pricing?product=stocks), [IBKR market-data pricing](https://portal.interactivebrokers.com/en/pricing/market-data-pricing.php?menu=A).

### Point-in-time rules

Store four times for each news item: `published_at`, `first_seen_at`, `processed_at`, and `decision_at`. Use `first_seen_at` as the earliest usable time unless a replay proves an earlier arrival.

Store the raw source version. Do not let a later article edit replace the version seen by the model. Build clusters online. A cluster cannot gain members that arrived after the simulated decision.

Freeze the symbol universe before each evaluation window. A current list of liquid names must not be projected backward. For a small fixed basket, start evaluation only after the basket was frozen.

Daily Yahoo or Stooq data can test slow hypotheses. It cannot validate a five-minute news response. Intraday news methods need an intraday market feed with timestamps that share a checked clock convention.

## Candidate methods

The common news feature vector can contain:

`count_5m`, `count_30m`, source-weighted count, signed sentiment, novelty, cluster size, cluster age, entity confidence, topic, scheduled-event flag, surprise, source diversity, market-wide intensity, sector intensity, and the first price response observed before the decision.

All counts must use event time available to the system. All rolling values must end before `decision_at`.

### 1. Marked multivariate Hawkes process

A Hawkes process models event arrivals whose recent events raise later arrival intensity. Use one process per symbol-topic or a pooled sector model. Treat a news item as an exogenous immigrant. Its mark controls excitation by sentiment, novelty, source weight, and entity confidence. The model output is a conditional event or jump hazard, not a moving average. Hawkes models have a long finance literature, and published work has added exogenous macro-news components. [Hawkes processes in finance](https://arxiv.org/abs/1502.04592) [Rambaldi, Pennesi, and Lillo](https://arxiv.org/abs/1405.6047)

**Windows verdict:** implementable. Fit an exponential-kernel model with ordinary Python numerical tools. Start with a pooled, low-dimensional model. A separate model per symbol will be data-starved.

**Required features:** exact arrival times, online cluster identity, symbol and sector links, signed sentiment, novelty, source weight, scheduled flag, and market or sector background intensity.

### 2. News-conditioned jump diffusion

Model returns with a continuous diffusion plus discrete jumps. Make jump intensity a positive function of the current news vector. Model jump sign and size separately from jump arrival. This structure matches evidence that news frequency and content relate to both return jumps and time-varying jump intensity. [News as sources of jumps in stock returns](https://www.sciencedirect.com/science/article/pii/S0304405X21003470)

**Windows verdict:** implementable, but calibration is harder than Hawkes or drift regression. Use it after the event store and intraday bars are stable. Begin with discrete-time likelihood or particle filtering. Do not start with a large neural intensity model.

**Required features:** the common news vector, pre-event realized variance, spread or liquidity proxy, sector return, market return, scheduled surprise, and the observed first price response.

### 3. Online news-cluster drift model

Maintain a latent information state for each symbol. Each new cluster updates the state. The state decays with a learned half-life and predicts returns over fixed horizons. Estimate separate effects for positive and negative news, topic, novelty, and source diversity. Published results report short-horizon continuation or underreaction after firm news, but those findings do not prove that this specific basket will earn money. [Pervasive underreaction](https://www.sciencedirect.com/science/article/pii/S0304405X21001306) A Federal Reserve working paper reports that daily news prediction can be concentrated in the next one or two days, with different positive and negative response speeds. [Heston and Sinha](https://www.federalreserve.gov/econres/feds/news-versus-sentiment-predicting-stock-returns-from-news-stories.htm)

**Windows verdict:** best first directional model. It is simple to audit, supports regularization, and produces clear feature ablations. It is event-driven but not naive technical analysis.

**Required features:** online cluster count, signed tone, topic, novelty, source diversity, entity confidence, cluster age, first-seen time, first price response, market return, and sector return.

**Lookahead ban:** never train or score with the final article text, final cluster membership, or a vendor timestamp learned after the simulated print. The row must contain only the version and cluster state known at `decision_at`.

### 4. News-driven stochastic volatility

Use a latent log-variance process. Let news intensity, sentiment magnitude, novelty, and scheduled-event surprise enter the variance transition. This method uses intel to forecast risk and holding limits. It must not be added only to make the stack appear sophisticated. Evidence that news flow and content relate to jump intensity and volatility supports this role. [News as sources of jumps in stock returns](https://www.sciencedirect.com/science/article/pii/S0304405X21003470)

**Windows verdict:** optional risk overlay. Add it only after the first three models show stable timestamp hygiene. Do not use it as the first directional signal.

**Required features:** absolute sentiment, news counts by horizon, novelty, source diversity, scheduled flag, surprise, market-wide intensity, and lagged realized variance.

## Evaluation: functional or not

Define the prediction before fitting. Each model must state its target, horizon, retrain schedule, decision delay, and order rule.

Use expanding or rolling walk-forward evaluation. Fit only on the past. Select parameters inside the training window. Advance to the next untouched window and do not revisit it.

Purge samples whose return-label intervals overlap a test interval. Add an embargo after each test interval. The embargo must cover the label horizon and the measured news-processing delay. This prevents a training row from using returns or cluster information that belong to the test period.

The checked-in fixture harness is `walkforward --fixtures`: two expanding `decision_at` windows, a purge/embargo that covers each fold's label horizon plus `decision_delay_hours`, and per-fold fixture-mark PnL. It does not search parameters or combine folds into a fitted score. A later print in an earlier fold fails closed. Jump-diffusion stays documentation-only until honest intraday bars exist.

The data gate is strict:

- Require `first_seen_at <= processed_at <= decision_at < fill_at`.
- Reject any feature whose source version was unavailable at `decision_at`.
- Rebuild clusters as an online replay. Do not load final clusters.
- Keep train, validation, and test windows in time order.
- Include delisted or renamed names when the evaluated universe requires them.

Use model-specific proof:

- Hawkes: out-of-sample event log likelihood, time-rescaling diagnostics, and calibration by predicted-intensity bucket.
- Jump diffusion: jump-probability Brier score, jump-size likelihood, tail calibration, and net paper performance.
- Drift model: horizon return error, rank information coefficient, calibration by score bucket, and net paper performance.
- Volatility model: variance forecast loss, interval coverage, and breach counts.

Each model needs three comparisons: a no-news version, a shuffled-news placebo that preserves intraday seasonality, and a news-feature ablation. The full model is functional only if it improves its proper forecast score out of sample and retains value after conservative costs.

The trading gate is stricter than the forecast gate. Report net return, turnover, worst drawdown, exposure, hit rate, and tail loss. Use bid-ask costs, a decision delay, and conservative fills. Require the result to persist across several walk-forward folds and more than one sector. Then run a forward-only paper shadow period with frozen code and parameters.

Track every tried specification. Many searches can produce a lucky backtest. Bailey and coauthors show why standard holdouts can be unreliable after strategy selection and propose a probability-of-backtest-overfitting framework. [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

Declare a scheme **not functional** if it fails calibration, loses its advantage after the news ablation, depends on one fold or symbol, or fails after costs. A correct negative ends that branch.

## Recommended sequence

1. Build only the local ledger and timestamp contract.
2. Freeze a small liquid cash-equity and ETF universe across technology, energy, and media.
3. Replay daily data first to verify data hygiene. Do not claim intraday value from daily data.
4. Fit the online drift model as the first directional baseline.
5. Fit a pooled marked Hawkes model as the event-intensity layer.
6. Add news-conditioned jump diffusion only after intraday data passes timestamp checks.
7. Connect Alpaca paper after the owner creates the account and keys.
8. Consider IBKR only when futures or broader instruments become a real requirement.

## Research method and confidence

The search used official Alpaca, IBKR, Massive, Stooq, and project documentation. It also used arXiv, the Federal Reserve, and journal landing pages. Searches ran on 2026-09-02.

Search strings included `Alpaca paper trading paper-api`, `IBKR paper port 7497 funded account`, `Massive stocks free pricing`, `Stooq CSV historical terms`, `Hawkes news intensity finance`, `news jump intensity stock returns`, and `walk-forward embargo lookahead finance`.

The search interface did not supply total result counts. The pass opened the directly relevant primary or first-party pages. Vendor feature and price statements remain labeled as vendor self-claims.

The journal search index returned both cited abstracts, but direct ScienceDirect fetches returned HTTP 403. Stooq pages returned a JavaScript verification page. These access limits do not affect the broker decision, but Stooq remains low confidence and cannot carry a load-bearing decision.

The load-bearing broker conclusion has two first-party sources: Alpaca requires signup and paper keys, while IBKR requires an approved, funded regular account. The local ledger follows directly from the no-account constraint. The method recommendation uses several independent papers, but profitability for this universe remains unverified until walk-forward and paper evidence exists.
