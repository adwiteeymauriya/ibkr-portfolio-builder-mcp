"""Settings loaded from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["both", "oauth", "bearer"]


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    auth_mode: AuthMode
    login_password: str | None
    session_secret: str
    static_bearer_token: str | None
    host: str
    port: int

    @property
    def oauth_enabled(self) -> bool:
        return self.auth_mode in ("oauth", "both") and bool(self.login_password)

    @property
    def bearer_enabled(self) -> bool:
        return self.auth_mode in ("bearer", "both") and bool(self.static_bearer_token)

    @classmethod
    def from_env(cls) -> Settings:
        public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        raw_mode = os.environ.get("AUTH_MODE", "both").strip().lower()
        if raw_mode not in ("both", "oauth", "bearer"):
            raise RuntimeError(
                f"AUTH_MODE must be one of: both, oauth, bearer (got {raw_mode!r})"
            )
        auth_mode: AuthMode = raw_mode  # type: ignore[assignment]

        login_password = os.environ.get("LOGIN_PASSWORD") or None
        static_bearer_token = os.environ.get("STATIC_BEARER_TOKEN") or None
        session_secret = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
        host = os.environ.get("HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", "8000"))

        # Validate that the selected mode has the credentials it needs.
        if auth_mode in ("oauth", "both") and not login_password and auth_mode == "oauth":
            raise RuntimeError(
                "AUTH_MODE=oauth requires LOGIN_PASSWORD to be set."
            )
        if auth_mode == "bearer" and not static_bearer_token:
            raise RuntimeError(
                "AUTH_MODE=bearer requires STATIC_BEARER_TOKEN to be set."
            )
        if auth_mode == "both" and not login_password and not static_bearer_token:
            raise RuntimeError(
                "AUTH_MODE=both requires at least one of LOGIN_PASSWORD or "
                "STATIC_BEARER_TOKEN to be set."
            )

        return cls(
            public_base_url=public_base_url,
            auth_mode=auth_mode,
            login_password=login_password,
            session_secret=session_secret,
            static_bearer_token=static_bearer_token,
            host=host,
            port=port,
        )
