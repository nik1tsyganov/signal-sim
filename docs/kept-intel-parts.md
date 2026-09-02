# Kept Intel Parts

**What we kept (behaviors, not files):**
- **TrendRadar**: Hotspot/breakout items, keyword/entity tags, local-first store of headlines, and source platform ID.
- **World Monitor**: Processed intel briefs (finance/energy/commodity flavor), entity list, and URL.

**What we discarded:**
- Vendoring their source code.
- TrendRadar's HTTP/MCP client (fixtures only for tonight).
- World Monitor's raw RSS dump and hosted MCP (fixtures only for tonight).

**License Context (Consume as services):**
- TrendRadar is GPL-3.0. We will interact via HTTP/MCP client later to avoid vendoring.
- World Monitor is AGPL-3.0. We will interact via hosted MCP later to avoid vendoring.
