"""FastMCP app entrypoint.

Wires up the login-gated OAuth provider, registers tools, and builds the
Starlette application that exposes:

  /mcp                                  -- Streamable HTTP MCP endpoint
  /authorize, /token, /register, etc.   -- OAuth 2.1 (self-hosted)
  /login                                -- HTML password form (consent step)
  /.well-known/oauth-protected-resource -- RFC 9728
  /.well-known/oauth-authorization-server -- RFC 8414
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth.auth import ClientRegistrationOptions
from starlette.applications import Starlette
from starlette.routing import Mount

from connector.auth import LoginGatedOAuthProvider
from connector.settings import Settings
from connector.tools import register_tools

MCP_PATH = "/mcp"


def build_app() -> Starlette:
    settings = Settings.from_env()

    auth = LoginGatedOAuthProvider(
        base_url=settings.public_base_url,
        login_password=settings.login_password,
        session_secret=settings.session_secret,
        static_bearer_token=settings.static_bearer_token if settings.bearer_enabled else None,
        oauth_enabled=settings.oauth_enabled,
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    mcp = FastMCP(name="IBKR MCP Connector", auth=auth)
    register_tools(mcp)

    mcp_app = mcp.http_app(path=MCP_PATH)

    app = Starlette(
        routes=[
            *auth.get_well_known_routes(mcp_path=MCP_PATH),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.lifespan,
    )
    return app


app = build_app()


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "connector.server:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
