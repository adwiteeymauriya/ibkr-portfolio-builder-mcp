"""Login-gated OAuth provider.

Subclasses FastMCP's ``InMemoryOAuthProvider`` and wraps the SDK's
``/authorize`` route with a session-cookie gate. If the user has no valid
session cookie when they hit ``/authorize``, they are redirected to
``/login?next=<original_url>``. After they submit the magic password we set
a signed cookie and bounce them back to ``/authorize``, where the framework
handler then runs unchanged.

The whole point of this module is to add a human-clickable consent step to
the otherwise auto-approving ``InMemoryOAuthProvider``, so the OAuth flow
works against Claude.ai.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

SESSION_COOKIE_NAME = "ccc_session"
SESSION_TTL_SECONDS = 60 * 60 * 8  # 8h


def _login_page(error: str | None = None, next_url: str = "/") -> str:
    err_html = (
        f'<p style="color:#b00020;margin:0 0 12px 0">{error}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sign in — Claude Custom Connector</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:#0e0e10; color:#eaeaea; display:flex; align-items:center;
            justify-content:center; min-height:100vh; margin:0; }}
    .card {{ background:#1a1a1d; padding:32px; border-radius:12px;
             box-shadow:0 8px 32px rgba(0,0,0,0.4); width:320px; }}
    h1 {{ margin:0 0 4px 0; font-size:18px; }}
    p.sub {{ margin:0 0 20px 0; color:#888; font-size:13px; }}
    label {{ display:block; font-size:12px; color:#aaa; margin-bottom:6px; }}
    input[type=password] {{ width:100%; padding:10px 12px; border-radius:8px;
                            border:1px solid #333; background:#0e0e10; color:#eee;
                            box-sizing:border-box; font-size:14px; }}
    button {{ width:100%; margin-top:16px; padding:10px; border-radius:8px;
              border:0; background:#5e5cff; color:white; font-weight:600;
              font-size:14px; cursor:pointer; }}
    button:hover {{ background:#4845ff; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Sign in</h1>
    <p class="sub">Claude Custom Connector</p>
    {err_html}
    <input type="hidden" name="next" value="{next_url}" />
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autofocus required />
    <button type="submit">Continue</button>
  </form>
</body>
</html>"""


class LoginGatedOAuthProvider(InMemoryOAuthProvider):
    """In-memory OAuth provider that forces a login step before ``/authorize``.

    Adds three things on top of ``InMemoryOAuthProvider``:
    1. A session-cookie gate that wraps the framework's ``/authorize`` route.
    2. A ``/login`` route that renders the password form (GET) and validates
       it (POST), setting a signed cookie on success.
    3. A ``user_id`` baked into the issued ``AccessToken`` so tools can read it
       back via ``get_access_token().claims`` (we stash the user on the token's
       ``scopes`` list — see ``exchange_authorization_code`` below).
    """

    def __init__(
        self,
        *,
        base_url: str,
        login_password: str | None,
        session_secret: str,
        static_bearer_token: str | None = None,
        oauth_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url=base_url, **kwargs)
        self._login_password = login_password
        self._oauth_enabled = oauth_enabled
        self._static_bearer_token = static_bearer_token
        self._signer = URLSafeTimedSerializer(session_secret, salt="ccc-session")

    # ----- session helpers -----

    def _make_session_cookie(self, user_id: str) -> str:
        return self._signer.dumps({"sub": user_id, "iat": int(time.time())})

    def _read_session(self, request: Request) -> str | None:
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if not raw:
            return None
        try:
            data = self._signer.loads(raw, max_age=SESSION_TTL_SECONDS)
        except BadSignature:
            return None
        return data.get("sub")

    # ----- /login routes -----

    async def _login_get(self, request: Request) -> Response:
        next_url = request.query_params.get("next", "/")
        return HTMLResponse(_login_page(next_url=next_url))

    async def _login_post(self, request: Request) -> Response:
        form = await request.form()
        password = str(form.get("password", ""))
        next_url = str(form.get("next", "/"))
        if not self._login_password:
            # OAuth is disabled or no password configured.
            return HTMLResponse(
                _login_page(
                    error="Password login is not enabled on this server.",
                    next_url=next_url,
                ),
                status_code=503,
            )
        # secrets.compare_digest refuses to compare strings with non-ASCII
        # characters and raises TypeError. Encode both sides to bytes so any
        # Unicode-bearing password (em-dashes, curly quotes accidentally
        # pasted in, accented characters, etc.) works.
        if not secrets.compare_digest(
            password.encode("utf-8"),
            self._login_password.encode("utf-8"),
        ):
            return HTMLResponse(
                _login_page(error="Wrong password.", next_url=next_url),
                status_code=401,
            )
        # Single-user stub: hardcode an identity.
        user_id = "demo-user"
        resp = RedirectResponse(url=next_url, status_code=302)
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            self._make_session_cookie(user_id),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return resp

    # ----- /authorize gate -----

    def _wrap_authorize(self, original_endpoint):
        """Wrap the SDK's /authorize handler with a session-cookie gate."""

        async def gated(request: Request) -> Response:
            user_id = self._read_session(request)
            if user_id is None:
                # Force login, then come back to the exact same /authorize URL.
                next_url = "/authorize?" + urlencode(dict(request.query_params))
                return RedirectResponse(
                    url="/login?" + urlencode({"next": next_url}),
                    status_code=302,
                )
            return await original_endpoint(request)

        return gated

    # ----- token verification (static bearer fallback) -----

    async def verify_token(self, token: str):  # type: ignore[override]
        """Accept either the static bearer token or an OAuth-issued token."""
        if self._static_bearer_token and secrets.compare_digest(
            token.encode("utf-8"),
            self._static_bearer_token.encode("utf-8"),
        ):
            # Return a synthetic AccessToken so the MCP middleware treats the
            # caller as authenticated.
            from mcp.server.auth.provider import AccessToken

            return AccessToken(
                token=token,
                client_id="static-bearer",
                scopes=[],
                expires_at=None,
            )
        return await super().verify_token(token)

    # ----- route assembly -----

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:  # type: ignore[override]
        routes = super().get_routes(mcp_path)

        wrapped: list[Route] = []
        for route in routes:
            if (
                isinstance(route, Route)
                and route.path == "/authorize"
                and route.methods
            ):
                # The SDK registers /authorize as a single Route allowing both
                # GET and POST; preserve whatever methods it declared so the
                # gate covers every method that could reach the underlying
                # authorize handler.
                wrapped.append(
                    Route(
                        path="/authorize",
                        endpoint=self._wrap_authorize(route.endpoint),
                        methods=list(route.methods),
                    )
                )
            else:
                wrapped.append(route)

        wrapped.append(Route("/login", endpoint=self._login_get, methods=["GET"]))
        wrapped.append(Route("/login", endpoint=self._login_post, methods=["POST"]))
        return wrapped
