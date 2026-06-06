"""Thin wrapper around ib_async for use inside MCP tools.

Strategy: connect per tool call with a *unique* client id per session. This
keeps the tool code straightforward and avoids the ib-gateway behaviour
where a fresh connection using the same client id as a not-yet-fully-closed
previous one is silently refused (the connect hangs until timeout).

We start at ``IB_CLIENT_ID`` and increment atomically per session. ib-gateway
accepts dozens of distinct client ids without issue.

Anti-pattern guard: ``ib_async`` requires its own asyncio event loop and may
clash with the Starlette/uvicorn loop. We follow the upstream recommendation
of using ``util.patchAsyncio()`` at import time so the running loop is
re-entrant for short blocking calls inside tools.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from contextlib import asynccontextmanager

from ib_async import IB, util

# ib_async needs nest_asyncio under FastAPI/Starlette — see ib_async/util.py.
util.patchAsyncio()


def _settings() -> tuple[str, int, int]:
    host = os.environ.get("IB_HOST", "ib-gateway")
    port = int(os.environ.get("IB_PORT", "4004"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "7"))
    return host, port, client_id


# Monotonic counter for per-session client ids. Lives module-global; the
# starting offset is read from IB_CLIENT_ID at first use.
_client_id_counter: itertools.count | None = None
_client_id_lock = asyncio.Lock()


async def _next_client_id() -> int:
    global _client_id_counter
    async with _client_id_lock:
        if _client_id_counter is None:
            _, _, base = _settings()
            _client_id_counter = itertools.count(base)
        return next(_client_id_counter)


@asynccontextmanager
async def ib_session(*, timeout: float = 10.0):
    """Yield a connected ``IB`` instance, disconnect on exit.

    Each session uses a fresh, monotonically increasing client id so that
    rapid back-to-back tool calls don't collide on a not-yet-torn-down
    previous TCP connection.

    Raises ``ConnectionError`` with a useful message if ib-gateway is
    unreachable or refuses the connection (e.g. wrong port for the active
    trading mode, login not completed yet).
    """
    host, port, _ = _settings()
    client_id = await _next_client_id()
    ib = IB()
    try:
        await asyncio.wait_for(
            ib.connectAsync(host, port, clientId=client_id, readonly=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        raise ConnectionError(
            f"ib-gateway at {host}:{port} (clientId={client_id}) did not "
            f"respond within {timeout}s. Possible causes: gateway still "
            "starting up, login failed, wrong port for trading mode "
            "(paper=4004, live=4003 via socat)."
        ) from e
    except Exception as e:
        raise ConnectionError(
            f"Failed to connect to ib-gateway at {host}:{port} "
            f"(clientId={client_id}): {e!r}"
        ) from e
    try:
        yield ib
    finally:
        ib.disconnect()
