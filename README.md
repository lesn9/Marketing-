# Web3 Marketing + Competition Intelligence Bot

This build uses a research-first architecture:

**project → source collection → live web research → evidence → AI analysis → one-message Telegram UI**

The bot is designed to behave like a practical Web3 marketer/competitive-intelligence researcher, not a generic report generator.

## Railway environment variables

Required:

- `TELEGRAM_BOT_TOKEN`
- At least one AI key: `GROQ_API_KEY`, `GEMINI_API_KEY`, `CEREBRAS_API_KEY`, or `OPENROUTER_API_KEY`

Recommended for deep web research:

- `TAVILY_API_KEY`

Optional:

- `GROQ_API_KEY_2`
- `OPENROUTER_API_KEY_2`
- `GROQ_MODEL` (default: `openai/gpt-oss-20b`)
- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `OPENROUTER_MODEL`
- `CEREBRAS_MODEL`
- `X_BEARER_TOKEN` for direct X API profile/recent-post data
- `ALLOWED_USER_IDS`
- `DATABASE_PATH` (default `./marketing.db`)
- `TAVILY_SEARCH_DEPTH` (`advanced` by default)
- `TAVILY_MAX_RESULTS` (default `8`)
- `TAVILY_EXTRACT_DEPTH` (`advanced` by default)

No Tavily SDK is required; the bot calls Tavily through the existing `httpx` dependency.

## Deep research

When `TAVILY_API_KEY` is configured, the research layer expands beyond the supplied homepage/handle. It searches for project-specific evidence around:

- website/docs/product
- X/Twitter and public posts where indexed
- Telegram/Discord/community
- Reddit
- YouTube
- GitHub
- Medium/Mirror/newsletters
- campaigns
- partnerships
- AMAs/Spaces
- creators/media
- growth and launch activity

Tavily results are treated as evidence, not as permission to invent missing facts. If a platform cannot be verified, the analysis should say so rather than calling it inactive.

## Competition workspace

`/competition` has category buttons instead of generic command-navigation buttons:

- Similar products
- Architecture
- Social leaders
- Marketing
- UX leaders
- Community
- Growth
- Same-stage
- Same-level

Each category is researched separately and remembers projects already shown so the same project is not automatically recycled across every category. There is no forced competitor quota: fewer genuine matches are better than padded lists.

The Competition UI also has:

- More
- Examples
- Refresh
- Back / Next

Pages are edited in place. The bot does not dump all pages into the chat.

## Telegram UI

Research outputs are paginated to one message at a time. `Next`, `Back`, `Examples`, `Refresh`, and command navigation edit the existing message where applicable.

Generic command navigation is intentionally absent from:

`/reply`, `/settings`, `/start`, `/fullback`, `/report`, `/marketingreply`, `/unwatch`, `/shuffle`, `/help`

`/report` has its own section buttons.

## Human output rules

The AI is instructed to:

- research before analysis
- distinguish facts from inference
- avoid invented metrics, accounts, partnerships and product claims
- give concrete examples and execution steps
- keep the user external to the project unless explicitly told otherwise
- avoid corporate AI clichés and giant markdown tables
- write in natural Web3 language without forced slang

## Files

- `main.py` — Telegram commands, callbacks, pagination and UI
- `config.py` — environment variables
- `intelligence.py` — source collection, Tavily research, AI routing and strategy engines
- `database.py` — SQLite sessions/watchlist/cache
- `db.py` — legacy helper kept unchanged
- `engines.py` — legacy file kept unchanged; not used by the active architecture
- `requirements.txt`

## Run

```bash
python main.py
```
