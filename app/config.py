from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_base_url: str
    github_webhook_secret: str
    github_oauth_client_id: str
    github_oauth_client_secret: str
    admin_logins: tuple[str, ...]
    admin_token: str
    cookie_secret: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("MERGEWORK_DATABASE_URL", "sqlite:///./mergework.sqlite3"),
        public_base_url=os.environ.get("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.ltclab.site"),
        github_webhook_secret=os.environ.get("MERGEWORK_GITHUB_WEBHOOK_SECRET", ""),
        github_oauth_client_id=os.environ.get("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", ""),
        github_oauth_client_secret=os.environ.get("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", ""),
        admin_logins=tuple(
            login.strip().lower()
            for login in os.environ.get("MERGEWORK_ADMIN_LOGINS", "ramimbo").split(",")
            if login.strip()
        ),
        admin_token=os.environ.get("MERGEWORK_ADMIN_TOKEN", ""),
        cookie_secret=os.environ.get("MERGEWORK_COOKIE_SECRET", ""),
    )
