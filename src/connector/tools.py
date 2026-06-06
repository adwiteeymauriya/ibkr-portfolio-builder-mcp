"""Tools exposed by the connector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP
from ib_async import ScannerSubscription, Stock

from connector.catalog import (
    list_categories,
    list_strategies,
    search_filters,
    search_scans,
)
from connector.ibkr import ib_session
from connector.screener import search_scan_codes


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool
    async def ib_account_summary() -> dict:
        """Return a summary of the connected Interactive Brokers account.

        Reads NetLiquidation, BuyingPower, TotalCashValue, AvailableFunds,
        and UnrealizedPnL from ib-gateway. Read-only.
        """
        wanted = {
            "NetLiquidation",
            "BuyingPower",
            "TotalCashValue",
            "AvailableFunds",
            "UnrealizedPnL",
        }
        async with ib_session() as ib:
            managed = ib.managedAccounts()
            account = managed[0] if managed else ""
            rows = await ib.accountSummaryAsync(account=account)
            values: dict[str, dict[str, str]] = {}
            for row in rows:
                if row.tag not in wanted:
                    continue
                values[row.tag] = {"value": row.value, "currency": row.currency}
        return {
            "account": account,
            "managed_accounts": list(managed),
            "summary": values,
        }

    @mcp.tool
    async def ib_news_providers() -> dict:
        """List IBKR news providers this account is subscribed to.

        Some providers (e.g. Briefing, Dow Jones, Reuters) require paid
        subscriptions; the free set varies per account. The provider ``code``
        values returned here are what ``ib_news_for_symbol`` accepts.
        """
        async with ib_session() as ib:
            providers = await ib.reqNewsProvidersAsync()
        return {
            "providers": [
                {"code": p.code, "name": p.name} for p in (providers or [])
            ],
        }

    @mcp.tool
    async def ib_news_for_symbol(
        symbol: str,
        count: int = 10,
        lookback_days: int = 7,
    ) -> dict:
        """Fetch recent news headlines for a US stock symbol.

        Resolves ``symbol`` to a USD stock contract (SMART routing), pulls up
        to ``count`` headlines across all subscribed providers from the last
        ``lookback_days`` days. Returns headline metadata only (no article
        body).

        Note: IBKR's ``reqHistoricalNews`` requires per-provider *historical*
        entitlement, which is separate from the live news subscriptions
        listed by ``ib_news_providers``. Paper accounts usually have the
        provider list populated but no historical entitlement, so this tool
        will return an empty list with a ``notice`` field on a paper
        connection. Switch the gateway to ``TRADING_MODE=live`` (and use a
        funded live account with the news subscriptions) to get headlines.
        """
        symbol = symbol.strip().upper()
        if not symbol:
            return {"symbol": symbol, "news": [], "error": "symbol is required"}
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, lookback_days))

        async with ib_session() as ib:
            providers = await ib.reqNewsProvidersAsync()
            provider_codes = "+".join(p.code for p in (providers or []))
            if not provider_codes:
                return {
                    "symbol": symbol,
                    "news": [],
                    "notice": "No news providers subscribed on this account.",
                }
            contract = Stock(symbol, "SMART", "USD")
            details = await ib.reqContractDetailsAsync(contract)
            if not details:
                return {
                    "symbol": symbol,
                    "news": [],
                    "error": f"No US stock contract found for {symbol!r}.",
                }
            con_id = details[0].contract.conId
            historical = await ib.reqHistoricalNewsAsync(
                conId=con_id,
                providerCodes=provider_codes,
                startDateTime=start,
                endDateTime=end,
                totalResults=max(1, min(count, 50)),
            )

        items: list[dict] = []
        for h in getattr(historical, "historical", []) or []:
            items.append(
                {
                    "time": str(h.time),
                    "provider_code": h.providerCode,
                    "article_id": h.articleId,
                    "headline": h.headline,
                }
            )
        result: dict = {
            "symbol": symbol,
            "con_id": con_id,
            "providers_queried": provider_codes,
            "news": items,
        }
        if not items:
            result["notice"] = (
                "0 headlines returned. Common cause: paper accounts have "
                "the news providers list populated but no historical-news "
                "entitlement. Connect a live account, or contact IBKR to "
                "enable historical news on this paper account."
            )
        return result

    @mcp.tool
    async def ib_news_article(provider_code: str, article_id: str) -> dict:
        """Fetch the body of a single news article.

        Args:
            provider_code: provider code from ``ib_news_for_symbol`` (e.g.
                ``"BRFG"``, ``"DJ-N"``).
            article_id: article ID from ``ib_news_for_symbol``.

        WARNING: Each call may incur a per-article fee charged by the
        provider (Dow Jones in particular). Use sparingly. Returns
        ``article_type`` (``0``=plain text, ``1``=HTML, ``2``=binary) and
        ``article_text``. If the account lacks entitlement for the
        provider, ``article_text`` is empty and a ``notice`` is included.
        """
        provider_code = provider_code.strip()
        article_id = article_id.strip()
        if not provider_code or not article_id:
            return {
                "error": "provider_code and article_id are both required",
            }
        async with ib_session() as ib:
            article = await ib.reqNewsArticleAsync(provider_code, article_id)
        text = getattr(article, "articleText", "") or ""
        article_type = getattr(article, "articleType", None)
        result = {
            "provider_code": provider_code,
            "article_id": article_id,
            "article_type": article_type,
            "article_text": text,
        }
        if not text:
            result["notice"] = (
                "Empty article body. Likely causes: no entitlement for this "
                "provider on this account, invalid article_id, or the "
                "article has been purged from the provider's archive."
            )
        return result

    @mcp.tool
    def ib_screener_codes(
        query: str | None = None,
        instrument: str | None = None,
        limit: int = 30,
    ) -> dict:
        """Substring search across raw IBKR scan codes (legacy / fallback).

        Use ``ib_scan_catalog`` for the typed catalog with categories and
        strategy tags. This tool still reads ``scan-parameters.xml``
        directly and is useful when you need a code that's not in the
        curated catalog (newer SCAN_* vendor codes, etc.).
        """
        return {
            "matches": search_scan_codes(query=query, instrument=instrument, limit=limit),
        }

    @mcp.tool
    def ib_scan_catalog(
        category: str | None = None,
        strategy: str | None = None,
        instrument: str | None = None,
        query: str | None = None,
        limit: int = 50,
        list_meta: bool = False,
    ) -> dict:
        """Typed catalog of IBKR scan codes.

        Each entry carries: ``category`` (IBKR taxonomy), ``strategy_tags``
        (intent — value, growth, income, momentum_up, etc.), ``vendor``,
        ``inverse_of`` (paired ASC/DESC or HIGH/LOW code), and the
        instruments the scan applies to. AND-combines the filters.

        Args:
            category: exact category (e.g. ``"Fundamentals"``,
                ``"Price Movement"``).
            strategy: exact strategy tag (e.g. ``"value"``, ``"growth"``,
                ``"income"``).
            instrument: substring against machine instruments
                (``"STK"`` matches ``"STK.US.MAJOR"``).
            query: substring against code/name/description.
            limit: max rows.
            list_meta: if True, also return the list of all categories and
                strategy tags so the LLM can discover what's available.
        """
        result: dict = {
            "matches": search_scans(
                category=category,
                strategy=strategy,
                instrument=instrument,
                query=query,
                limit=limit,
            ),
        }
        if list_meta:
            result["categories"] = list_categories()
            result["strategies"] = list_strategies()
        return result

    @mcp.tool
    def ib_filter_catalog(
        category: str | None = None,
        instrument: str | None = None,
    ) -> dict:
        """Look up IBKR scanner filter parameters.

        Filters narrow scan results (e.g. ``priceAbove``, ``marketCapAbove``,
        ``avgVolumeAbove``). The catalog groups filters by category
        (``price``, ``volume``, ``market_cap``, ``fundamentals``,
        ``technical``, ``options``) and by applicable instrument.

        Args:
            category: filter category to list.
            instrument: instrument code (e.g. ``"STK"``) — restricts the
                returned filters to those allowed by that instrument.
        """
        return search_filters(category=category, instrument=instrument)

    @mcp.tool
    async def ib_screener(
        scan_code: str,
        instrument: str = "STK",
        location: str = "STK.US.MAJOR",
        limit: int = 20,
        above_price: float | None = None,
        below_price: float | None = None,
        above_volume: int | None = None,
    ) -> dict:
        """Run an IBKR market screener.

        Args:
            scan_code: a valid IBKR scanCode (e.g. ``"TOP_PERC_GAIN"``,
                ``"MOST_ACTIVE"``, ``"HOT_BY_VOLUME"``). Discover them via
                ``ib_screener_codes``.
            instrument: instrument set (default ``"STK"``).
            location: location code (default ``"STK.US.MAJOR"``).
            limit: number of rows to request (max 50).
            above_price / below_price / above_volume: optional numeric
                filters applied by the gateway.
        """
        sub = ScannerSubscription(
            numberOfRows=max(1, min(limit, 50)),
            instrument=instrument,
            locationCode=location,
            scanCode=scan_code,
        )
        if above_price is not None:
            sub.abovePrice = above_price
        if below_price is not None:
            sub.belowPrice = below_price
        if above_volume is not None:
            sub.aboveVolume = above_volume

        async with ib_session() as ib:
            rows = await ib.reqScannerDataAsync(sub)

        results: list[dict] = []
        for row in rows or []:
            cd = row.contractDetails
            contract = getattr(cd, "contract", None)
            results.append(
                {
                    "rank": row.rank,
                    "symbol": getattr(contract, "symbol", None),
                    "sec_type": getattr(contract, "secType", None),
                    "currency": getattr(contract, "currency", None),
                    "exchange": getattr(contract, "primaryExchange", None)
                    or getattr(contract, "exchange", None),
                    "con_id": getattr(contract, "conId", None),
                }
            )
        return {
            "scan_code": scan_code,
            "instrument": instrument,
            "location": location,
            "results": results,
        }

    # ---------------------------------------------------------------------
    # Read-only account + market-data tools (parity with IBKR's official MCP)
    # ---------------------------------------------------------------------

    def _resolve_stock(symbol: str) -> Stock:
        return Stock(symbol.strip().upper(), "SMART", "USD")

    @mcp.tool
    async def ib_positions() -> dict:
        """List open positions across all managed accounts.

        Returns one entry per (account, contract) pair with current quantity,
        average cost, mark-to-market value, and unrealized PnL.
        """
        async with ib_session() as ib:
            await ib.reqPositionsAsync()
            items: list[dict] = []
            # portfolio() returns PortfolioItem (with marketPrice/Value/PnL)
            # per managed account; merge it with positions() for accounts
            # that haven't surfaced in portfolio yet.
            seen_keys: set[tuple] = set()
            for acct in ib.managedAccounts():
                for p in ib.portfolio(account=acct):
                    c = p.contract
                    key = (acct, c.conId)
                    seen_keys.add(key)
                    items.append(
                        {
                            "account": acct,
                            "symbol": c.symbol,
                            "sec_type": c.secType,
                            "currency": c.currency,
                            "exchange": c.primaryExchange or c.exchange,
                            "con_id": c.conId,
                            "position": p.position,
                            "avg_cost": p.averageCost,
                            "market_price": p.marketPrice,
                            "market_value": p.marketValue,
                            "unrealized_pnl": p.unrealizedPNL,
                            "realized_pnl": p.realizedPNL,
                        }
                    )
            for p in ib.positions():
                c = p.contract
                key = (p.account, c.conId)
                if key in seen_keys:
                    continue
                items.append(
                    {
                        "account": p.account,
                        "symbol": c.symbol,
                        "sec_type": c.secType,
                        "currency": c.currency,
                        "exchange": c.primaryExchange or c.exchange,
                        "con_id": c.conId,
                        "position": p.position,
                        "avg_cost": p.avgCost,
                    }
                )
        return {"positions": items}

    @mcp.tool
    async def ib_open_orders() -> dict:
        """List currently open and pending orders.

        Returns one entry per open Trade with order metadata + live status.
        Empty list if no orders are working.
        """
        async with ib_session() as ib:
            trades = await ib.reqOpenOrdersAsync()
        items: list[dict] = []
        for t in trades or []:
            o = t.order
            c = t.contract
            s = t.orderStatus
            items.append(
                {
                    "order_id": o.orderId,
                    "perm_id": o.permId,
                    "client_id": o.clientId,
                    "action": o.action,
                    "order_type": o.orderType,
                    "total_quantity": o.totalQuantity,
                    "lmt_price": o.lmtPrice if o.lmtPrice else None,
                    "aux_price": o.auxPrice if o.auxPrice else None,
                    "tif": o.tif,
                    "status": s.status,
                    "filled": s.filled,
                    "remaining": s.remaining,
                    "avg_fill_price": s.avgFillPrice,
                    "symbol": c.symbol,
                    "sec_type": c.secType,
                    "exchange": c.primaryExchange or c.exchange,
                }
            )
        return {"orders": items}

    @mcp.tool
    async def ib_trades(days_back: int = 7) -> dict:
        """Recent executed trades / fills.

        Pulls executions from the last ``days_back`` days. IBKR's execution
        history is typically capped at ~7 days on the API, longer windows
        may return the same recent set.
        """
        from ib_async import ExecutionFilter

        days_back = max(1, min(days_back, 30))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        ef = ExecutionFilter(time=cutoff.strftime("%Y%m%d %H:%M:%S"))
        async with ib_session() as ib:
            fills = await ib.reqExecutionsAsync(ef)
        items: list[dict] = []
        for f in fills or []:
            ex = f.execution
            c = f.contract
            comm = f.commissionReport
            items.append(
                {
                    "time": str(f.time),
                    "symbol": c.symbol,
                    "sec_type": c.secType,
                    "exchange": ex.exchange,
                    "action": ex.side,  # "BOT" / "SLD"
                    "shares": ex.shares,
                    "price": ex.price,
                    "commission": getattr(comm, "commission", None),
                    "currency": getattr(comm, "currency", None),
                    "account": ex.acctNumber,
                    "exec_id": ex.execId,
                    "order_id": ex.orderId,
                }
            )
        return {"trades": items}

    @mcp.tool
    async def ib_price_snapshot(symbol: str, sec_type: str = "STK") -> dict:
        """Get a current price snapshot for a contract.

        Returns bid / ask / last with market-data type. Paper accounts
        without a market-data subscription get 15-minute delayed data;
        the ``market_data_type`` field tells you which (1=live, 2=frozen,
        3=delayed, 4=delayed-frozen).
        """
        symbol = symbol.strip().upper()
        if not symbol:
            return {"error": "symbol is required"}
        if sec_type.upper() != "STK":
            return {
                "error": f"sec_type={sec_type!r} not yet supported; only STK in v1.",
            }
        errors: list[str] = []

        def _capture(reqId, errorCode, errorString, contract=None):
            # 2104/2106/2158 are connection-OK status messages; ignore them.
            if errorCode in (2104, 2106, 2158, 2150, 2168, 2169, 10167):
                return
            errors.append(f"{errorCode}: {errorString}")

        async with ib_session() as ib:
            ib.errorEvent += _capture
            contract = _resolve_stock(symbol)
            await ib.qualifyContractsAsync(contract)
            # Paper accounts lack live US equity data entitlement; fall back
            # to delayed data (15 min, marketDataType=3) so values populate.
            ib.reqMarketDataType(3)
            tickers = await ib.reqTickersAsync(contract)
        if not tickers:
            result: dict = {"symbol": symbol, "error": "no ticker returned"}
            if errors:
                result["ibkr_errors"] = errors
            return result
        t = tickers[0]
        result: dict = {
            "symbol": symbol,
            "time": str(t.time) if t.time else None,
            "market_data_type": t.marketDataType,
            "bid": t.bid,
            "bid_size": t.bidSize,
            "ask": t.ask,
            "ask_size": t.askSize,
            "last": t.last,
            "last_size": t.lastSize,
            "high": t.high,
            "low": t.low,
            "close": t.close,
            "volume": t.volume,
        }
        if errors:
            result["ibkr_errors"] = errors
            result["notice"] = (
                "Empty fields are usually caused by IBKR market-data "
                "restrictions. Common case: 'No market data during "
                "competing live session' means the same IBKR account is "
                "logged in elsewhere (TWS desktop, mobile app) and IBKR "
                "drops market data on the gateway. Log out of the other "
                "session, then restart ib-gateway. Paper accounts also "
                "lack live US-equity entitlement; use ib_price_history "
                "instead for daily bars (which always works)."
            )
        return result

    @mcp.tool
    async def ib_price_history(
        symbol: str,
        duration: str = "30 D",
        bar_size: str = "1 day",
        sec_type: str = "STK",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> dict:
        """Historical OHLCV bars for a contract.

        Args:
            symbol: ticker (e.g. ``"AAPL"``).
            duration: IBKR duration string -- ``"30 D"``, ``"6 M"``, ``"1 Y"``,
                ``"5 Y"`` etc. Combined with bar_size to bound the request.
            bar_size: ``"1 day"`` (default), ``"1 hour"``, ``"15 mins"``,
                ``"5 mins"``, ``"1 min"``, etc.
            sec_type: only ``"STK"`` supported in v1.
            what_to_show: ``"TRADES"`` (default), ``"MIDPOINT"``, ``"BID"``,
                ``"ASK"``.
            use_rth: True restricts to regular trading hours.
        """
        symbol = symbol.strip().upper()
        if sec_type.upper() != "STK":
            return {"error": f"sec_type={sec_type!r} not yet supported; only STK in v1."}
        async with ib_session() as ib:
            contract = _resolve_stock(symbol)
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
            )
        rows: list[dict] = []
        for b in bars or []:
            rows.append(
                {
                    "date": str(b.date),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
            )
        return {
            "symbol": symbol,
            "duration": duration,
            "bar_size": bar_size,
            "bars": rows,
        }

    @mcp.tool
    async def ib_search_contracts(pattern: str, limit: int = 20) -> dict:
        """Fuzzy-search IBKR's contract database by symbol or name fragment.

        Use this when the LLM has a company name or partial ticker and needs
        to resolve it to one or more concrete contracts before calling
        per-contract tools.
        """
        pattern = pattern.strip()
        if not pattern:
            return {"matches": [], "error": "pattern is required"}
        async with ib_session() as ib:
            results = await ib.reqMatchingSymbolsAsync(pattern)
        items: list[dict] = []
        for r in (results or [])[:limit]:
            c = r.contract
            items.append(
                {
                    "symbol": c.symbol,
                    "sec_type": c.secType,
                    "currency": c.currency,
                    "primary_exchange": c.primaryExchange,
                    "con_id": c.conId,
                    "derivative_sec_types": list(r.derivativeSecTypes or []),
                }
            )
        return {"matches": items}

    @mcp.tool
    async def ib_contract_details(symbol: str, sec_type: str = "STK") -> dict:
        """Full contract metadata: long name, industry, category, subcategory,
        trading hours, valid exchanges. Use to look up the sector/industry of
        a ticker for top-down sector filtering.
        """
        symbol = symbol.strip().upper()
        if sec_type.upper() != "STK":
            return {"error": f"sec_type={sec_type!r} not yet supported; only STK in v1."}
        async with ib_session() as ib:
            contract = _resolve_stock(symbol)
            details = await ib.reqContractDetailsAsync(contract)
        if not details:
            return {"symbol": symbol, "error": "no contract found"}
        d = details[0]
        c = d.contract
        return {
            "symbol": symbol,
            "long_name": d.longName,
            "industry": d.industry,
            "category": d.category,
            "subcategory": d.subcategory,
            "market_name": d.marketName,
            "time_zone_id": d.timeZoneId,
            "trading_hours": d.tradingHours,
            "liquid_hours": d.liquidHours,
            "valid_exchanges": d.validExchanges,
            "primary_exchange": c.primaryExchange,
            "currency": c.currency,
            "con_id": c.conId,
            "min_tick": d.minTick,
            "order_types": d.orderTypes,
        }
