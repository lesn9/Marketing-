"""
📣 Web3 Marketing + Competition Intelligence Bot
4-file architecture: main.py · config.py · intelligence.py · database.py
"""
from __future__ import annotations

import html
import re
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
        log.warning(
            "Access denied uid=%s owners=%s env=%s",
            u.id,
            app.bot_data.get("owners") if app else None,
            config.ALLOWED_USER_IDS,
        )
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Private bot — access denied (your id={u.id}).\n"
                "Set Railway ALLOWED_USER_IDS to your Telegram id, or CLAIM_OWNER=1 then /start."
            )
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




# ---------------------------------------------------------------------------
# Telegram UI — one message, edit in place, page navigation
# ---------------------------------------------------------------------------

DASHBOARD_CMDS = {
    "market", "marketingaudit", "positioning", "campaigns", "marketgaps",
    "opportunities", "funnels", "marketingfunnels", "suggestmarketing",
    "marketingideas", "organicmarketing", "zeromarketing", "0marketing",
    "marketingproposals", "proposals", "report", "fullpack", "partnerships",
    "spaces", "ama", "competition",
}


def split_pages(text: str, max_len: int = 2800) -> list[str]:
    """Split long output into navigable pages at section boundaries."""
    text = (text or "").strip()
    if not text:
        return ["(empty)"]
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"\n(?=(?:⚡|🔎|🎯|🚀|👉|📣|📊|🏆|💡|⚠️|📅|🤝|🎙️|🎤|📈|🪙|🌱|📋|📩|💼))", text)
    pages: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not buf:
            buf = part
        elif len(buf) + 2 + len(part) <= max_len:
            buf = buf + "\n\n" + part
        else:
            pages.append(buf)
            buf = part
    if buf:
        pages.append(buf)
    final: list[str] = []
    for pg in pages:
        while len(pg) > max_len:
            cut = pg.rfind("\n", 0, max_len)
            if cut < 400:
                cut = max_len
            final.append(pg[:cut].strip())
            pg = pg[cut:].strip()
        if pg:
            final.append(pg)
    return final or [text[:max_len]]


def ui_keyboard(
    *,
    page: int,
    total: int,
    kind: str,
    show_examples: bool = True,
    nav_page: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Back", callback_data=f"ui:back:{kind}"))
    if total > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{total}", callback_data="ui:noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"ui:next:{kind}"))
    if nav:
        rows.append(nav)

    tools: list[InlineKeyboardButton] = []
    if show_examples and kind not in ("reply",):
        tools.append(InlineKeyboardButton("💡 Examples", callback_data=f"ui:examples:{kind}"))
    tools.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"ui:refresh:{kind}"))
    rows.append(tools)

    # Command navigation — compact categories
    if kind != "reply":
        if nav_page == 0:
            rows.append([
                InlineKeyboardButton("📊 Audit", callback_data="ui:cmd:marketingaudit"),
                InlineKeyboardButton("🎯 Positioning", callback_data="ui:cmd:positioning"),
                InlineKeyboardButton("🏆 Competition", callback_data="ui:cmd:competition"),
            ])
            rows.append([
                InlineKeyboardButton("💡 Ideas", callback_data="ui:cmd:suggestmarketing"),
                InlineKeyboardButton("🌱 Organic", callback_data="ui:cmd:organicmarketing"),
                InlineKeyboardButton("📋 Proposal", callback_data="ui:cmd:marketingproposals"),
            ])
            rows.append([InlineKeyboardButton("▶️ More", callback_data="ui:nav:1")])
        else:
            rows.append([
                InlineKeyboardButton("📈 Funnel", callback_data="ui:cmd:funnels"),
                InlineKeyboardButton("🚀 Campaigns", callback_data="ui:cmd:campaigns"),
                InlineKeyboardButton("🤝 Partners", callback_data="ui:cmd:partnerships"),
            ])
            rows.append([
                InlineKeyboardButton("🔎 Gaps", callback_data="ui:cmd:marketgaps"),
                InlineKeyboardButton("⚡ Opps", callback_data="ui:cmd:opportunities"),
                InlineKeyboardButton("🪙 $0", callback_data="ui:cmd:zeromarketing"),
            ])
            rows.append([InlineKeyboardButton("◀️ Commands", callback_data="ui:nav:0")])

    # Format buttons for proposals
    if kind in ("marketingproposals", "proposals"):
        rows.insert(0, [
            InlineKeyboardButton("📩 Dev DM", callback_data="var:prop:founder_dm"),
            InlineKeyboardButton("💼 Job pitch", callback_data="var:prop:job"),
            InlineKeyboardButton("🎯 Short", callback_data="var:prop:short"),
        ])
        rows.insert(1, [
            InlineKeyboardButton("📋 Full", callback_data="var:prop:full"),
            InlineKeyboardButton("📅 30-day", callback_data="var:prop:30day"),
            InlineKeyboardButton("🐦 X DM", callback_data="var:prop:x_dm"),
        ])

    return InlineKeyboardMarkup(rows)


async def deliver_ui(
    msg,
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    *,
    kind: str,
    title: str,
    body: str,
    sources=None,
    edit_message=None,
    show_examples: bool = True,
) -> None:
    """ONE Telegram message only. Pagination via Back/Next edits the same message."""
    # Strip markdown tables for Telegram readability
    clean = body or ""
    if "|" in clean and "---" in clean:
        lines = []
        for ln in clean.splitlines():
            s = ln.strip()
            if s.startswith("|") or (s and set(s) <= set("|-: ")):
                continue
            lines.append(ln)
        clean = "\n".join(lines)
    clean = intel.scrub_internal(clean)

    pages = split_pages(clean, max_len=3200)
    sessions = context.application.bot_data.setdefault("sessions", {})
    sess = sessions.setdefault(uid, {})
    if sources is not None:
        sess["sources"] = sources
    sess["last_kind"] = kind
    sess["last_text"] = clean
    sess["pages"] = pages
    sess["page"] = 0
    sess["nav_page"] = 0
    sess["title"] = title
    sess["options"] = intel.extract_options(clean)

    page0 = pages[0]
    if len(pages) > 1:
        header = f"{title}\n<i>Page 1/{len(pages)} — use Next</i>\n\n" if title else f"<i>Page 1/{len(pages)}</i>\n\n"
    else:
        header = f"{title}\n\n" if title else ""
    text = (header + page0)[:4090]
    # Competition uses category keyboard; others use ui_keyboard
    if kind == "competition" and sess.get("comp_sid"):
        kb = competition_keyboard(sess["comp_sid"])
    else:
        kb = ui_keyboard(page=0, total=len(pages), kind=kind, show_examples=show_examples)

    if edit_message is not None:
        try:
            await edit_message.edit_text(
                text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True
            )
            sess["msg_id"] = edit_message.message_id
            sess["chat_id"] = edit_message.chat_id
            return
        except Exception as exc:
            log.warning("edit failed: %s", exc)

    # Single send only — never reply_long flood
    sent = await msg.reply_html(text, reply_markup=kb, disable_web_page_preview=True)
    sess["msg_id"] = sent.message_id
    sess["chat_id"] = sent.chat_id



def competition_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Category modes — each mode should return different projects."""
    return InlineKeyboardMarkup([
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
        [
            InlineKeyboardButton("➕ More", callback_data=f"cm:more:{session_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh:competition"),
        ],
        [
            InlineKeyboardButton("📊 Audit", callback_data="ui:cmd:marketingaudit"),
            InlineKeyboardButton("📋 Proposal", callback_data="ui:cmd:marketingproposals"),
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

    text = intel.scrub_internal(text or "")
    uid = update.effective_user.id if update.effective_user else 0
    title = f"📣 <b>{esc(name.upper())}</b>"
    await deliver_ui(
        msg,
        context,
        uid,
        kind=name,
        title=title,
        body=text,
        sources=sources,
        show_examples=name not in ("reply",),
    )
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
            InlineKeyboardButton("🔀 More", callback_data="var:shuffle"),
            InlineKeyboardButton("📩 Founder DM", callback_data="var:prop:founder_dm"),
            InlineKeyboardButton("💼 Job pitch", callback_data="var:prop:job"),
        ],
    ])


def _reply_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Reply", callback_data="var:fmt:reply"),
            InlineKeyboardButton("🐦 X Reply", callback_data="var:fmt:x_reply"),
            InlineKeyboardButton("👥 Community", callback_data="var:fmt:community"),
        ],
        [
            InlineKeyboardButton("📩 Dev DM", callback_data="var:fmt:dev_dm"),
            InlineKeyboardButton("📣 Observation", callback_data="var:fmt:observation"),
            InlineKeyboardButton("🔀 More", callback_data="var:shuffle"),
        ],
    ])


def _report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Overview", callback_data="var:rep:overview"),
            InlineKeyboardButton("🎯 Positioning", callback_data="var:rep:positioning"),
            InlineKeyboardButton("📣 Content", callback_data="var:rep:content"),
        ],
        [
            InlineKeyboardButton("👥 Community", callback_data="var:rep:community"),
            InlineKeyboardButton("🤝 Partners", callback_data="var:rep:partners"),
            InlineKeyboardButton("🚀 Opportunities", callback_data="var:rep:opportunities"),
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
    try:
        # Env allowlist wins
        if config.ALLOWED_USER_IDS:
            if u.id not in config.ALLOWED_USER_IDS:
                await update.effective_message.reply_text(
                    f"Private bot — your id {u.id} is not in ALLOWED_USER_IDS."
                )
                return
            context.application.bot_data["owners"] = list(config.ALLOWED_USER_IDS)
        else:
            # Auto-lock: claim ownership on /start if unlocked or same owner
            raw = await db.get_meta("owner_id")
            owners = context.application.bot_data.get("owners") or []
            if raw and raw.isdigit() and not owners:
                owners = [int(raw)]
            if not owners:
                owners = [u.id]
                await db.set_meta("owner_id", str(u.id))
                log.info("Locked bot to owner_id=%s", u.id)
            elif u.id not in owners:
                # Allow reclaim if only one owner slot and user sets CLAIM_OWNER=1
                claim = (config.env("CLAIM_OWNER") if hasattr(config, "env") else "") or __import__("os").getenv("CLAIM_OWNER", "")
                if str(claim).strip() in ("1", "true", "yes"):
                    owners = [u.id]
                    await db.set_meta("owner_id", str(u.id))
                    log.info("Owner reclaimed by uid=%s", u.id)
                else:
                    await update.effective_message.reply_text(
                        f"Private bot — locked to {owners}. Your id={u.id}.\n"
                        "Set ALLOWED_USER_IDS={your_id} or CLAIM_OWNER=1 on Railway to reclaim."
                    )
                    return
            context.application.bot_data["owners"] = owners

        await update.effective_message.reply_html(
            "📣 <b>Online.</b>\n"
            f"Your id: <code>{u.id}</code>\n"
            f"Access: {context.application.bot_data.get('owners')}\n\n" + HELP
        )
    except Exception as exc:
        log.exception("cmd_start failed")
        try:
            await update.effective_message.reply_text(f"Start error: {exc}")
        except Exception:
            pass


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await gate(update, context):
        await update.effective_message.reply_html(HELP)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    owners = context.application.bot_data.get("owners") or config.ALLOWED_USER_IDS or []
    attempts = getattr(intel, "AI_ATTEMPTS", None) or []
    att_line = " · ".join(attempts[-6:]) if attempts else "none yet"
    await update.effective_message.reply_html(
        "⚙️ <b>Settings</b>\n"
        f"Build: <code>{esc(config.BUILD)}</code>\n"
        f"Groq: {'set' if config.GROQ_API_KEY else '—'} model=<code>{esc(config.GROQ_MODEL)}</code>\n"
        f"Groq2: {'set' if config.GROQ_API_KEY_2 else '—'} "
        f"model=<code>{esc(config.GROQ_MODEL_2 or config.GROQ_MODEL)}</code>\n"
        f"Gemini: {'set' if config.GEMINI_API_KEY else '—'} model=<code>{esc(config.GEMINI_MODEL)}</code>\n"
        f"Cerebras: {'set' if config.CEREBRAS_API_KEY else '—'} model=<code>{esc(config.CEREBRAS_MODEL)}</code>\n"
        f"OpenRouter: {'set' if config.OPENROUTER_API_KEY else '—'} "
        f"OR2: {'set' if config.OPENROUTER_API_KEY_2 else '—'}\n"
        f"OR model: <code>{esc(config.OPENROUTER_MODEL)}</code>\n"
        f"Access: locked to {owners or 'will lock on first /start'}\n"
        f"DB: <code>{esc(config.DATABASE_PATH)}</code>\n"
        f"Last AI error: <code>{esc(getattr(intel, 'LAST_AI_ERROR', '') or 'none')}</code>\n"
        f"Last attempts: <code>{esc(att_line)}</code>\n\n"
        "Router: Groq → Groq2 → Gemini → Cerebras → OpenRouter → OR2\n"
        "Each provider uses <b>only</b> its Railway-configured model (no hidden fallbacks)."
    )


async def _args(update, context, usage: str):
    if not context.args:
        await update.effective_message.reply_html(usage)
        return None
    return list(context.args)


def _handler(name: str, usage: str):
    async def h(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            log.info("cmd /%s from %s args=%s", name, update.effective_user and update.effective_user.id, context.args)
            if not await gate(update, context):
                return
            args = await _args(update, context, usage)
            if args:
                await run_engine(update, context, args, name)
        except Exception as exc:
            log.exception("handler /%s failed", name)
            if update.effective_message:
                await update.effective_message.reply_text(f"Error in /{name}: {exc}")
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
    text = intel.scrub_internal(text or "")
    sessions = context.application.bot_data.setdefault("sessions", {})
    sess = sessions.setdefault(update.effective_user.id, {})
    sess["sources"] = sources
    sess["shown"] = list(names)
    sess["comp_sid"] = sid
    await deliver_ui(
        msg,
        context,
        update.effective_user.id,
        kind="competition",
        title="🏆 <b>COMPETITION</b>",
        body=text,
        sources=sources,
    )


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
    text, st, names = await intel.discover_competitors(
        mem["sources"], mode=mode, exclude=list(mem.get("shown") or []), batch_size=6,
    )
    shown = list(mem.get("shown") or [])
    for n in names:
        if n not in shown:
            shown.append(n)
    mem["shown"] = shown
    await db.save_comp_session(sid, update.effective_user.id, mem["sources"], mode, shown)
    text = intel.scrub_internal(text or "No further competitors found.")
    sessions = context.application.bot_data.setdefault("sessions", {})
    sess = sessions.setdefault(update.effective_user.id, {})
    sess["sources"] = mem["sources"]
    sess["shown"] = shown
    sess["comp_sid"] = sid
    await deliver_ui(
        q.message,
        context,
        update.effective_user.id,
        kind="competition",
        title="🏆 <b>COMPETITION</b>",
        body=text,
        sources=mem["sources"],
        edit_message=q.message,
    )


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
        f"🎯 <b>COMPETITOR</b> focus={esc(focus)}\n\n{text}",
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
    await reply_long(update.effective_message, f"⚖️ <b>COMPARE</b>\n\n{text}")


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
        f"📋 <b>MARKETING PROPOSALS</b>\nSources: {esc(intel.sources_label(sources))}\n\n{text or ''}",
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
    body = f"💬 <b>REPLY</b>\n\n{out or ''}"
    if len(body) <= 4000:
        sent = await msg.reply_html(body, reply_markup=_reply_keyboard())
    else:
        await reply_long(msg, body)
        sent = await msg.reply_html("Options:", reply_markup=_reply_keyboard())
    sess["msg_id"] = sent.message_id
    sess["chat_id"] = sent.chat_id
    sess["last_text"] = out or ""
    sess["last_kind"] = "reply"
    sess["user_request"] = text_in


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
        f"\n{text or ''}",
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
    await reply_long(msg, f"🔀 <b>SHUFFLE</b>\n\n{out or ''}")
    await msg.reply_html("Again:", reply_markup=_action_keyboard("gen"))



async def cb_ui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unified Back/Next/Examples/Refresh/Command navigation — always edit message."""
    q = update.callback_query
    if not q or not q.data or not update.effective_user:
        return
    await q.answer()
    uid = update.effective_user.id
    sess = _session(context, uid)
    parts = q.data.split(":")
    # ui:back:kind | ui:next:kind | ui:examples:kind | ui:refresh:kind | ui:cmd:name | ui:nav:N | ui:noop
    action = parts[1] if len(parts) > 1 else "noop"
    kind = parts[2] if len(parts) > 2 else sess.get("last_kind") or "market"

    if action == "noop":
        return

    if action == "nav":
        sess["nav_page"] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        pages = sess.get("pages") or [sess.get("last_text") or ""]
        page = int(sess.get("page") or 0)
        page = max(0, min(page, len(pages) - 1))
        title = sess.get("title") or ""
        header = f"{title}\nPage {page + 1}/{len(pages)}\n" if len(pages) > 1 else (title + "\n" if title else "")
        text = header + pages[page]
        kb = ui_keyboard(
            page=page, total=len(pages), kind=sess.get("last_kind") or kind,
            nav_page=sess.get("nav_page") or 0,
        )
        try:
            await q.message.edit_text(text[:4096], parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        except Exception as exc:
            log.warning("nav edit: %s", exc)
        return

    if action in ("back", "next"):
        pages = sess.get("pages") or [sess.get("last_text") or ""]
        page = int(sess.get("page") or 0)
        if action == "back":
            page = max(0, page - 1)
        else:
            page = min(len(pages) - 1, page + 1)
        sess["page"] = page
        title = sess.get("title") or ""
        header = f"{title}\nPage {page + 1}/{len(pages)}\n" if len(pages) > 1 else (title + "\n" if title else "")
        text = header + pages[page]
        kb = ui_keyboard(
            page=page, total=len(pages), kind=sess.get("last_kind") or kind,
            nav_page=sess.get("nav_page") or 0,
        )
        try:
            await q.message.edit_text(text[:4096], parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        except Exception as exc:
            log.warning("page edit: %s", exc)
        return

    if action == "examples":
        sources = sess.get("sources")
        last = sess.get("last_text") or ""
        prompt_kind = kind or sess.get("last_kind") or "market"
        out, st = await intel.complete_fast(
            f"""{intel.HUMAN_VOICE}
Generate 💡 EXAMPLES for this project — real execution examples only.
For each example: What / How / Who / Where / Example outreach or campaign structure /
Real project + website if known (label ⚠️ if inferred) / What THIS project can adapt.
3 examples max. No thinking process. No generic "use KOLs" without names/structure.
PROJECT CONTEXT:
{intel.evidence_brief(sources) if sources else last[:2500]}
COMMAND CONTEXT: {prompt_kind}
""",
            max_tokens=1600,
        )
        out = intel.scrub_internal(out or "No examples available.")
        title = f"💡 <b>EXAMPLES</b> · {esc(prompt_kind)}"
        await deliver_ui(
            q.message, context, uid, kind=prompt_kind, title=title, body=out,
            sources=sources, edit_message=q.message, show_examples=False,
        )
        return

    if action == "refresh":
        sources = sess.get("sources")
        if not sources:
            await q.message.reply_text("Nothing to refresh — run a command with a project first.")
            return
        cmd = sess.get("last_kind") or "marketingaudit"
        # Re-collect if we have parseable sites/handles
        try:
            parsed = intel.ParsedInput()
            for w in (sources.get("websites") or []):
                if isinstance(w, dict) and w.get("url"):
                    parsed.websites.append(w["url"])
            for x in (sources.get("x") or []):
                if isinstance(x, dict) and x.get("handle"):
                    parsed.x_handles.append("@" + str(x["handle"]).lstrip("@"))
            if parsed.websites or parsed.x_handles:
                sources = await intel.collect_sources(parsed, bot=context.bot)
        except Exception as exc:
            log.warning("refresh collect: %s", exc)
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
        if cmd in ("marketingproposals", "proposals"):
            text, st = await intel.run_marketing_proposals(sources, style="full")
        elif cmd == "competition":
            text, st, names = await intel.discover_competitors(sources, mode="similar", exclude=sess.get("shown") or [])
            sess.setdefault("shown", [])
            for n in names:
                if n not in sess["shown"]:
                    sess["shown"].append(n)
        else:
            fn = runners.get(cmd, intel.run_marketing_audit)
            text, st = await fn(sources)
        text = intel.scrub_internal(text or "")
        title = f"📣 <b>{esc(cmd.upper())}</b> · refreshed"
        await deliver_ui(
            q.message, context, uid, kind=cmd, title=title, body=text,
            sources=sources, edit_message=q.message,
        )
        return

    if action == "cmd":
        cmd = parts[2] if len(parts) > 2 else "marketingaudit"
        sources = sess.get("sources")
        if not sources:
            await q.answer("Run a project command first", show_alert=True)
            return
        # Switch command using same research
        runners = {
            "marketingaudit": intel.run_marketing_audit,
            "positioning": intel.run_positioning,
            "campaigns": intel.run_campaigns,
            "marketgaps": intel.run_marketgaps,
            "opportunities": intel.run_opportunities,
            "funnels": intel.run_funnels,
            "suggestmarketing": intel.run_suggest_marketing,
            "organicmarketing": intel.run_organic,
            "zeromarketing": intel.run_zero,
            "marketingproposals": lambda s: intel.run_marketing_proposals(s, style="full"),
            "partnerships": getattr(intel, "run_partnerships", intel.run_suggest_marketing),
        }
        if cmd == "competition":
            text, st, names = await intel.discover_competitors(sources, mode="similar", exclude=sess.get("shown") or [])
            sess.setdefault("shown", [])
            for n in names:
                if n not in sess["shown"]:
                    sess["shown"].append(n)
            text = intel.scrub_internal(text or "")
            title = "🏆 <b>COMPETITION</b>"
            await deliver_ui(
                q.message, context, uid, kind="competition", title=title, body=text,
                sources=sources, edit_message=q.message,
            )
            return
        fn = runners.get(cmd)
        if not fn:
            await q.answer("Unknown", show_alert=True)
            return
        result = await fn(sources)
        text, st = result[0], result[1]
        text = intel.scrub_internal(text or "")
        title = f"📣 <b>{esc(cmd.upper())}</b>"
        await deliver_ui(
            q.message, context, uid, kind=cmd, title=title, body=text,
            sources=sources, edit_message=q.message,
        )
        return



async def cb_variations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user:
        return
    if not allowed(update.effective_user.id, context.application):
        await q.answer("Private", show_alert=True)
        return
    await q.answer("Updating…")
    parts = q.data.split(":")
    sess = _session(context, update.effective_user.id)
    sources = sess.get("sources")
    last = sess.get("last_text") or ""
    status = None
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
        fmt = parts[2] if len(parts) > 2 else "reply"
        req = sess.get("user_request") or last or "Write 2–3 short options."
        out, st = await intel.run_reply_assistant(
            sources,
            req,
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
    if status:
        try:
            await status.delete()
        except Exception:
            pass
    # EDIT existing message — do not flood chat with new messages
    title = {
        "shuffle": "🔀",
        "prop": "📋",
        "tone": "🎯",
        "pov": "👤",
        "fmt": "💬",
        "part": "🤝",
        "sug": "💡",
        "rep": "📊",
    }.get(parts[1], "✨")
    body = f"{title}\n\n{out or ''}"
    if parts[1] == "part":
        kb = _partner_keyboard()
    elif parts[1] == "prop":
        kb = _action_keyboard("prop")
    elif parts[1] == "sug":
        kb = _suggest_keyboard()
    elif parts[1] == "fmt" or sess.get("last_kind") == "reply":
        kb = _reply_keyboard()
    elif parts[1] == "rep":
        kb = _report_keyboard()
    else:
        kb = _reply_keyboard() if sess.get("last_kind") == "reply" else _action_keyboard("prop")

    try:
        if len(body) <= 4096:
            await q.message.edit_text(body, parse_mode="HTML", reply_markup=kb)
        else:
            await q.message.edit_text(body[:4000] + "…", parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        log.warning("edit_text failed: %s — fallback reply", exc)
        await q.message.reply_html(body[:4000], reply_markup=kb)


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
    await reply_long(update.message, f"💬\n\n{out or ''}")
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
    app.add_handler(CallbackQueryHandler(cb_ui, pattern=r"^ui:"))
    app.add_handler(CallbackQueryHandler(cb_variations, pattern=r"^var:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Unhandled error: %s", context.error)
        try:
            if isinstance(update, Update) and update.effective_message:
                await update.effective_message.reply_text(
                    f"Internal error: {context.error}"
                )
        except Exception:
            pass

    app.add_error_handler(on_error)

    log.info("Polling %s", config.BUILD)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
