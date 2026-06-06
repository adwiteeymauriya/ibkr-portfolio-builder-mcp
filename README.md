# ibkr-portfolio-builder-mcp

A remote MCP server for **top-down portfolio construction with Interactive Brokers**. Built to be used as a Claude.ai custom connector, Claude Code MCP, or any HTTP MCP client.

> *Most ibkr-mcp servers expose individual lookup primitives — `get_quote`, `get_position`, `place_order`. This one exposes the **research workflow**: a typed catalog of 468 screeners across 16 categories tagged by strategy intent (value / growth / income / momentum / quality / events / …), per-scan applicable instruments, inverse-pair links, and live news/account access — so an LLM can do real top-down portfolio construction (pick sectors / strategies → run scans → cross-reference with news → narrow to candidates) instead of bottoms-up ticker fishing.*

## Why this exists

I built this because the existing IBKR-MCP servers in the community treat IBKR as a "look-up-one-ticker" data source. That mirrors how most retail brokerage UIs work, but it's not how good portfolio construction actually happens.

A good top-down workflow looks like:

1. **Macro thesis** ("rates are about to fall, dividend payers should re-rate") →
2. **Strategy intent** ("show me income screens with quality bias on US large caps") →
3. **Screener composition** (run dividend yield + ROE + low debt screens; intersect) →
4. **Event/news context** ("any of these have earnings in the next two weeks? any negative analyst actions?") →
5. **Narrow candidate set** ("five tickers, ranked by my criteria, ready for deeper diligence").

To do that with an LLM, the MCP server needs to expose **the research vocabulary**, not just the raw API. That's the gap this server fills:

- **A typed scan catalog** — 468 IBKR scan codes across 16 categories (`Fundamentals`, `Price Movement`, `Dividends`, `Options & Volatility`, `Events & Earnings`, `52/26/13 Week High-Low`, `ESG`, `Bonds`, …) auto-tagged with **28 strategy intent tags** (`value`, `growth`, `quality`, `income`, `momentum_up`, `momentum_down`, `analyst`, `technical`, `gap`, `volatility`, `events`, `leverage`, `efficiency`, `risk_adjusted`, …). The LLM asks "what value scans exist for US stocks?" and gets a clean, filterable answer instead of trying to guess scan codes from training data.
- **Inverse pair links** — every `HIGH_X ↔ LOW_X` and `X_ASC ↔ X_DESC` pair is precomputed, so the LLM can flip polarity ("what's the opposite of LOW_PE_RATIO?") without guessing.
- **Per-scan instrument map** — the catalog knows which scan applies to `STK`, `ETF`, `OPT`, `BOND`, etc. The LLM stops sending Refinitiv scans to bond instruments and getting empty results.
- **Filter catalog** — separate typed map of the numeric filters (`priceAbove`, `peRatioBelow`, `divYieldAbove`, `growthRateAbove`, `avgVolumeAbove`, `marketCapAbove`, …) grouped by category, with per-instrument applicability notes.
- **News + screeners in one tool surface** — same connector, same auth, same conversation. The LLM can intersect a scan result with recent headlines or upcoming earnings without context-switching.
- **Two auth modes** — full OAuth 2.1 (DCR + PKCE + well-knowns) for Claude.ai custom connectors, plus a static bearer token for everything else. Same server, same tools.

It's still an early server. The IBKR API has plenty of restrictions on what a paper account can actually see (notably historical news entitlement). But the *catalog* and *workflow shape* are production-ready, and they're the load-bearing piece for an LLM-driven research loop.

## Quick facts

- **Transport:** Streamable HTTP at `/mcp`.
- **Auth:** OAuth 2.1 (PKCE + RFC 7591 Dynamic Client Registration) AND/OR static bearer token. Selectable via `AUTH_MODE`.
- **Persistence:** in-memory only today (sessions / DCR clients / OAuth tokens reset on container restart). Redis is on the roadmap — see below.
- **IBKR connection:** ib-gateway (`ghcr.io/gnzsnz/ib-gateway:stable`) runs as a sibling service in this compose; paper account in read-only API mode by default.
- **Built on:** [FastMCP](https://gofastmcp.com/) + [ib_async](https://github.com/ib-api-reloaded/ib_async).

## Tools

Parity with IBKR's official MCP for the **9 read-only tools** (skipping the two write/order-instruction tools — see roadmap), plus the 5 screener/news/catalog tools that are this server's reason for existing.

| Tool | What it does |
|---|---|
| `ib_account_summary` | NetLiquidation / BuyingPower / TotalCashValue / AvailableFunds / UnrealizedPnL for the connected paper or live account. |
| `ib_positions` | Open positions across managed accounts, with quantity / avg cost / mark-to-market / unrealized PnL. |
| `ib_open_orders` | Currently working orders with status, filled / remaining quantity, average fill price. |
| `ib_trades` | Recent executed fills (`days_back` window, IBKR caps history ~7 days). |
| `ib_price_snapshot` | Current bid / ask / last / high / low / volume for a US stock. Surfaces IBKR market-data restriction messages clearly. |
| `ib_price_history` | OHLCV bars for any duration / bar size (`1 day`, `1 hour`, `5 mins`, ...). Always works regardless of market-data subscription. |
| `ib_search_contracts` | Fuzzy-search IBKR's contract database by name / partial ticker. |
| `ib_contract_details` | Full contract metadata — `long_name`, `industry`, `category`, `subcategory`, trading hours, valid exchanges. **The hook for sector-aware top-down screening.** |
| `ib_scan_catalog` | The typed scan catalog. Filter by `category`, `strategy`, `instrument`, free-text `query`; optionally return the full list of available categories + strategies via `list_meta=true`. |
| `ib_filter_catalog` | Filter parameter codes grouped by category (`price`, `volume`, `market_cap`, `fundamentals`, `technical`, `options`), with an instrument applicability note. |
| `ib_screener_codes` | Substring search over the raw `scan-parameters.xml` codes (legacy / fallback). Useful for newer vendor codes not yet in the curated catalog. |
| `ib_screener` | Run an IBKR scan — pass `scan_code`, `instrument`, `location`, optional price / volume filters. Returns rank + ticker + exchange. |
| `ib_news_providers` | List subscribed IBKR news providers (Briefing.com, Dow Jones, etc.). |
| `ib_news_for_symbol` | Fetch headlines for a US stock across all subscribed providers. Headlines only; surfaces a clear `notice` field when the account lacks historical-news entitlement. |
| `ib_news_article` | Fetch a single article body by `provider_code` + `article_id`. ⚠️ may incur a per-article fee (Dow Jones in particular). |

> **Note on order placement.** IBKR's official MCP also exposes `Create Order Instruction` and `Delete Order Instruction`. This server intentionally **does not** — it runs ib-gateway in `READ_ONLY_API=yes` mode so even a misrouted tool call cannot place an order. Order execution belongs in a separate service with its own approval gate. See the roadmap for a possible "staged-only" instruction tool that would write to a local store without ever touching IBKR.

## Authentication

`AUTH_MODE` selects which mechanisms the server accepts. The default is `both`.

| Mode | What's accepted | Required env |
|---|---|---|
| `oauth` | OAuth-issued tokens only. Required for Claude.ai custom connectors. | `LOGIN_PASSWORD` |
| `bearer` | A static `Authorization: Bearer <token>` only. Skips the OAuth dance — best for CLI clients, Claude Code, your own scripts. | `STATIC_BEARER_TOKEN` |
| `both` *(default)* | Either OAuth tokens or the static bearer token. | At least one of `LOGIN_PASSWORD` or `STATIC_BEARER_TOKEN`. |

The OAuth endpoints (`/authorize`, `/token`, `/register`, `/.well-known/*`, `/login`) are always registered. In `bearer` mode they're inert — nothing in your README needs to point at them.

### Generating secrets

```bash
uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(48))"   # bearer token
uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(48))"   # session secret
```

### Using the static bearer token

```bash
curl -X POST https://YOUR.DOMAIN/mcp \
  -H "Authorization: Bearer $STATIC_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Connecting from Claude.ai (OAuth)

Settings → Connectors → **Add custom connector** → URL: `https://YOUR.DOMAIN/mcp` → leave OAuth client id/secret blank (Claude.ai uses DCR). When Claude.ai opens the OAuth flow, you'll be prompted for `LOGIN_PASSWORD`.

## Run

```bash
cp .env.example .env
# edit .env: set TWS_USERID / TWS_PASSWORD (paper account), LOGIN_PASSWORD, STATIC_BEARER_TOKEN, PUBLIC_BASE_URL
docker compose up -d
```

By default this pulls the published multi-arch image from GitHub Container Registry: `ghcr.io/adwiteeymauriya/ibkr-portfolio-builder-mcp:latest`. To build locally instead (e.g. when iterating on the source), run `docker compose build ibkr-mcp` first.

ib-gateway takes ~60–90 s to finish IBKR login after first start. Tail logs with `docker logs -f ibkr-mcp-gateway`.

For local-only use (bearer-token clients, no Claude.ai) this is enough — `http://localhost:8000/mcp` is now serving. Claude.ai custom connectors require an **HTTPS URL on the public internet**, so a reverse proxy with TLS in front is needed.

### Putting TLS in front for Claude.ai

Pick one. Both end with a working `https://your-host/mcp` and a valid cert.

**Option 1 — Cloudflare Tunnel (no public IP, no port forwarding).** Best if the server runs on a home network or a VM behind NAT. Cloudflare gives you a hostname and TLS for free; the tunnel daemon dials out from the server to Cloudflare's edge.

```bash
# One-time: install cloudflared, then
cloudflared tunnel login
cloudflared tunnel create ibkr-mcp
cloudflared tunnel route dns ibkr-mcp ibkr-mcp.your-domain.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: ibkr-mcp
credentials-file: /home/you/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: ibkr-mcp.your-domain.com
    service: http://localhost:8000
  - service: http_status:404
```

Run with `cloudflared tunnel run ibkr-mcp` (or install as a systemd unit via `cloudflared service install`). Then set `PUBLIC_BASE_URL=https://ibkr-mcp.your-domain.com` in `.env` and `docker compose restart ibkr-mcp`.

**Option 2 — Caddy reverse proxy with Let's Encrypt.** Best if the server has a public IP and ports 80/443 open. Caddy fetches certs automatically.

`Caddyfile`:

```
ibkr-mcp.your-domain.com {
    reverse_proxy localhost:8000
}
```

Run with `caddy run` (or install as a system service: `sudo caddy start` + a systemd unit). Same `.env` change as above.

In both cases `PUBLIC_BASE_URL` must match exactly the URL you give Claude.ai — Claude.ai validates the OAuth issuer against it.

## Example LLM prompts (top-down workflow)

```
1. Discovery:
   "List the strategies and categories available in ib_scan_catalog."

2. Strategy intent:
   "Find me value scans for US stocks. Show me their inverse codes too."

3. Composition:
   "Run LOW_PE_RATIO on STK.US.MAJOR with priceAbove $20 and avgVolumeAbove
    1,000,000, top 30. Cross-reference with HIGH_RETURN_ON_EQUITY top 30.
    Show me overlap."

4. Event context:
   "For the overlap list, check ib_news_for_symbol for any negative
    headlines in the last 7 days, and Events & Earnings scans for
    upcoming earnings within 14 days."

5. Narrow:
   "Rank the survivors by liquidity and tell me which two you'd dig
    into next."
```

## Roadmap

| Area | Item |
|---|---|
| Auth | Redis-backed sessions, DCR clients, OAuth tokens |
| Auth | Per-token scopes (`read-only`, `read-news`, `screener-only`, ...) |
| Instruments | Options (chains, greeks, IV/price calc) |
| Instruments | Futures (`ContFuture`, combos via `Bag`) |
| Instruments | Bonds (search + quote) |
| Instruments | Forex + crypto |
| Exchanges | Non-US equity routings (EU, HK, JP, AU) |
| Exchanges | Currency-aware `ib_account_summary` |
| Research | Reuters fundamentals (`reqFundamentalDataAsync`) |
| Research | Real-time streaming bars + tick-by-tick |
| Research | Level 2 order book |
| Research | Daily PnL streams (`pnlAsync`, `pnlSingleAsync`) |
| Research | Advisor sub-accounts (`reqFamilyCodesAsync`) |
| Research | Catalog → Memgraph for multi-hop queries |
| Research | Broader `ib_news_search` |
| Risk | `ib_what_if_order` (margin preview, no execution) |
| Risk | Local staged-instruction tools (no IBKR write) |

Open issues / PRs welcome on any of these.

## Layout

```
.
├── Dockerfile
├── docker-compose.yml             # connector + ib-gateway, internal IBKR network
├── pyproject.toml                 # uv-managed: fastmcp, itsdangerous, uvicorn, ib_async
├── uv.lock
├── .env.example
├── LICENSE                        # MIT
├── scan-parameters.xml            # IBKR's authoritative scan params (raw XML)
├── scanner_reference.json         # IBKR-categorized scanner reference
├── scanner_params.json            # Flat dump of scan codes + filters
├── scripts/
│   └── build_catalog.py           # Regenerates src/connector/data/* from the three source files above
└── src/
    └── connector/
        ├── settings.py            # env-driven config (incl. AUTH_MODE)
        ├── auth.py                # LoginGatedOAuthProvider + static bearer override + /login
        ├── ibkr.py                # ib_async connection helper (connect-per-call)
        ├── screener.py            # raw scan-parameters.xml substring search (fallback tool)
        ├── catalog.py             # typed scan + filter catalog loaders + filtering
        ├── tools.py               # 15 MCP tools wired into FastMCP
        ├── server.py              # FastMCP + Starlette wiring + uvicorn entry
        └── data/
            ├── scan_catalog.json  # generated
            └── filter_catalog.json # generated
```

## License

[MIT](./LICENSE).

## Acknowledgements

- [gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker) for the headless ib-gateway image.
- [ib_async](https://github.com/ib-api-reloaded/ib_async) for the async IBKR client.
- [FastMCP](https://gofastmcp.com/) for the MCP server framework with built-in OAuth support.

## Disclaimer

This software talks to your Interactive Brokers account. By default it runs against a **paper account in read-only API mode** — orders cannot be placed even if a tool tries. If you switch to a live account, you do so at your own risk. None of the tool output constitutes investment advice; the strategy tagging is a vocabulary helper, not a recommendation engine.
