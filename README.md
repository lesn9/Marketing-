# Web3 Marketing + Competition Intelligence

Exactly **4 Python files** + requirements + README.

```
main.py            Telegram handlers, buttons, commands
config.py          Env vars
intelligence.py    Sources + AI + all strategy engines
database.py        SQLite (watchlist, competitor sessions)
requirements.txt
README.md
```

## Railway env

| Variable | Required | Notes |
|----------|----------|-------|
| TELEGRAM_BOT_TOKEN | Yes | New bot from BotFather |
| GROQ_API_KEY | Strongly yes | Primary AI |
| OPENROUTER_API_KEY | Recommended | Fallback |
| ALLOWED_USER_IDS | Optional | Your Telegram numeric id (from @userinfobot). Locks bot to you. |
| DATABASE_PATH | Optional | Default `./marketing.db` — local SQLite file path, not something you download |
| GROQ_MODEL | Optional | Default llama-3.3-70b-versatile; if errors try llama-3.1-8b-instant |
| OPENROUTER_MODEL | Optional | Free model fallback |

Start: `python main.py`

## Commands

/market /marketingaudit /positioning /competition /competitor /campaigns
/marketingfunnels /funnels /suggestmarketing /organicmarketing /zeromarketing
/marketgaps /opportunities /compare /report /fullpack
/watch /watchlist /unwatch /settings /help

## AI errors with keys set

Bot tries multiple models on Groq then OpenRouter.
If you still see AI_PROVIDER_ERROR: check Railway logs, rate limits, and try GROQ_MODEL=llama-3.1-8b-instant.
