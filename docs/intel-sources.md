# Intel Sources Analysis: TrendRadar & World Monitor

## 1. What Each Product Emits
- **TrendRadar**: Emits MCP tools (read_article, search_news, etc.), RSS feeds, and push notifications to tools like Slack and Telegram (`trendradar-utf8.md:14-36`). It has a heavy China-platform mix, monitoring platforms like Zhihu, Bilibili, Wall Street CN, and Cailianshe (`trendradar-utf8.md:997-1003`). English language support exists, but source material heavily leans Chinese.
- **World Monitor**: Emits MCP tools via streamable HTTP (`worldmonitor.app/mcp`) and a REST API (`api.worldmonitor.app`). It offers curated news feeds, global/regional data, and a finance radar (`worldmonitor-utf8.md:53-58`).

## 2. License & Contamination
- **TrendRadar (GPL-3.0)** and **World Monitor (AGPL-3.0)**.
- **Risk**: Copying their source code directly into the trading app would trigger copyleft contamination, forcing the trading app to become open-source under GPL/AGPL terms.
- **Recommendation**: Client-only consumption. Consuming them via their MCP interfaces or REST APIs as separate processes over HTTP/IPC prevents contamination and maintains isolation.

## 3. Mapping Intel to Tradable Tickers
- **World Monitor**: Could map to US tech, XLE/USO, and media ETFs via its finance radar and curated news. However, this **cannot be done without a paid key**. The `tools/call` endpoint explicitly requires a `X-WorldMonitor-Key` header (`worldmonitor-utf8.md:127-128`), and running it locally requires upstream credential keys (`worldmonitor-utf8.md:92`).
- **TrendRadar**: Has Wall Street CN (华尔街见闻) coverage which mentions US tech, but the data is largely Chinese text. A downstream NER/translation layer is strictly required to map Chinese entity names (e.g., "苹果" -> AAPL) to US tickers reliably.

## 4. Combination Contract
A downstream signal schema normalizing both would need:
- `timestamp`: (time of event/news)
- `entities`: (array of mentioned companies/tickers)
- `confidence`: (source credibility or AI-derived certainty)
- `source`: (e.g., World Monitor, TrendRadar - Zhihu)
- `url`: (original article reference)

## 5. What could not be verified
- [UNVERIFIED] Whether World Monitor's API returns specific ticker-level data for US media ETFs. (Requires paid key to test live `tools/call`).
- [UNVERIFIED] TrendRadar's latency for breaking US tech news on Chinese platforms vs native English sources.

## Correct Negative Findings
**REFUTED**: We cannot feed tradable US names reliably using these tools out-of-the-box without spending money or risking copyleft. World Monitor requires a paid API key for `tools/call` (`worldmonitor-utf8.md:127-128`). TrendRadar avoids the key issue but requires complex translation/NER to map its Chinese-heavy feed to US tickers. Copying either source directly would contaminate the repo with GPL-3.0/AGPL-3.0.

---
### WRITE AUDIT
Git is not yet initialized. File list:
- C:\src\signal-sim\docs\intel-sources.md
