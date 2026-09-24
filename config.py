"""Configuration — env vars only."""
from __future__ import annotations

import os
from urllib.parse import unquote


def env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if not v:
            continue
        v = unquote(v.strip().strip('"').strip("'"))
        if v:
            return v
    return default


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [
    int(x) for x in env("ALLOWED_USER_IDS").replace(" ", "").split(",") if x.isdigit()
]
GROQ_API_KEY = env("GROQ_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "OPENROUTER_KEY")
GROQ_MODEL = env("GROQ_MODEL", default="llama-3.3-70b-versatile")
OPENROUTER_MODEL = env(
    "OPENROUTER_MODEL",
    default="meta-llama/llama-3.3-70b-instruct:free",
)
X_BEARER_TOKEN = env("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
DATABASE_PATH = env("DATABASE_PATH", default="./marketing.db")
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
BUILD = "2026-09-24-mkt-intel-v3-lock-ssl-ai"
