"""
📣 Web3 Marketing + Competition Intelligence Bot
4-file architecture: main.py · config.py · intelligence.py · database.py
"""
from __future__ import annotations

import html
import logging
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import intelligence as intel
from database import DB

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("mkt")

HELP = """📣 <b>Marketing + Competition Intelligence</b>

Strategy: Current → Diagnosis → Recommendation → Execution.

<b>Research</b>
/market · /marketingaudit · /positioning · /competition · /competitor
/campaigns · /funnels · /suggestmarketing · /organicmarketing · /zeromarketing
/marketgaps · /opportunities · /compare · /report

<b>Proposal & reply</b>
/marketingproposals @project|url — proposal you can send a team
/reply · /marketingreply — paste a message or ask what to say
/shuffle — new human variations of the last answer

You can also just type naturally:
“quick idea for a dev DM”
“how would I propose this to them”
“reply to: we’re focused on onboarding”

Buttons under answers: more variations · tone · perspective · format.

<b>Other</b>
/watch · /settings · /help
"""


def esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def allowed(uid: int, app: Application | None = None) -> bool:
    """Env ALLOWED_USER_IDS wins; else locked owner from first /start."""
    if config.ALLOWED_USER_IDS:
        return uid in config.ALLOWED_USER_IDS
    if app is not None:
        owners = app.bot_data.get("owners") or []
        if owners:
            return uid in owners
    return True  # open only until first /start locks


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE | None = None) -> bool:
    u = update.effective_user
    if not u:
        return False
    app = context.application if context else None
    if not allowed(u.id, app):
        if update.effective_message:
            await update.effective_message.reply_text("Private bot — access denied.")
        return False
    return True


async def reply_long(msg, text: str) -> None:
    while text:
        chunk, text = text[:4000], text[4000:]
        if text and len(chunk) == 4000:
            cut = chunk.rfind("\n")
            if cut > 500:
                text = chunk[cut:] + text
                chunk = chunk[:cut]
        try:
            await msg.reply_html(chunk, disable_web_page_preview=True)
        except Exception:
            await msg.reply_text(chunk)


def competition_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 More competitors", callback_data=f"cm:more:{session_id}")],
        [
            InlineKeyboardButton("🎯 Similar products", callback_data=f"cm:product:{session_id}"),
            InlineKeyboardButton("🏗️ Architecture", callback_data=f"cm:architecture:{session_id}"),
        ],
        [
            InlineKeyboardButton("🐦 Social leaders", callback_data=f"cm:social:{session_id}"),
            InlineKeyboardButton("📣 Marketing", callback_data=f"cm:marketing:{session_id}"),
        ],
        [
            InlineKeyboardButton("🌐 UX leaders", callback_data=f"cm:ux:{session_id}"),
            InlineKeyboardButton("💬 Community", callback_data=f"cm:community:{session_id}"),
        ],
        [
            InlineKeyboardButton("🚀 Growth", callback_data=f"cm:growth:{session_id}"),
            InlineKeyboardButton("📈 Same-stage", callback_data=f"cm:samestage:{session_id}"),
        ],
    ])


async def run_engine(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str], name: str):
    msg = update.effective_message
    assert msg
    parsed = intel.parse_user_input(args)
    if not any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
        await msg.reply_text("Need @handle, website, t.me, or contract.")
        return
    status = await msg.reply_html(
        f"🔎 Collecting… X={len(parsed.x_handles)} web={len(parsed.websites)} TG={len(parsed.telegrams)}"
        + (f" · type={esc(parsed.project_type)}" if parsed.project_type else "")
    )
    sources = await intel.collect_sources(parsed, bot=context.bot)
    await status.edit_text(
        f"🧠 <b>{esc(name)}</b>…\nSources: {esc(intel.sources_label(sources))}",
        parse_mode="HTML",
    )

    runners = {
        "market": intel.run_marketing_audit,
        "marketingaudit": intel.run_marketing_audit,
        "positioning": intel.run_positioning,
        "campaigns": intel.run_campaigns,
        "marketgaps": intel.run_marketgaps,
        "opportunities": intel.run_opportunities,
        "report": intel.run_report,
        "fullpack": intel.run_report,
        "funnels": intel.run_funnels,
        "marketingfunnels": intel.run_funnels,
        "suggestmarketing": intel.run_suggest_marketing,
        "marketingideas": intel.run_suggest_marketing,
        "organicmarketing": intel.run_organic,
        "zeromarketing": intel.run_zero,
        "0marketing": intel.run_zero,
    }
    if name in ("marketingproposals", "proposals"):
        text, st = await intel.run_marketing_proposals(sources, style="full")
    else:
        fn = runners.get(name)
        if not fn:
            text, st = "Unknown command", "AI_PROVIDER_ERROR"
        else:
            text, st = await fn(sources)

    # Remember research for follow-up /reply /proposals /shuffle
    if update.effective_user:
        uid = update.effective_user.id
        sessions = context.application.bot_data.setdefault("sessions", {})
        sessions[uid] = {
            "sources": sources,
            "last_text": text or "",
            "last_kind": name,
            "options": intel.extract_options(text or ""),
        }

    header = (
        f"📣 <b>{esc(name.upper())}</b>\n"
        f"Sources: {esc(intel.sources_label(sources))}\n"
        f"AI: {esc(st)}\n\n"
    )
    await reply_long(msg, header + (text or ""))
    if name in ("suggestmarketing", "marketingideas"):
        kb = _suggest_keyboard()
    elif name in ("marketingproposals", "proposals"):
        kb = _action_keyboard("prop")
    else:
        kb = _action_keyboard("gen")
    await msg.reply_html("Refine:", reply_markup=kb)
    try:
        await status.delete()
    except Exception:
        pass


def _action_keyboard(kind: str = "gen") -> InlineKeyboardMarkup:
    if kind == "prop":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Full", callback_data="var:prop:full"),
                InlineKeyboardButton("📩 Founder DM", callback_data="var:prop:founder_dm"),
                InlineKeyboardButton("💬 Short", callback_data="var:prop:short"),
            ],
            [
                InlineKeyboardButton("🐦 X DM", callback_data="var:prop:x_dm"),
                InlineKeyboardButton("📧 Email", callback_data="var:prop:email"),
                InlineKeyboardButton("💼 Job pitch", callback_data="var:prop:job"),
            ],
            [
                InlineKeyboardButton("🗓️ 30-day", callback_data="var:prop:30day"),
                InlineKeyboardButton("🔀 More versions", callback_data="var:shuffle"),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 More variations", callback_data="var:shuffle"),
            InlineKeyboardButton("🎯 Direct", callback_data="var:tone:direct"),
            InlineKeyboardButton("💬 Casual", callback_data="var:tone:casual"),
        ],
        [
            InlineKeyboardButton("🧠 Strategic", callback_data="var:tone:strategic"),
            InlineKeyboardButton("👤 User POV", callback_data="var:pov:user"),
            InlineKeyboardButton("📣 Marketer", callback_data="var:pov:marketer"),
        ],
        [
            InlineKeyboardButton("📩 Dev DM", callback_data="var:fmt:dev_dm"),
            InlineKeyboardButton("🐦 X reply", callback_data="var:fmt:x_reply"),
            InlineKeyboardButton("🤝 Partnerships", callback_data="var:part:ideas"),
        ],
    ])



def _suggest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 More ideas", callback_data="var:sug:more"),
            InlineKeyboardButton("🤝 Partnerships", callback_data="var:sug:partnerships"),
            InlineKeyboardButton("🐦 X", callback_data="var:sug:x"),
        ],
        [
            InlineKeyboardButton("👥 Community", callback_data="var:sug:community"),
            InlineKeyboardButton("🎯 Acquisition", callback_data="var:sug:acquisition"),
            InlineKeyboardButton("💰 $0 Marketing", callback_data="var:sug:zero"),
        ],
        [
            InlineKeyboardButton("🛍️ Product-led", callback_data="var:sug:product"),
            InlineKeyboardButton("📣 Creators", callback_data="var:sug:creators"),
            InlineKeyboardButton("🎮 Campaigns", callback_data="var:sug:campaigns"),
        ],
        [
            InlineKeyboardButton("🔎 Proof deeper", callback_data="var:sug:proof"),
            InlineKeyboardButton("🔄 Adapt", callback_data="var:sug:adapt"),
            InlineKeyboardButton("📋 To proposal", callback_data="var:prop:short"),
        ],
    ])


def _partner_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Ideas", callback_data="var:part:ideas"),
            InlineKeyboardButton("🤝 Projects", callback_data="var:part:project"),
            InlineKeyboardButton("🌐 Ecosystem", callback_data="var:part:ecosystem"),
        ],
        [
            InlineKeyboardButton("🔌 Integrations", callback_data="var:part:integration"),
            InlineKeyboardButton("📣 Creators", callback_data="var:part:creator"),
            InlineKeyboardButton("👥 Community", callback_data="var:part:community"),
        ],
        [
            InlineKeyboardButton("🎙️ Spaces/AMAs", callback_data="var:part:spaces"),
            InlineKeyboardButton("📰 Media", callback_data="var:part:media"),
            InlineKeyboardButton("🎯 Campaigns", callback_data="var:part:campaign"),
        ],
        [
            InlineKeyboardButton("🎯 Best fit", callback_data="var:part:best"),
            InlineKeyboardButton("📩 Pitch them", callback_data="var:part:pitch"),
            InlineKeyboardButton("🗓️ Campaign plan", callback_data="var:part:plan"),
        ],
        [InlineKeyboardButton("🔀 More", callback_data="var:part:ideas")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    db: DB = context.application.bot_data["db"]
    # Auto-lock: first person to /start becomes sole owner (unless ALLOWED_USER_IDS set)
    if not config.ALLOWED_USER_IDS:
        owners = context.application.bot_data.get("owners") or []
        if not owners:
            raw = await db.get_meta("owner_id")
            if raw and raw.isdigit():
                owners = [int(raw)]
            else:
                owners = [u.id]
                await db.set_meta("owner_id", str(u.id))
                log.info("Locked bot to owner_id=%s", u.id)
            context.application.bot_data["owners"] = owners
        if u.id not in owners:
            await update.effective_message.reply_text("Private bot — access denied.")
            return
    elif not allowed(u.id, context.application):
        await update.effective_message.reply_text("Private bot — access denied.")
        return
    await update.effective_message.reply_html(
        "📣 <b>Online.</b>\n"
        f"Access locked to your account (<code>{u.id}</code>).\n\n" + HELP
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await gate(update, context):
        await update.effective_message.reply_html(HELP)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    owners = context.application.bot_data.get("owners") or config.ALLOWED_USER_IDS or []
    await update.effective_message.reply_html(
        "⚙️ <b>Settings</b>\n"
        f"Build: <code>{esc(config.BUILD)}</code>\n"
        f"Groq: {'set' if config.GROQ_API_KEY else '—'} · Groq2: {'set' if config.GROQ_API_KEY_2 else '—'}\n"
        f"OpenRouter: {'set' if config.OPENROUTER_API_KEY else '—'} · OR2: {'set' if config.OPENROUTER_API_KEY_2 else '—'}\n"
        f"Gemini: {'set' if config.GEMINI_API_KEY else '—'} · Cerebras: {'set' if config.CEREBRAS_API_KEY else '—'}\n"
        f"Models: g=<code>{esc(config.GROQ_MODEL)}</code> or=<code>{esc(config.OPENROUTER_MODEL)}</code>\n"
        f"Access: locked to {owners or 'will lock on first /start'}\n"
        f"DB: <code>{esc(config.DATABASE_PATH)}</code>\n"
        f"Last AI error: <code>{esc(getattr(intel, 'LAST_AI_ERROR', '') or 'none')}</code>\n\n"
        "Order: Groq → Groq2 → Cerebras → Gemini → OpenRouter → OR2\n"
        "Use underscores: <code>GROQ_API_KEY_2</code> (not spaces)."
    )


async def _args(update, context, usage: str):
    if not context.args:
        await update.effective_message.reply_html(usage)
        return None
    return list(context.args)


def _handler(name: str, usage: str):
    async def h(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await gate(update, context):
            return
        args = await _args(update, context, usage)
        if args:
            await run_engine(update, context, args, name)
    return h


async def cmd_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    args = await _args(update, context, "Usage: <code>/competition @handle|url …</code>")
    if not args:
        return
    msg = update.effective_message
    status = await msg.reply_text("🔎 Subject + Web3 competitor discovery…")
    parsed = intel.parse_user_input(args)
    sources = await intel.collect_sources(parsed, bot=context.bot)
    text, st, names = await intel.discover_competitors(
        sources, mode="similar", exclude=[], batch_size=6
    )
    sid = secrets.token_hex(8)
    db: DB = context.application.bot_data["db"]
    await db.save_comp_session(sid, update.effective_user.id, sources, "similar", names)
    context.application.bot_data.setdefault("comp", {})[sid] = {
        "sources": sources,
        "mode": "similar",
        "shown": list(names),
        "user_id": update.effective_user.id,
    }
    try:
        await status.delete()
    except Exception:
        pass
    header = (
        f"🏆 <b>COMPETITION</b> (Web3 · tiered)\n"
        f"Sources: {esc(intel.sources_label(sources))}\nAI: {esc(st)}\n"
        f"Batch {len(names)} · session <code>{esc(sid)}</code>\n\n"
    )
    await reply_long(msg, header + (text or ""))
    await msg.reply_html("More / modes:", reply_markup=competition_keyboard(sid))


async def cb_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user:
        return
    if not allowed(update.effective_user.id, context.application):
        await q.answer("Private", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3:
        await q.answer()
        return
    action, sid = parts[1], parts[2]
    mem = (context.application.bot_data.get("comp") or {}).get(sid)
    db: DB = context.application.bot_data["db"]
    if not mem:
        row = await db.get_comp_session(sid)
        if not row:
            await q.answer("Session expired — /competition again", show_alert=True)
            return
        mem = {
            "sources": row["subject"],
            "mode": row["mode"],
            "shown": row["shown"],
            "user_id": row["user_id"],
        }
        context.application.bot_data.setdefault("comp", {})[sid] = mem
    if mem.get("user_id") != update.effective_user.id:
        await q.answer("Not your session", show_alert=True)
        return
    mode = mem.get("mode") or "similar"
    if action != "more":
        mode = action
        mem["mode"] = mode
    await q.answer("Researching…")
    status = await q.message.reply_text(f"🔄 mode={mode}…")
    text, st, names = await intel.discover_competitors(
        mem["sources"], mode=mode, exclude=list(mem.get("shown") or []), batch_size=6,
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
        f"🏆 <b>COMPETITION</b> · {esc(mode)}\n"
        f"AI: {esc(st)} · new={len(names)} · shown={len(shown)}\n\n"
    )
    await reply_long(q.message, header + (text or "No further competitors found."))
    await q.message.reply_html("Continue:", reply_markup=competition_keyboard(sid))


async def cmd_competitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_html(
            "Usage: <code>/competitor https://malaswap.com Uniswap</code>\n"
            "or <code>/competitor @malaswap Uniswap</code>"
        )
        return
    parsed = intel.parse_user_input(list(context.args), competitor_mode=True)
    focus = parsed.competitor_focus or context.args[-1]
    status = await update.effective_message.reply_text("🔎…")
    sources = await intel.collect_sources(parsed, bot=context.bot)
    text, st = await intel.run_competitor_focus(sources, focus)
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(
        update.effective_message,
        f"🎯 <b>COMPETITOR</b> focus={esc(focus)}\nAI: {esc(st)}\n\n{text}",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    raw = " ".join(context.args or [])
    if "|" not in raw:
        await update.effective_message.reply_html(
            "Usage: <code>/compare @a https://a.com | @b https://b.com</code>"
        )
        return
    left, right = raw.split("|", 1)
    status = await update.effective_message.reply_text("🔎 Comparing…")
    sa = await intel.collect_sources(intel.parse_user_input(left.split()), bot=context.bot)
    sb = await intel.collect_sources(intel.parse_user_input(right.split()), bot=context.bot)
    text, st = await intel.run_compare(sa, sb)
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(update.effective_message, f"⚖️ <b>COMPARE</b>\nAI: {esc(st)}\n\n{text}")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    args = await _args(update, context, "Usage: <code>/watch @handle|url</code>")
    if not args:
        return
    key = " ".join(args)[:200]
    await context.application.bot_data["db"].watch(
        update.effective_user.id, key, key, {"input": key}
    )
    await update.effective_message.reply_html(f"⭐ Watching <code>{esc(key)}</code>")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    args = await _args(update, context, "Usage: <code>/unwatch @handle|url</code>")
    if not args:
        return
    await context.application.bot_data["db"].unwatch(
        update.effective_user.id, " ".join(args)[:200]
    )
    await update.effective_message.reply_text("Removed.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    rows = await context.application.bot_data["db"].watchlist(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Empty. /watch @handle")
        return
    await update.effective_message.reply_html(
        "⭐ <b>Watchlist</b>\n" + "\n".join(f"• <code>{esc(r['key'])}</code>" for r in rows[:40])
    )


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await gate(update, context):
        await update.effective_message.reply_text(
            "Watchlist storage is live. Automatic marketing-diff push alerts are staged."
        )


def _session(context: ContextTypes.DEFAULT_TYPE, uid: int) -> dict:
    return context.application.bot_data.setdefault("sessions", {}).setdefault(uid, {})


async def cmd_proposals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    assert msg
    args = list(context.args or [])
    sess = _session(context, update.effective_user.id)
    sources = sess.get("sources")
    if args:
        parsed = intel.parse_user_input(args)
        if any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
            status = await msg.reply_text("🔎 Researching for proposal…")
            sources = await intel.collect_sources(parsed, bot=context.bot)
            sess["sources"] = sources
            try:
                await status.delete()
            except Exception:
                pass
    if not sources:
        await msg.reply_html(
            "Usage: <code>/marketingproposals @handle|url</code>\n"
            "Or run /market on a project first, then /marketingproposals."
        )
        return
    status = await msg.reply_text("✍️ Drafting proposal…")
    text, st = await intel.run_marketing_proposals(sources, style="full")
    sess["last_text"] = text or ""
    sess["last_kind"] = "proposals"
    sess["options"] = intel.extract_options(text or "")
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(
        msg,
        f"📋 <b>MARKETING PROPOSALS</b>\nSources: {esc(intel.sources_label(sources))}\n"
        f"AI: {esc(st)}\n\n{text or ''}",
    )
    await msg.reply_html("Formats:", reply_markup=_action_keyboard("prop"))


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    assert msg
    text_in = " ".join(context.args or []).strip()
    if not text_in and msg.reply_to_message and msg.reply_to_message.text:
        text_in = msg.reply_to_message.text
    if not text_in:
        await msg.reply_html(
            "Usage: <code>/reply your question or paste their message</code>\n"
            "Or reply to a message with /reply"
        )
        return
    sess = _session(context, update.effective_user.id)
    status = await msg.reply_text("💬…")
    out, st = await intel.run_reply_assistant(
        sess.get("sources"),
        text_in,
        prior_options=sess.get("options") or [],
    )
    sess["last_text"] = out or ""
    sess["last_kind"] = "reply"
    opts = intel.extract_options(out or "")
    if opts:
        sess["options"] = (sess.get("options") or []) + opts
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(msg, f"💬 <b>REPLY</b> · AI: {esc(st)}\n\n{out or ''}")
    await msg.reply_html("Refine:", reply_markup=_action_keyboard("gen"))


async def cmd_partnerships(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    assert msg
    args = list(context.args or [])
    sess = _session(context, update.effective_user.id)
    sources = sess.get("sources")
    if args:
        parsed = intel.parse_user_input(args)
        if any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
            status = await msg.reply_text("🔎…")
            sources = await intel.collect_sources(parsed, bot=context.bot)
            sess["sources"] = sources
            try:
                await status.delete()
            except Exception:
                pass
    if not sources:
        await msg.reply_html(
            "Usage: <code>/partnerships @handle|url</code>\n"
            "Or /market first, then /partnerships."
        )
        return
    status = await msg.reply_text("🤝 Partnership ideas…")
    text, st = await intel.run_partnerships(sources, mode="ideas")
    sess["last_text"] = text or ""
    sess["last_kind"] = "partnerships"
    sess["options"] = intel.extract_options(text or "")
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(
        msg,
        f"🤝 <b>PARTNERSHIPS</b>\nSources: {esc(intel.sources_label(sources))}\n"
        f"AI: {esc(st)}\n\n{text or ''}",
    )
    await msg.reply_html("Explore:", reply_markup=_partner_keyboard())


async def cmd_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    assert msg
    sess = _session(context, update.effective_user.id)
    original = " ".join(context.args or []).strip() or sess.get("last_text") or ""
    if not original:
        await msg.reply_text("Nothing to shuffle yet. Run /market, /reply, or /marketingproposals first.")
        return
    status = await msg.reply_text("🔀…")
    out, st = await intel.run_shuffle(
        original,
        sources=sess.get("sources"),
        prior=sess.get("options") or [],
    )
    sess["last_text"] = out or ""
    opts = intel.extract_options(out or "")
    if opts:
        sess["options"] = (sess.get("options") or []) + opts
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(msg, f"🔀 <b>SHUFFLE</b> · AI: {esc(st)}\n\n{out or ''}")
    await msg.reply_html("Again:", reply_markup=_action_keyboard("gen"))


async def cb_variations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user:
        return
    if not allowed(update.effective_user.id, context.application):
        await q.answer("Private", show_alert=True)
        return
    await q.answer()
    parts = q.data.split(":")
    sess = _session(context, update.effective_user.id)
    sources = sess.get("sources")
    last = sess.get("last_text") or ""

    status = await q.message.reply_text("✍️…")
    if parts[1] == "shuffle":
        out, st = await intel.run_shuffle(
            last, sources=sources, prior=sess.get("options") or [],
        )
    elif parts[1] == "prop":
        style = parts[2] if len(parts) > 2 else "full"
        if not sources:
            await status.edit_text("No project in session — /marketingproposals @project first.")
            return
        out, st = await intel.run_marketing_proposals(
            sources, style=style, prior_text=last,
        )
    elif parts[1] == "tone":
        tone = parts[2] if len(parts) > 2 else "direct"
        out, st = await intel.run_reply_assistant(
            sources,
            f"Rewrite into 3–4 {tone} options. Source material:\n{last[:3000]}",
            tone=tone,
            prior_options=sess.get("options") or [],
        )
    elif parts[1] == "pov":
        pov = parts[2] if len(parts) > 2 else "user"
        out, st = await intel.run_reply_assistant(
            sources,
            f"Rewrite from {pov} perspective, 3–4 options:\n{last[:3000]}",
            perspective=pov,
            prior_options=sess.get("options") or [],
        )
    elif parts[1] == "fmt":
        fmt = parts[2] if len(parts) > 2 else "dev_dm"
        out, st = await intel.run_reply_assistant(
            sources,
            f"Turn this into {fmt} format, 3–4 natural options:\n{last[:3000]}",
            mode=fmt,
            prior_options=sess.get("options") or [],
        )
    elif parts[1] == "part":
        mode = parts[2] if len(parts) > 2 else "ideas"
        if not sources:
            await status.edit_text("No project in session — /market or /partnerships @project first.")
            return
        out, st = await intel.run_partnerships(sources, mode=mode, prior=last)
    elif parts[1] == "sug":
        focus = parts[2] if len(parts) > 2 else "more"
        if not sources:
            await status.edit_text("No project in session — /suggestmarketing @project first.")
            return
        out, st = await intel.run_suggest_marketing(sources, focus=focus, prior=last)
    else:
        out, st = "Unknown action", "AI_PROVIDER_ERROR"

    sess["last_text"] = out or ""
    opts = intel.extract_options(out or "")
    if opts:
        sess["options"] = (sess.get("options") or []) + opts
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(q.message, f"✨ AI: {esc(st)}\n\n{out or ''}")
    if parts[1] == "part":
        kb = _partner_keyboard()
    elif parts[1] == "prop":
        kb = _action_keyboard("prop")
    elif parts[1] == "sug":
        kb = _suggest_keyboard()
    else:
        kb = _action_keyboard("gen")
    await q.message.reply_html("Refine:", reply_markup=kb)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Natural-language marketing assistant (no command required)."""
    if not await gate(update, context) or not update.effective_user or not update.message:
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    low = text.lower()
    # Lightweight intent — only engage on marketing-ish asks
    triggers = (
        "reply", "dm", "what can i say", "what should i", "quick idea", "marketing idea",
        "propose", "proposal", "pitch", "how would you", "approach them", "dev ",
        "founder", "shuffle", "make it", "more casual", "more direct", "another angle",
        "short version", "suggest", "what quick",
    )
    if not any(t in low for t in triggers):
        return
    sess = _session(context, update.effective_user.id)
    status = await update.message.reply_text("💬…")
    if "shuffle" in low or "another angle" in low or "more casual" in low:
        out, st = await intel.run_shuffle(
            sess.get("last_text") or text,
            instruction=text,
            sources=sess.get("sources"),
            prior=sess.get("options") or [],
        )
    elif "propos" in low or "pitch" in low or "approach them" in low:
        if sess.get("sources"):
            out, st = await intel.run_marketing_proposals(
                sess["sources"], style="founder_dm", prior_text=sess.get("last_text") or "",
            )
        else:
            out, st = await intel.run_reply_assistant(None, text)
    else:
        out, st = await intel.run_reply_assistant(
            sess.get("sources"),
            text,
            prior_options=sess.get("options") or [],
        )
    sess["last_text"] = out or ""
    opts = intel.extract_options(out or "")
    if opts:
        sess["options"] = (sess.get("options") or []) + opts
    try:
        await status.delete()
    except Exception:
        pass
    await reply_long(update.message, f"💬 AI: {esc(st)}\n\n{out or ''}")
    await update.message.reply_html("Refine:", reply_markup=_action_keyboard("gen"))


async def on_start(app: Application) -> None:
    db = DB(config.DATABASE_PATH)
    await db.connect()
    app.bot_data["db"] = db
    app.bot_data["comp"] = {}
    # Restore locked owner
    if config.ALLOWED_USER_IDS:
        app.bot_data["owners"] = list(config.ALLOWED_USER_IDS)
    else:
        raw = await db.get_meta("owner_id")
        if raw and raw.isdigit():
            app.bot_data["owners"] = [int(raw)]
            log.info("Restored owner lock id=%s", raw)
        else:
            app.bot_data["owners"] = []
    cmds = [
        BotCommand("start", "Start"),
        BotCommand("help", "Help"),
        BotCommand("market", "Marketing intelligence audit"),
        BotCommand("marketingaudit", "Full marketing audit"),
        BotCommand("positioning", "Positioning strategy"),
        BotCommand("competition", "Web3 competitive intelligence"),
        BotCommand("competitor", "Deep-dive one comparable"),
        BotCommand("campaigns", "Campaign concepts"),
        BotCommand("marketingfunnels", "Funnel audit"),
        BotCommand("funnels", "Funnel audit"),
        BotCommand("suggestmarketing", "Marketing ideas"),
        BotCommand("organicmarketing", "Organic growth plan"),
        BotCommand("zeromarketing", "$0 marketing plan"),
        BotCommand("marketgaps", "Gaps → actions"),
        BotCommand("opportunities", "Growth opportunities"),
        BotCommand("compare", "Compare two projects"),
        BotCommand("report", "Full strategy report"),
        BotCommand("fullpack", "Full strategy pack"),
        BotCommand("marketingproposals", "Proposal for a project"),
        BotCommand("reply", "Marketing reply / what to say"),
        BotCommand("marketingreply", "Marketing reply assistant"),
        BotCommand("shuffle", "Human variations of last answer"),
        BotCommand("partnerships", "Partnership & collab ideas"),
        BotCommand("watch", "Watch subject"),
        BotCommand("watchlist", "List watches"),
        BotCommand("unwatch", "Unwatch"),
        BotCommand("settings", "Keys + build"),
    ]
    try:
        await app.bot.set_my_commands(cmds)
    except Exception as exc:
        log.warning("set_my_commands: %s", exc)
    me = await app.bot.get_me()
    log.info("Online @%s build=%s", me.username, config.BUILD)


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
    usage = "Add @handle and/or website URL after the command."
    aliases = {
        "market": "market",
        "marketingaudit": "marketingaudit",
        "marketing": "market",
        "positioning": "positioning",
        "campaigns": "campaigns",
        "marketgaps": "marketgaps",
        "opportunities": "opportunities",
        "report": "report",
        "fullpack": "fullpack",
        "funnels": "funnels",
        "marketingfunnels": "marketingfunnels",
        "marketingfunnel": "funnels",
        "suggestmarketing": "suggestmarketing",
        "marketingideas": "marketingideas",
        "organicmarketing": "organicmarketing",
        "zeromarketing": "zeromarketing",
        "0marketing": "0marketing",
        "freegrowth": "zeromarketing",
    }
    for cmd_name, engine in aliases.items():
        app.add_handler(
            CommandHandler(
                cmd_name,
                _handler(engine, f"Usage: <code>/{cmd_name} @handle|url …</code>\n{usage}"),
            )
        )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("competition", cmd_competition))
    app.add_handler(CommandHandler("competitor", cmd_competitor))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("marketingproposals", cmd_proposals))
    app.add_handler(CommandHandler("proposals", cmd_proposals))
    app.add_handler(CommandHandler("reply", cmd_reply))
    app.add_handler(CommandHandler("marketingreply", cmd_reply))
    app.add_handler(CommandHandler("suggestreply", cmd_reply))
    app.add_handler(CommandHandler("shuffle", cmd_shuffle))
    app.add_handler(CommandHandler("variations", cmd_shuffle))
    app.add_handler(CommandHandler("partnerships", cmd_partnerships))
    app.add_handler(CommandHandler("partnership", cmd_partnerships))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CallbackQueryHandler(cb_competition, pattern=r"^cm:"))
    app.add_handler(CallbackQueryHandler(cb_variations, pattern=r"^var:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Polling %s", config.BUILD)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
