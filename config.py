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

# Primary + secondary keys (use underscore: GROQ_API_KEY_2 not "GROQ_API_KEY 2")
GROQ_API_KEY = env("GROQ_API_KEY")
GROQ_API_KEY_2 = env("GROQ_API_KEY_2", "GROQ_API_KEY2")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "OPENROUTER_KEY")
OPENROUTER_API_KEY_2 = env("OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY2", "OPENROUTER_KEY_2")
GEMINI_API_KEY = env("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY")
CEREBRAS_API_KEY = env("CEREBRAS_API_KEY", "CEREBRAS_KEY")
TAVILY_API_KEY = env("TAVILY_API_KEY", "TAVILY_KEY")
TAVILY_SEARCH_DEPTH = env("TAVILY_SEARCH_DEPTH", default="advanced")
TAVILY_MAX_RESULTS = int(env("TAVILY_MAX_RESULTS", default="8") or "8")
TAVILY_EXTRACT_DEPTH = env("TAVILY_EXTRACT_DEPTH", default="advanced")

GROQ_MODEL = env("GROQ_MODEL", default="openai/gpt-oss-20b")
OPENROUTER_MODEL = env(
    "OPENROUTER_MODEL",
    default="google/gemma-2-9b-it:free",
)
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-2.5-flash")
CEREBRAS_MODEL = env("CEREBRAS_MODEL", default="llama3.1-8b")

X_BEARER_TOKEN = env("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
DATABASE_PATH = env("DATABASE_PATH", default="./marketing.db")
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
BUILD = "2026-09-25-mkt-research-ui-v1"
