# 📣 Web3 Marketing + Competition Intelligence

Compact Telegram bot (6 Python files). Strategy-first: Observation → Diagnosis → Recommendation → Execution.

## Files

```
main.py           # Telegram handlers
config.py         # Env settings
sources.py        # Input parse + website/X/TG collection
engines.py        # AI + marketing/competition engines
db.py             # Watchlist + competition sessions
requirements.txt
README.md
```

## Railway env vars

| Variable | Required | What it does |
|----------|----------|----------------|
| `TELEGRAM_BOT_TOKEN` | **Yes** | From @BotFather (new bot) |
| `GROQ_API_KEY` | Strongly yes | Primary AI |
| `OPENROUTER_API_KEY` | Recommended | Fallback AI |
| `ALLOWED_USER_IDS` | Optional | Your Telegram numeric user id — locks bot to you only. Without it, anyone who finds the bot can use it. |
| `DATABASE_PATH` | Optional | Where SQLite file is stored. Default `./marketing.db`. On Railway ephemeral disk is fine for watchlist/sessions; use a volume path if you need persistence across redeploys. You don't "get" it from anywhere — you choose the path or leave default. |
| `X_BEARER_TOKEN` | Optional | Not required. Without it, send `@username` and the bot uses AI + best-effort public page. |

### What is ALLOWED_USER_IDS?
Your Telegram user id (number). Get it from @userinfobot. If set, only those ids can use the bot. If empty, bot is open.

### What is DATABASE_PATH?
File path for SQLite (watchlist + "more competitors" session memory). Default `./marketing.db` is fine. Not a cloud service — just a local file path.

## Start command
```
python main.py
```

## Commands
/market /competition /competitor /positioning /campaigns /marketgaps /opportunities /compare /report /watch /settings

### Competition
- Web3/crypto competitors only
- Structured profiles with Website / X / Telegram when known
- 🔄 More competitors (deduped session)
- Mode buttons: Similar products, Architecture, Social/Marketing/UX/Community/Growth leaders
