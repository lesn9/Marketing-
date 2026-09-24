import asyncio
import logging
import re
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, Message
import httpx
from bs4 import BeautifulSoup

from ai_engine import AIEngine, AIProviderError, MARKETING_SYSTEM
from config import load_settings

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot")

# ---------- Load settings (safe) ----------
try:
    settings = load_settings()
except Exception as e:
    logger.critical(f"Failed to load settings: {e}")
    sys.exit(1)

bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
ai = AIEngine(settings.groq_api_key, settings.openrouter_api_key)

# ---------- Native Telegram commands ----------
# Selecting a command from the / menu inserts it into the composer.
# User can still add arguments and press Send manually.
COMMANDS = [
    BotCommand(command="start", description="Start the bot"),
    BotCommand(command="help", description="Show available commands"),
    BotCommand(command="market", description="Marketing intelligence report"),
    BotCommand(command="competition", description="Competitive landscape"),
    BotCommand(command="competitor", description="Deep dive on one competitor"),
    BotCommand(command="positioning", description="Positioning analysis"),
    BotCommand(command="opportunities", description="Actionable marketing opportunities"),
    BotCommand(command="report", description="Full marketing + competition report"),
    BotCommand(command="campaigns", description="Visible campaign analysis"),
    BotCommand(command="marketgaps", description="Marketing & competitive gaps"),
    BotCommand(command="compare", description="Compare two projects"),
    BotCommand(command="settings", description="Bot status & providers"),
]


async def set_commands():
    try:
        await bot.set_my_commands(COMMANDS)
        logger.info("Native bot commands registered successfully")
    except Exception as e:
        logger.error(f"Failed to set commands: {e}")


# ---------- Helpers ----------
def parse_sources(text: str) -> dict:
    sources = {
        "website": None,
        "x": None,
        "telegram": None,
        "ca": None,
        "raw": text.strip(),
    }

    url_match = re.search(r"https?://[^\s]+", text)
    if url_match:
        sources["website"] = url_match.group(0).rstrip(".,)")

    x_match = re.search(r"(?:https?://(?:x|twitter)\.com/|@)([A-Za-z0-9_]{1,15})", text, re.I)
    if x_match:
        sources["x"] = x_match.group(1)

    tg_match = re.search(r"(?:https?://t\.me/|@)([A-Za-z0-9_]{5,})", text, re.I)
    if tg_match and not sources["x"]:
        sources["telegram"] = tg_match.group(1)

    ca_match = re.search(r"0x[a-fA-F0-9]{40}", text)
    if ca_match:
        sources["ca"] = ca_match.group(0)

    sol_match = re.search(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text)
    if sol_match and not sources["ca"]:
        sources["ca"] = sol_match.group(0)

    return sources


async def fetch_website(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketingBot/1.0)"})
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:8000]
    except Exception as e:
        logger.warning(f"Website fetch failed for {url}: {e}")
        return f"[Website unavailable: {url}]"


async def collect_evidence(sources: dict) -> str:
    parts = []
    if sources["website"]:
        content = await fetch_website(sources["website"])
        parts.append(f"🌐 WEBSITE ({sources['website']}):\n{content}")
    if sources["x"]:
        parts.append(f"🐦 X: @{sources['x']} (live metrics unavailable unless X_BEARER_TOKEN is set)")
    if sources["telegram"]:
        parts.append(f"💬 Telegram: @{sources['telegram']} (public group data limited)")
    if sources["ca"]:
        parts.append(f"⛓️ Contract: {sources['ca']}")
    if not parts:
        parts.append(f"Raw input: {sources['raw']}")
    return "\n\n".join(parts)


async def run_analysis(command: str, sources: dict, extra: str = "") -> str:
    evidence = await collect_evidence(sources)
    user_prompt = f"""Command: /{command}
Sources supplied:
{evidence}

{extra}

Produce the response using the exact section structure expected for this command.
Keep it mobile-friendly and evidence-based.
"""
    try:
        return await ai.chat(MARKETING_SYSTEM, user_prompt)
    except AIProviderError as e:
        logger.error(f"AI error: {e.code} - {e.message}")
        if e.code == "AI_NOT_CONFIGURED":
            return "⚠️ AI providers are not configured.\nPlease set GROQ_API_KEY or OPENROUTER_API_KEY in Railway Variables."
        return f"⚠️ AI temporarily unavailable ({e.code}). Please try again shortly."


# ---------- Handlers ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "📣 <b>Marketing + Competition Intelligence Bot</b>\n\n"
        "Send any project evidence and get marketing & competitive analysis.\n\n"
        "Examples:\n"
        "<code>/market https://project.com</code>\n"
        "<code>/market @handle</code>\n"
        "<code>/market 0x...</code>\n"
        "<code>/competition https://project.com</code>\n\n"
        "Type /help for all commands.\n\n"
        "Commands appear in Telegram’s native / menu and are inserted into the composer "
        "so you can still add arguments before sending."
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📣 <b>Available Commands</b>\n\n"
        "/market – Marketing intelligence\n"
        "/competition – Competitive landscape\n"
        "/competitor – Deep dive on one competitor\n"
        "/positioning – Positioning analysis\n"
        "/opportunities – Actionable opportunities\n"
        "/report – Full report\n"
        "/campaigns – Campaign analysis\n"
        "/marketgaps – Gaps analysis\n"
        "/compare – Compare two projects\n"
        "/settings – Status\n\n"
        "You can supply website, X, Telegram link, or contract address (any combination)."
    )
    await message.answer(text)


@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    groq = "✅ Connected" if settings.groq_api_key else "❌ Not set"
    openrouter = "✅ Connected" if settings.openrouter_api_key else "❌ Not set"
    x = "✅ Available" if settings.x_bearer_token else "⚠️ Unavailable"

    text = (
        "⚙️ <b>SETTINGS</b>\n\n"
        "🧠 <b>AI</b>\n"
        f"Groq: {groq}\n"
        f"OpenRouter: {openrouter}\n\n"
        "🐦 <b>X</b>\n"
        f"Live X metrics: {x}\n\n"
        "🌐 <b>Data Sources</b>\n"
        "Website: Available\n"
        "Telegram: Limited\n"
        "Blockchain: Basic\n\n"
        "📡 Monitoring: Active (basic)"
    )
    await message.answer(text)


@dp.message(Command("market"))
async def cmd_market(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/market https://project.com</code> or <code>/market @handle</code> or CA")
        return
    status = await message.answer("🔍 Collecting evidence & analyzing…")
    sources = parse_sources(command.args)
    result = await run_analysis("market", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("competition"))
async def cmd_competition(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/competition https://project.com</code>")
        return
    status = await message.answer("⚔️ Analyzing competitive landscape…")
    sources = parse_sources(command.args)
    result = await run_analysis("competition", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("competitor"))
async def cmd_competitor(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/competitor @competitor or website</code>")
        return
    status = await message.answer("⚔️ Building competitor profile…")
    sources = parse_sources(command.args)
    result = await run_analysis("competitor", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("positioning"))
async def cmd_positioning(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/positioning https://project.com</code>")
        return
    status = await message.answer("🎯 Analyzing positioning…")
    sources = parse_sources(command.args)
    result = await run_analysis("positioning", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("opportunities"))
async def cmd_opportunities(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/opportunities https://project.com</code>")
        return
    status = await message.answer("💡 Generating opportunities…")
    sources = parse_sources(command.args)
    result = await run_analysis("opportunities", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/report https://project.com</code>")
        return
    status = await message.answer("📊 Generating full report (this may take a moment)…")
    sources = parse_sources(command.args)
    result = await run_analysis("report", sources)

    # Send in chunks if long
    chunks = [result[i:i + 4000] for i in range(0, len(result), 4000)]
    await status.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.answer(chunk)


@dp.message(Command("campaigns"))
async def cmd_campaigns(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/campaigns https://project.com</code>")
        return
    status = await message.answer("📣 Analyzing campaigns…")
    sources = parse_sources(command.args)
    result = await run_analysis("campaigns", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("marketgaps"))
async def cmd_marketgaps(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: <code>/marketgaps https://project.com</code>")
        return
    status = await message.answer("🕳️ Identifying gaps…")
    sources = parse_sources(command.args)
    result = await run_analysis("marketgaps", sources)
    await status.edit_text(result[:4000])


@dp.message(Command("compare"))
async def cmd_compare(message: Message, command: CommandObject):
    if not command.args or " " not in command.args.strip():
        await message.answer("Usage: <code>/compare project1 project2</code>")
        return
    status = await message.answer("⚖️ Comparing projects…")
    sources = parse_sources(command.args)
    result = await run_analysis("compare", sources, extra="Compare the two projects descriptively.")
    await status.edit_text(result[:4000])


# ---------- Startup ----------
async def main():
    logger.info("Starting Marketing + Competition Intelligence Bot...")
    await set_commands()
    logger.info("Bot is now running (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
        sys.exit(1)
