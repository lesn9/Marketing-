"""
📣 Web3 Marketing + Competition Intelligence Bot (compact)
Start: python main.py
"""
from __future__ import annotations

import html
import logging
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config
import engines
from db import DB
from sources import collect_sources, parse_user_input, sources_label

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("mkt")

HELP = """📣 <b>Marketing + Competition Intelligence</b>

Not a website summarizer — strategy: Observation → Diagnosis → Recommendation → Execution.

<b>Input</b> (any mix):
<code>/market @handle</code>
<code>/market https://site.com</code>
<code>/market @h https://site.com https://t.me/x meme</code>

<b>Commands</b>
/market — marketing strategy audit
/competition — Web3 competitor intelligence (+ More / modes)
/competitor — deep-dive one comparable
/positioning · /campaigns · /marketgaps · /opportunities
/compare A | B — two projects
/report — full pack
/watch · /watchlist · /unwatch
/settings · /help

X works with just <code>@username</code> (no paid X API required).
Competitors are <b>crypto/Web3 only</b>.
"""


def esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def allowed(uid: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return uid in config.ALLOWED_USER_IDS


async def gate(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return False
    if not allowed(u.id):
        if update.effective_message:
            await update.effective_message.reply_text("Private bot.")
        return False
    return True


async def reply_long(msg, text: str) -> None:
    while text:
        chunk, text = text[:4000], text[4000:]
        if len(text) and len(chunk) == 4000:
            cut = chunk.rfind("\n")
            if cut > 500:
                text = chunk[cut:] + text
                chunk = chunk[:cut]
        try:
            await msg.reply_html(chunk, disable_web_page_preview=True)
        except Exception:
            await msg.reply_text(chunk)


def competition_keyboard(session_id: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔄 More competitors", callback_data=f"cm:more:{session_id}"),
        ],
        [
            InlineKeyboardButton("🎯 Similar products", callback_data=f"cm:product:{session_id}"),
            InlineKeyboardButton("🏗️ Architecture", callback_data=f"cm:architecture:{session_id}"),
        ],
        [
            InlineKeyboardButton("🐦 Social leaders", callback_data=f"cm:social:{session_id}"),
            InlineKeyboardButton("📣 Marketing leaders", callback_data=f"cm:marketing:{session_id}"),
        ],
        [
            InlineKeyboardButton("🌐 UX leaders", callback_data=f"cm:ux:{session_id}"),
            InlineKeyboardButton("💬 Community leaders", callback_data=f"cm:community:{session_id}"),
        ],
        [
            InlineKeyboardButton("🚀 Growth leaders", callback_data=f"cm:growth:{session_id}"),
            InlineKeyboardButton("💎 Product leaders", callback_data=f"cm:product_leaders:{session_id}"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str], name: str):
    msg = update.effective_message
    assert msg
    parsed = parse_user_input(args)
    if not any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
        await msg.reply_text("Need an @handle, website, t.me link, or contract.")
        return
    status = await msg.reply_html(
        f"🔎 Collecting… X={len(parsed.x_handles)} web={len(parsed.websites)} "
        f"TG={len(parsed.telegrams)}"
        + (f" · type={esc(parsed.project_type)}" if parsed.project_type else "")
    )
    sources = await collect_sources(parsed, bot=context.bot)
    # Optional extra X strategy block when handle-only
    x_extra = ""
    if (sources.get("x") or {}).get("mode") in {"handle_only", "public_html"}:
        x_extra = await engines.enrich_x_analysis_via_ai(sources)

    await status.edit_text(f"🧠 <b>{esc(name)}</b>…\nSources: {esc(sources_label(sources))}", parse_mode="HTML")

    if name == "market":
        text, st = await engines.run_market(sources)
    elif name == "positioning":
        text, st = await engines.run_positioning(sources)
    elif name == "campaigns":
        text, st = await engines.run_campaigns(sources)
    elif name == "marketgaps":
        text, st = await engines.run_marketgaps(sources)
    elif name == "opportunities":
        text, st = await engines.run_opportunities(sources)
    elif name == "report":
        text, st = await engines.run_report(sources)
    else:
        text, st = "Unknown", "AI_PROVIDER_ERROR"

    if x_extra and name in {"market", "report", "positioning"}:
        text = (text or "") + "\n\n——\n" + x_extra

    header = f"📣 <b>{esc(name.upper())}</b>\nSources: {esc(sources_label(sources))}\nAI: {esc(st)}\n\n"
    await reply_long(msg, header + (text or ""))
    try:
        await status.delete()
    except Exception:
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    await update.effective_message.reply_html("📣 <b>Online.</b>\n\n" + HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await gate(update):
        await update.effective_message.reply_html(HELP)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    await update.effective_message.reply_html(
        "⚙️ <b>Settings</b>\n"
        f"Build: <code>{esc(config.BUILD)}</code>\n"
        f"Groq: {'set' if config.GROQ_API_KEY else 'NOT set'}\n"
        f"OpenRouter: {'set' if config.OPENROUTER_API_KEY else 'NOT set'}\n"
        f"X Bearer: {'set (optional)' if config.X_BEARER_TOKEN else 'not set — using @handle + AI'}\n"
        f"Allowed users: {config.ALLOWED_USER_IDS or 'open (anyone)'}\n"
        f"DB path: <code>{esc(config.DATABASE_PATH)}</code>\n\n"
        "<b>Env</b>: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, OPENROUTER_API_KEY, "
        "ALLOWED_USER_IDS (optional), DATABASE_PATH (optional)"
    )


async def _need_args(update, context, usage: str):
    if not context.args:
        await update.effective_message.reply_html(usage)
        return None
    return list(context.args)


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/market @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "market")


async def cmd_positioning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/positioning @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "positioning")


async def cmd_campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/campaigns @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "campaigns")


async def cmd_marketgaps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/marketgaps @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "marketgaps")


async def cmd_opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/opportunities @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "opportunities")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    args = await _need_args(update, context, "Usage: <code>/report @handle|url …</code>")
    if args:
        await pipeline(update, context, args, "report")


async def cmd_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update) or not update.effective_user:
        return
    args = await _need_args(update, context, "Usage: <code>/competition @handle|url …</code>")
    if not args:
        return
    msg = update.effective_message
    status = await msg.reply_text("🔎 Collecting subject + discovering Web3 competitors…")
    parsed = parse_user_input(args)
    sources = await collect_sources(parsed, bot=context.bot)
    text, st, names = await engines.discover_competitors(sources, mode="similar", exclude=[], batch_size=5)
    sid = secrets.token_hex(8)
    db: DB = context.application.bot_data["db"]
    await db.save_comp_session(sid, update.effective_user.id, sources, "similar", names)
    # also keep in memory for fast more
    context.application.bot_data.setdefault("comp", {})[sid] = {
        "sources": sources, "mode": "similar", "shown": list(names), "user_id": update.effective_user.id,
    }
    try:
        await status.delete()
    except Exception:
        pass
    header = (
        f"🏆 <b>COMPETITION</b> (Web3 only)\n"
        f"Sources: {esc(sources_label(sources))}\nAI: {esc(st)}\n"
        f"Batch: {len(names)} · session <code>{esc(sid)}</code>\n\n"
    )
    await reply_long(msg, header + (text or ""))
    await msg.reply_html(
        "Use buttons for <b>More</b> or a different discovery mode:",
        reply_markup=competition_keyboard(sid),
    )


async def cb_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user:
        return
    if not allowed(update.effective_user.id):
        await q.answer("Private", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3:
        await q.answer()
        return
    _, action, sid = parts[0], parts[1], parts[2]
    mem = (context.application.bot_data.get("comp") or {}).get(sid)
    db: DB = context.application.bot_data["db"]
    if not mem:
        row = await db.get_comp_session(sid)
        if not row:
            await q.answer("Session expired — run /competition again", show_alert=True)
            return
        mem = {"sources": row["subject"], "mode": row["mode"], "shown": row["shown"], "user_id": row["user_id"]}
        context.application.bot_data.setdefault("comp", {})[sid] = mem
    if mem.get("user_id") != update.effective_user.id:
        await q.answer("Not your session", show_alert=True)
        return

    mode = mem.get("mode") or "similar"
    if action != "more":
        mode = action
        mem["mode"] = mode
    await q.answer("Researching…")
    status = await q.message.reply_text(f"🔄 Discovery mode={mode}…")
    text, st, names = await engines.discover_competitors(
        mem["sources"], mode=mode, exclude=list(mem.get("shown") or []), batch_size=5,
    )
    shown = list(mem.get("shown") or [])
    for n in names:
        if n not in shown:
            shown.append(n)
    mem["shown"] = shown
    await db.save_comp_session(sid, update.effective_user.id, mem["sources"], mode, shown)
    try:
        await status.delete()
    except Exception:
        pass
    header = (
        f"🏆 <b>COMPETITION</b> · mode={esc(mode)}\n"
        f"AI: {esc(st)} · new={len(names)} · total shown={len(shown)}\n\n"
    )
    await reply_long(q.message, header + (text or "No further competitors found."))
    await q.message.reply_html("Continue:", reply_markup=competition_keyboard(sid))


async def cmd_competitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_html(
            "Usage: <code>/competitor &lt;subject @/url&gt; &lt;competitor name&gt;</code>"
        )
        return
    focus = context.args[-1]
    subject = context.args[:-1]
    status = await update.effective_message.reply_text("🔎…")
    sources = await collect_sources(parse_user_input(subject), bot=context.bot)
    text, st = await engines.run_competitor_focus(sources, focus)
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(update.effective_message, f"🎯 <b>COMPETITOR</b> {esc(focus)}\nAI: {esc(st)}\n\n{text}")


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update):
        return
    raw = " ".join(context.args or [])
    if "|" not in raw:
        await update.effective_message.reply_html(
            "Usage: <code>/compare @a https://a.com | @b https://b.com</code>"
        )
        return
    left, right = raw.split("|", 1)
    status = await update.effective_message.reply_text("🔎 Comparing…")
    sa = await collect_sources(parse_user_input(left.split()), bot=context.bot)
    sb = await collect_sources(parse_user_input(right.split()), bot=context.bot)
    text, st = await engines.run_compare(sa, sb)
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(update.effective_message, f"⚖️ <b>COMPARE</b>\nAI: {esc(st)}\n\n{text}")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update) or not update.effective_user:
        return
    args = await _need_args(update, context, "Usage: <code>/watch @handle|url</code>")
    if not args:
        return
    key = " ".join(args)[:200]
    await context.application.bot_data["db"].watch(update.effective_user.id, key, key, {"input": key})
    await update.effective_message.reply_html(f"⭐ Watching <code>{esc(key)}</code>")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update) or not update.effective_user:
        return
    args = await _need_args(update, context, "Usage: <code>/unwatch @handle|url</code>")
    if not args:
        return
    await context.application.bot_data["db"].unwatch(update.effective_user.id, " ".join(args)[:200])
    await update.effective_message.reply_text("Removed.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update) or not update.effective_user:
        return
    rows = await context.application.bot_data["db"].watchlist(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Empty. /watch @handle")
        return
    await update.effective_message.reply_html(
        "⭐ <b>Watchlist</b>\n" + "\n".join(f"• <code>{esc(r['key'])}</code>" for r in rows[:40])
    )


async def on_start(app: Application) -> None:
    db = DB(config.DATABASE_PATH)
    await db.connect()
    app.bot_data["db"] = db
    app.bot_data["comp"] = {}
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Start"),
            BotCommand("help", "Help"),
            BotCommand("market", "Marketing strategy audit"),
            BotCommand("competition", "Web3 competitive intelligence"),
            BotCommand("competitor", "Deep-dive comparable"),
            BotCommand("positioning", "Positioning"),
            BotCommand("campaigns", "Campaigns"),
            BotCommand("marketgaps", "Gaps → actions"),
            BotCommand("opportunities", "Growth opportunities"),
            BotCommand("compare", "Compare two projects"),
            BotCommand("report", "Full report"),
            BotCommand("watch", "Watch subject"),
            BotCommand("watchlist", "List watches"),
            BotCommand("unwatch", "Unwatch"),
            BotCommand("settings", "Keys + build"),
        ])
    except Exception as exc:
        log.warning("commands: %s", exc)
    me = await app.bot.get_me()
    log.info("Online @%s %s", me.username, config.BUILD)


async def on_stop(app: Application) -> None:
    db = app.bot_data.get("db")
    if db:
        await db.close()


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN")
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(on_start)
        .post_shutdown(on_stop)
        .build()
    )
    for cmd, fn in [
        ("start", cmd_start), ("help", cmd_help), ("settings", cmd_settings),
        ("market", cmd_market), ("competition", cmd_competition), ("competitor", cmd_competitor),
        ("positioning", cmd_positioning), ("campaigns", cmd_campaigns),
        ("marketgaps", cmd_marketgaps), ("opportunities", cmd_opportunities),
        ("compare", cmd_compare), ("report", cmd_report),
        ("watch", cmd_watch), ("watchlist", cmd_watchlist), ("unwatch", cmd_unwatch),
    ]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(cb_competition, pattern=r"^cm:"))
    log.info("Polling %s", config.BUILD)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
