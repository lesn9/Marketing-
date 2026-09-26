"""Web3 Marketing + Competition Intelligence Telegram bot.

The UI deliberately uses one Telegram message per research session. Navigation edits that
message instead of posting a new page, so long research stays readable on mobile.
"""
from __future__ import annotations

import html
import logging
import secrets
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import config
import intelligence as intel
from database import DB

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("mkt")

# These commands intentionally do not get the generic command-navigation row.
COMMAND_NAV_EXCLUDED = {
    "reply", "settings", "start", "fullback", "report", "marketingreply",
    "unwatch", "shuffle", "help",
}

PAGE_LIMIT = 2200

HELP = """📣 <b>Marketing + Competition Intelligence</b>

Research first. Understand the project. Then give specific marketing actions and examples.

<b>Research</b>
/market · /marketingaudit · /positioning · /competition · /competitor
/campaigns · /funnels · /suggestmarketing · /organicmarketing · /zeromarketing
/marketgaps · /opportunities · /compare · /report · /fullpack

<b>Proposals & replies</b>
/marketingproposals · /reply · /marketingreply · /shuffle

<b>Monitoring</b>
/watch · /watchlist · /alerts · /settings

You can also ask naturally: “give me a quick marketing idea for this project” or “turn that into a founder DM”."""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def clean_ai_text(text: str | None) -> str:
    """Strip common AI/debug/markdown rubbish before anything reaches Telegram."""
    if not text:
        return ""
    banned_fragments = (
        "AI shortlist + best-effort site checks",
        "AI shortlist",
        "best-effort site checks",
        "User Safety:",
        "session ",
        "Batch ",
        "chain-of-thought",
        "internal reasoning",
        "quality gate",
        "research engine",
        "tool output",
        "model routing",
        "debug",
    )
    lines: list[str] = []
    in_code = False
    for raw in text.replace("\r", "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if any(x.lower() in line.lower() for x in banned_fragments):
            continue
        # Remove markdown table separators and excessive markdown decoration.
        if line.startswith("|---") or line.startswith("| ---"):
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "")
        while line.startswith("###"):
            line = line[3:].strip()
        if line.startswith("##"):
            line = line[2:].strip()
        if line:
            lines.append(line)
    # Collapse more than one blank line.
    out: list[str] = []
    blanks = 0
    for line in lines:
        if not line:
            blanks += 1
            if blanks <= 1:
                out.append("")
        else:
            blanks = 0
            out.append(line)
    return "\n".join(out).strip()


def split_pages(text: str, limit: int = PAGE_LIMIT) -> list[str]:
    """Split a response into readable Telegram pages without sending them all at once."""
    text = clean_ai_text(text)
    if not text:
        return ["Nothing to show yet."]
    paragraphs = text.split("\n\n")
    pages: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pages.append(current)
            current = ""
        # A single very long paragraph gets line-wrapped into chunks.
        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut < 800:
                cut = paragraph.rfind(" ", 0, limit)
            if cut < 400:
                cut = limit
            pages.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        current = paragraph
    if current:
        pages.append(current)
    return pages or ["Nothing to show yet."]


def render_html(title: str, page: str, page_no: int, total: int) -> str:
    marker = f"\n\nPage {page_no + 1}/{total}" if total > 1 else ""
    # Telegram makes <pre> blocks easy to copy. Use it for proposal/outreach pages.
    copyable = any(k in title.upper() for k in ("PROPOSAL", "DEV DM", "X DM", "JOB PITCH"))
    body = f"<pre>{esc(page)}</pre>" if copyable else esc(page)
    return f"<b>{esc(title)}</b>\n\n{body}{marker}"


def allowed(uid: int, app: Application | None = None) -> bool:
    if config.ALLOWED_USER_IDS:
        return uid in config.ALLOWED_USER_IDS
    owners = (app.bot_data.get("owners") if app else None) or []
    return not owners or uid in owners


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    u = update.effective_user
    if not u:
        return False
    if not allowed(u.id, context.application):
        if update.effective_message:
            await update.effective_message.reply_text("Private bot — access denied.")
        return False
    return True


def session(context: ContextTypes.DEFAULT_TYPE, uid: int) -> dict[str, Any]:
    return context.application.bot_data.setdefault("sessions", {}).setdefault(uid, {})


def command_pages() -> list[list[tuple[str, str]]]:
    return [
        [
            ("📣 Marketing audit", "market"), ("🎯 Positioning", "positioning"),
            ("📣 Campaigns", "campaigns"), ("🧩 Funnel", "funnels"),
            ("💡 Marketing ideas", "suggestmarketing"), ("🌱 Organic", "organicmarketing"),
        ],
        [
            ("💰 $0 marketing", "zeromarketing"), ("🕳️ Market gaps", "marketgaps"),
            ("🚀 Opportunities", "opportunities"), ("🏆 Competition", "competition"),
            ("🔎 Competitor", "competitor"), ("⚖️ Compare", "compare"),
        ],
        [
            ("📋 Proposals", "marketingproposals"), ("🤝 Partnerships", "partnerships"),
            ("📦 Full pack", "fullpack"), ("👀 Watchlist", "watchlist"),
            ("🚨 Alerts", "alerts"),
        ],
    ]


def generic_keyboard(state: dict[str, Any]) -> InlineKeyboardMarkup:
    page = int(state.get("page", 0))
    total = len(state.get("pages") or [""])
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Back", callback_data="ui:prev"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("▶️ Next", callback_data="ui:next"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("💡 Examples", callback_data="ui:examples"),
        InlineKeyboardButton("🧭 Commands", callback_data="ui:commands:0"),
    ])
    if state.get("view") == "examples":
        rows.append([InlineKeyboardButton("↩️ Main", callback_data="ui:main")])
    return InlineKeyboardMarkup(rows)


def report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Overview", callback_data="reportview:overview"), InlineKeyboardButton("🎯 Positioning", callback_data="reportview:positioning")],
        [InlineKeyboardButton("📣 Content", callback_data="reportview:content"), InlineKeyboardButton("💬 Community", callback_data="reportview:community")],
        [InlineKeyboardButton("📈 Acquisition", callback_data="reportview:acquisition"), InlineKeyboardButton("🤝 Partnerships", callback_data="reportview:partnerships")],
        [InlineKeyboardButton("🏆 Competition", callback_data="reportview:competition"), InlineKeyboardButton("🚀 Opportunities", callback_data="reportview:opportunities")],
        [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])


def command_menu_keyboard(page: int) -> InlineKeyboardMarkup:
    groups = command_pages()
    page = max(0, min(page, len(groups) - 1))
    rows: list[list[InlineKeyboardButton]] = []
    group = groups[page]
    for i in range(0, len(group), 2):
        rows.append([
            InlineKeyboardButton(group[i][0], callback_data=f"cmd:{group[i][1]}"),
            *([InlineKeyboardButton(group[i + 1][0], callback_data=f"cmd:{group[i + 1][1]}")] if i + 1 < len(group) else []),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Back", callback_data=f"ui:commands:{page-1}"))
    if page < len(groups) - 1:
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"ui:commands:{page+1}"))
    nav.append(InlineKeyboardButton("↩️ Return", callback_data="ui:main"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def competition_keyboard(sid: str, page: int = 0, total: int = 1) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🎯 Similar products", callback_data=f"cmp:similar:{sid}"),
            InlineKeyboardButton("🏗️ Architecture", callback_data=f"cmp:architecture:{sid}"),
        ],
        [
            InlineKeyboardButton("🐦 Social leaders", callback_data=f"cmp:social:{sid}"),
            InlineKeyboardButton("📣 Marketing", callback_data=f"cmp:marketing:{sid}"),
        ],
        [
            InlineKeyboardButton("🧠 UX leaders", callback_data=f"cmp:ux:{sid}"),
            InlineKeyboardButton("💬 Community", callback_data=f"cmp:community:{sid}"),
        ],
        [
            InlineKeyboardButton("🚀 Growth", callback_data=f"cmp:growth:{sid}"),
            InlineKeyboardButton("📈 Same-stage", callback_data=f"cmp:samestage:{sid}"),
        ],
        [
            InlineKeyboardButton("🟣 Same-level", callback_data=f"cmp:samelevel:{sid}"),
            InlineKeyboardButton("➕ More", callback_data=f"cmp:more:{sid}"),
        ],
        [
            InlineKeyboardButton("💡 Examples", callback_data=f"cmp:examples:{sid}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"cmp:refresh:{sid}"),
        ],
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Back", callback_data=f"cmp:prev:{sid}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"cmp:next:{sid}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def edit_state(message, state: dict[str, Any]) -> None:
    pages = state.get("pages") or ["Nothing to show yet."]
    page = max(0, min(int(state.get("page", 0)), len(pages) - 1))
    state["page"] = page
    title = state.get("title", "Marketing Intelligence")
    kb = state.get("special_keyboard") or generic_keyboard(state)
    await message.edit_text(
        render_html(title, pages[page], page, len(pages)),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb,
    )


async def make_state(
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    *,
    title: str,
    command: str,
    args: list[str],
    text: str,
    sources: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    st = session(context, uid)
    state = {
        "title": title,
        "command": command,
        "args": args,
        "sources": sources,
        "status": status,
        "pages": split_pages(text),
        "page": 0,
        "view": "main",
        "base_pages": split_pages(text),
        "examples_pages": None,
        "last_text": text,
    }
    st["ui"] = state
    st["sources"] = sources
    st["last_text"] = text
    st["last_kind"] = command
    st["options"] = intel.extract_options(text)
    return state


async def run_command(command: str, update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    if not msg:
        return
    parsed = intel.parse_user_input(args)
    if not any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
        # Allow follow-up commands to reuse the current researched project.
        old = session(context, update.effective_user.id).get("sources")
        if not old:
            await msg.reply_html(f"Usage: <code>/{command} @handle|url</code>")
            return
        sources = old
    else:
        if update.callback_query and msg:
            status = None
            try:
                await msg.edit_text("🔎 Researching the project…")
            except Exception:
                pass
        else:
            status = await msg.reply_text("🔎 Researching the project…")
        try:
            sources = await intel.collect_sources(parsed, bot=context.bot)
        except Exception:
            log.exception("source collection failed for /%s", command)
            sources = {"limitations": ["Source collection failed"]}
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass

    runners: dict[str, Callable[..., Awaitable[tuple[str, str]]]] = {
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
    fn = runners.get(command)
    if not fn:
        await msg.reply_text("That command is not wired to a research engine yet.")
        return
    try:
        text, status = await fn(sources)
    except Exception:
        log.exception("command failed /%s", command)
        text, status = "Something went wrong while building this analysis. Try Refresh.", "ERROR"
    title = command.replace("marketingaudit", "MARKETING AUDIT").replace("zeromarketing", "$0 MARKETING").upper()
    state = await make_state(context, update.effective_user.id, title=title, command=command, args=args, text=text or "", sources=sources, status=status)
    if command in {"report", "fullpack"}:
        state["special_keyboard"] = report_keyboard()
        kb = report_keyboard()
    else:
        kb = generic_keyboard(state)
    rendered = render_html(title, state["pages"][0], 0, len(state["pages"]))
    if update.callback_query:
        await msg.edit_text(rendered, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    else:
        await msg.reply_text(rendered, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cmd_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    msg = update.effective_message
    args = list(context.args or [])
    parsed = intel.parse_user_input(args)
    if not any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
        old = session(context, update.effective_user.id).get("sources")
        if not old:
            await msg.reply_html("Usage: <code>/competition @handle|url</code>")
            return
        sources = old
    else:
        status = await msg.reply_text("🔎 Researching the project + competitive landscape…")
        try:
            sources = await intel.collect_sources(parsed, bot=context.bot)
        except Exception:
            log.exception("competition source collection failed")
            sources = {"limitations": ["Source collection failed"]}
        try:
            await status.delete()
        except Exception:
            pass
    status = await msg.reply_text("🏆 Finding relevant Web3 comparables…")
    try:
        text, ai_status, names = await intel.discover_competitors(sources, mode="similar", exclude=[], batch_size=5)
    except Exception:
        log.exception("competition discovery failed")
        text, ai_status, names = "I couldn't complete competitor research right now. Try Refresh.", "ERROR", []
    try:
        await status.delete()
    except Exception:
        pass
    sid = secrets.token_hex(8)
    db: DB = context.application.bot_data["db"]
    await db.save_comp_session(sid, update.effective_user.id, sources, "similar", names)
    comp = context.application.bot_data.setdefault("competition", {})
    comp[sid] = {
        "sources": sources,
        "user_id": update.effective_user.id,
        "shown": list(names),
        "by_mode": {"similar": list(names)},
        "views": {"similar": split_pages(text)},
        "current_mode": "similar",
        "page": 0,
        "examples": {},
    }
    pages = comp[sid]["views"]["similar"]
    await msg.reply_text(render_html("🏆 COMPETITION · Similar products", pages[0], 0, len(pages)), parse_mode="HTML", disable_web_page_preview=True, reply_markup=competition_keyboard(sid, 0, len(pages)))


async def cb_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    if not allowed(update.effective_user.id, context.application):
        await q.answer("Private", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3:
        await q.answer()
        return
    action, sid = parts[1], parts[2]
    comp = context.application.bot_data.setdefault("competition", {})
    mem = comp.get(sid)
    if not mem:
        row = await context.application.bot_data["db"].get_comp_session(sid)
        if not row or row["user_id"] != update.effective_user.id:
            await q.answer("Research session expired. Run /competition again.", show_alert=True)
            return
        mem = {"sources": row["subject"], "user_id": row["user_id"], "shown": row["shown"], "by_mode": {}, "views": {}, "current_mode": row["mode"], "page": 0, "examples": {}}
        comp[sid] = mem
    await q.answer("Researching…" if action not in {"prev", "next"} else "")

    if action in {"prev", "next"}:
        pages = mem.get("views", {}).get(mem.get("current_mode"), []) or ["No page."]
        page = int(mem.get("page", 0)) + (1 if action == "next" else -1)
        mem["page"] = max(0, min(page, len(pages) - 1))
        await q.message.edit_text(render_html(f"🏆 COMPETITION · {mem.get('current_mode')}", pages[mem["page"]], mem["page"], len(pages)), parse_mode="HTML", disable_web_page_preview=True, reply_markup=competition_keyboard(sid, mem["page"], len(pages)))
        return

    if action == "examples":
        mode = mem.get("current_mode") or "similar"
        current = "\n\n".join(mem.get("views", {}).get(mode, []))
        if not mem.get("examples", {}).get(mode):
            out, _ = await intel.run_examples(f"competition/{mode}", mem.get("sources"), current)
            mem.setdefault("examples", {})[mode] = split_pages(out)
        pages = mem["examples"][mode]
        mem["view"] = "examples"
        mem["page"] = 0
        await q.message.edit_text(render_html(f"🏆 COMPETITION · Examples · {mode}", pages[0], 0, len(pages)), parse_mode="HTML", disable_web_page_preview=True, reply_markup=competition_keyboard(sid, 0, len(pages)))
        return

    if action == "refresh":
        mode = mem.get("current_mode") or "similar"
    else:
        mode = action
        mem["current_mode"] = mode
        mem["view"] = "main"

    if action == "more":
        mode = mem.get("current_mode") or "similar"

    exclude = list(dict.fromkeys(mem.get("shown") or []))
    try:
        text, st, names = await intel.discover_competitors(mem["sources"], mode=mode, exclude=exclude, batch_size=5)
    except Exception:
        log.exception("competition callback failed: %s", mode)
        text, st, names = "I couldn't complete this research pass. Try Refresh.", "ERROR", []
    for n in names:
        if n not in mem["shown"]:
            mem["shown"].append(n)
    mem.setdefault("by_mode", {})[mode] = names
    mem.setdefault("views", {})[mode] = split_pages(text)
    mem["page"] = 0
    mem["current_mode"] = mode
    await context.application.bot_data["db"].save_comp_session(sid, update.effective_user.id, mem["sources"], mode, mem["shown"])
    pages = mem["views"][mode]
    title_map = {
        "similar": "Similar products", "architecture": "Architecture", "social": "Social leaders",
        "marketing": "Marketing", "ux": "UX leaders", "community": "Community", "growth": "Growth",
        "samestage": "Same-stage", "samelevel": "Same-level",
    }
    await q.message.edit_text(render_html(f"🏆 COMPETITION · {title_map.get(mode, mode)}", pages[0], 0, len(pages)), parse_mode="HTML", disable_web_page_preview=True, reply_markup=competition_keyboard(sid, 0, len(pages)))


async def cmd_competitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    if len(context.args or []) < 2:
        await update.effective_message.reply_html("Usage: <code>/competitor https://project.com Name</code>")
        return
    parsed = intel.parse_user_input(list(context.args), competitor_mode=True)
    focus = parsed.competitor_focus or context.args[-1]
    sources = await intel.collect_sources(parsed, bot=context.bot)
    text, _ = await intel.run_competitor_focus(sources, focus)
    state = await make_state(context, update.effective_user.id, title=f"🎯 COMPETITOR · {focus}", command="competitor", args=list(context.args), text=text, sources=sources, status="ok")
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=generic_keyboard(state))


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context):
        return
    raw = " ".join(context.args or [])
    if "|" not in raw:
        await update.effective_message.reply_html("Usage: <code>/compare @a https://a.com | @b https://b.com</code>")
        return
    left, right = raw.split("|", 1)
    sa = await intel.collect_sources(intel.parse_user_input(left.split()), bot=context.bot)
    sb = await intel.collect_sources(intel.parse_user_input(right.split()), bot=context.bot)
    text, st = await intel.run_compare(sa, sb)
    state = await make_state(context, update.effective_user.id, title="⚖️ COMPARE", command="compare", args=list(context.args), text=text, sources=sa, status=st)
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=generic_keyboard(state))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    db: DB = context.application.bot_data["db"]
    if not config.ALLOWED_USER_IDS:
        owners = context.application.bot_data.get("owners") or []
        if not owners:
            raw = await db.get_meta("owner_id")
            if raw and raw.isdigit():
                owners = [int(raw)]
            else:
                owners = [u.id]
                await db.set_meta("owner_id", str(u.id))
            context.application.bot_data["owners"] = owners
        if u.id not in owners:
            await update.effective_message.reply_text("Private bot — access denied.")
            return
    if not await gate(update, context):
        return
    await update.effective_message.reply_html("📣 <b>Online.</b>\n\n" + HELP)


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
        f"Gemini: {'set' if config.GEMINI_API_KEY else '—'} · Cerebras: {'set' if config.CEREBRAS_API_KEY else '—'}\n"
        f"OpenRouter: {'set' if config.OPENROUTER_API_KEY else '—'} · OR2: {'set' if config.OPENROUTER_API_KEY_2 else '—'}\n"
        f"Tavily research: {'set' if config.TAVILY_API_KEY else 'not set'}\n"
        f"Models: Groq=<code>{esc(config.GROQ_MODEL)}</code> · Gemini=<code>{esc(config.GEMINI_MODEL)}</code>\n"
        f"Access: locked to {owners or 'first /start'}\n"
        "Research: direct sources + Tavily web search/extraction when configured."
    )


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_html("Usage: <code>/watch @handle|url</code>")
        return
    key = " ".join(context.args)[:200]
    await context.application.bot_data["db"].watch(update.effective_user.id, key, key, {"input": key})
    await update.effective_message.reply_html(f"⭐ Watching <code>{esc(key)}</code>")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_html("Usage: <code>/unwatch @handle|url</code>")
        return
    await context.application.bot_data["db"].unwatch(update.effective_user.id, " ".join(context.args)[:200])
    await update.effective_message.reply_text("Removed.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    rows = await context.application.bot_data["db"].watchlist(update.effective_user.id)
    await update.effective_message.reply_html("⭐ <b>Watchlist</b>\n" + ("\n".join(f"• <code>{esc(r['key'])}</code>" for r in rows[:40]) if rows else "Empty."))


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await gate(update, context):
        await update.effective_message.reply_text("🚨 Alerts are stored as watch targets. Automatic push-diff checks can be added without changing the research UI.")


async def cmd_partnerships(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    sess = session(context, update.effective_user.id)
    sources = sess.get("sources")
    args = list(context.args or [])
    if args:
        parsed = intel.parse_user_input(args)
        if any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
            sources = await intel.collect_sources(parsed, bot=context.bot)
            sess["sources"] = sources
    if not sources:
        await update.effective_message.reply_html("Usage: <code>/partnerships @handle|url</code>")
        return
    out, st = await intel.run_partnerships(sources, mode="ideas")
    state = await make_state(context, update.effective_user.id, title="🤝 PARTNERSHIPS", command="partnerships", args=args, text=out, sources=sources, status=st)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Ideas", callback_data="partner:ideas"), InlineKeyboardButton("🤝 Projects", callback_data="partner:project")],
        [InlineKeyboardButton("🌐 Ecosystem", callback_data="partner:ecosystem"), InlineKeyboardButton("🔌 Integrations", callback_data="partner:integration")],
        [InlineKeyboardButton("📣 Creators", callback_data="partner:creator"), InlineKeyboardButton("🎙️ Spaces/AMAs", callback_data="partner:spaces")],
        [InlineKeyboardButton("📰 Media", callback_data="partner:media"), InlineKeyboardButton("🎯 Campaigns", callback_data="partner:campaign")],
        [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cmd_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    sess = session(context, update.effective_user.id)
    original = " ".join(context.args or []).strip() or sess.get("last_text") or ""
    if not original:
        await update.effective_message.reply_text("Nothing to shuffle yet. Run a marketing command first.")
        return
    out, st = await intel.run_shuffle(original, sources=sess.get("sources"), prior=sess.get("options") or [])
    state = await make_state(context, update.effective_user.id, title="🔀 SHUFFLE", command="shuffle", args=list(context.args), text=out, sources=sess.get("sources"), status=st)
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔀 More", callback_data="reply:shuffle"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],[InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")]]))


async def cmd_proposals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    sess = session(context, update.effective_user.id)
    sources = sess.get("sources")
    args = list(context.args or [])
    if args and any(vars(intel.parse_user_input(args)).values()):
        sources = await intel.collect_sources(intel.parse_user_input(args), bot=context.bot)
        sess["sources"] = sources
    if not sources:
        await update.effective_message.reply_html("Usage: <code>/marketingproposals @handle|url</code>")
        return
    text, st = await intel.run_marketing_proposals(sources, style="full")
    state = await make_state(context, update.effective_user.id, title="📋 MARKETING PROPOSAL", command="marketingproposals", args=args, text=text, sources=sources, status=st)
    # Proposal has its own format buttons, but still one message.
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Short", callback_data="prop:short"), InlineKeyboardButton("📩 Founder/Dev DM", callback_data="prop:founder_dm")],
        [InlineKeyboardButton("🐦 X DM", callback_data="prop:x_dm"), InlineKeyboardButton("💼 Job pitch", callback_data="prop:job")],
        [InlineKeyboardButton("🤝 Partnership", callback_data="prop:partner"), InlineKeyboardButton("👥 Community", callback_data="prop:community")],
        [InlineKeyboardButton("📅 30-day", callback_data="prop:30day"), InlineKeyboardButton("🔀 More", callback_data="prop:shuffle")],
        [InlineKeyboardButton("🎯 Direct", callback_data="prop:direct"), InlineKeyboardButton("🗣️ Casual", callback_data="prop:casual")],
        [InlineKeyboardButton("🧠 Strategic", callback_data="prop:strategic"), InlineKeyboardButton("👤 User POV", callback_data="prop:user_pov")],
        [InlineKeyboardButton("📣 Marketer", callback_data="prop:marketer"), InlineKeyboardButton("📩 Dev DM", callback_data="prop:dev_dm")],
        [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    text_in = " ".join(context.args or []).strip()
    if not text_in and update.effective_message.reply_to_message and update.effective_message.reply_to_message.text:
        text_in = update.effective_message.reply_to_message.text
    if not text_in:
        await update.effective_message.reply_html("Usage: <code>/reply your question or paste their message</code>")
        return
    sess = session(context, update.effective_user.id)
    out, st = await intel.run_reply_assistant(sess.get("sources"), text_in, prior_options=sess.get("options") or [])
    state = await make_state(context, update.effective_user.id, title="💬 REPLY", command="reply", args=list(context.args), text=out, sources=sess.get("sources"), status=st)
    # Reply is explicitly excluded from generic command nav, but gets useful reply-format controls.
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Direct", callback_data="reply:direct"), InlineKeyboardButton("🗣️ Casual", callback_data="reply:casual"), InlineKeyboardButton("🧠 Strategic", callback_data="reply:strategic")],
        [InlineKeyboardButton("👤 User POV", callback_data="reply:user"), InlineKeyboardButton("📣 Marketer", callback_data="reply:marketer"), InlineKeyboardButton("📩 Dev DM", callback_data="reply:dev_dm")],
        [InlineKeyboardButton("🐦 X Reply", callback_data="reply:x_reply"), InlineKeyboardButton("🔀 More", callback_data="reply:shuffle"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await update.effective_message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cb_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    await q.answer("Working…")
    sess = session(context, update.effective_user.id)
    sources = sess.get("sources")
    if not sources:
        await q.answer("Research a project first", show_alert=True)
        return
    style = q.data.split(":", 1)[1]
    if style == "shuffle":
        out, st = await intel.run_shuffle(sess.get("last_text") or "", sources=sources, prior=sess.get("options") or [])
    else:
        out, st = await intel.run_marketing_proposals(sources, style=style, prior_text=sess.get("last_text") or "")
    state = await make_state(context, update.effective_user.id, title=f"📋 PROPOSAL · {style}", command="marketingproposals", args=sess.get("ui", {}).get("args", []), text=out, sources=sources, status=st)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Short", callback_data="prop:short"), InlineKeyboardButton("📩 Founder/Dev DM", callback_data="prop:founder_dm")],
        [InlineKeyboardButton("🐦 X DM", callback_data="prop:x_dm"), InlineKeyboardButton("💼 Job pitch", callback_data="prop:job")],
        [InlineKeyboardButton("🤝 Partnership", callback_data="prop:partner"), InlineKeyboardButton("👥 Community", callback_data="prop:community")],
        [InlineKeyboardButton("📅 30-day", callback_data="prop:30day"), InlineKeyboardButton("🔀 More", callback_data="prop:shuffle")],
        [InlineKeyboardButton("🎯 Direct", callback_data="prop:direct"), InlineKeyboardButton("🗣️ Casual", callback_data="prop:casual")],
        [InlineKeyboardButton("🧠 Strategic", callback_data="prop:strategic"), InlineKeyboardButton("👤 User POV", callback_data="prop:user_pov")],
        [InlineKeyboardButton("📣 Marketer", callback_data="prop:marketer"), InlineKeyboardButton("📩 Dev DM", callback_data="prop:dev_dm")],
        [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cb_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    await q.answer("Working…")
    sess = session(context, update.effective_user.id)
    mode = q.data.split(":", 1)[1]
    if mode == "shuffle":
        out, st = await intel.run_shuffle(sess.get("last_text") or "", sources=sess.get("sources"), prior=sess.get("options") or [])
    else:
        out, st = await intel.run_reply_assistant(sess.get("sources"), sess.get("last_text") or "", mode=mode, perspective=mode)
    state = await make_state(context, update.effective_user.id, title=f"💬 REPLY · {mode}", command="reply", args=[], text=out, sources=sess.get("sources"), status=st)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Direct", callback_data="reply:direct"), InlineKeyboardButton("🗣️ Casual", callback_data="reply:casual"), InlineKeyboardButton("🧠 Strategic", callback_data="reply:strategic")],
        [InlineKeyboardButton("👤 User POV", callback_data="reply:user"), InlineKeyboardButton("📣 Marketer", callback_data="reply:marketer"), InlineKeyboardButton("📩 Dev DM", callback_data="reply:dev_dm")],
        [InlineKeyboardButton("🐦 X Reply", callback_data="reply:x_reply"), InlineKeyboardButton("🔀 More", callback_data="reply:shuffle"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cb_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    await q.answer("Working…")
    sess = session(context, update.effective_user.id)
    sources = sess.get("sources")
    if not sources:
        await q.message.edit_text("Run a project command first so I have project research for partnerships.")
        return
    mode = q.data.split(":", 1)[1]
    out, st = await intel.run_partnerships(sources, mode=mode, prior=sess.get("last_text") or "")
    state = await make_state(context, update.effective_user.id, title=f"🤝 PARTNERSHIPS · {mode}", command="partnerships", args=[], text=out, sources=sources, status=st)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Ideas", callback_data="partner:ideas"), InlineKeyboardButton("🤝 Projects", callback_data="partner:project")],
        [InlineKeyboardButton("🌐 Ecosystem", callback_data="partner:ecosystem"), InlineKeyboardButton("🔌 Integrations", callback_data="partner:integration")],
        [InlineKeyboardButton("📣 Creators", callback_data="partner:creator"), InlineKeyboardButton("🎙️ Spaces/AMAs", callback_data="partner:spaces")],
        [InlineKeyboardButton("📰 Media", callback_data="partner:media"), InlineKeyboardButton("🎯 Campaigns", callback_data="partner:campaign")],
        [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
    ])
    state["special_keyboard"] = kb
    await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)


async def cb_ui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    if not allowed(update.effective_user.id, context.application):
        await q.answer("Private", show_alert=True)
        return
    sess = session(context, update.effective_user.id)
    state = sess.get("ui") or {}
    action = q.data.split(":")[1] if ":" in q.data else ""
    if action == "commands":
        page = int(q.data.split(":")[2]) if len(q.data.split(":")) > 2 else 0
        await q.answer()
        await q.message.edit_text(f"🧭 <b>COMMANDS · {page+1}/{len(command_pages())}</b>\n\nPick what you want to run next.", parse_mode="HTML", reply_markup=command_menu_keyboard(page))
        return
    if action == "main":
        await q.answer()
        if not state:
            await q.message.edit_text("No active research session. Run a command first.", reply_markup=None)
            return
        state["view"] = "main"
        state["pages"] = state.get("base_pages") or ["Nothing to show yet."]
        state["page"] = min(int(state.get("page", 0)), len(state["pages"]) - 1)
        await edit_state(q.message, state)
        return
    if not state:
        await q.answer("No active page", show_alert=True)
        return
    await q.answer("Working…" if action == "refresh" or action == "examples" else "")
    if action in {"next", "prev"}:
        pages = state.get("pages") or [""]
        delta = 1 if action == "next" else -1
        state["page"] = max(0, min(int(state.get("page", 0)) + delta, len(pages) - 1))
        await edit_state(q.message, state)
        return
    if action == "refresh":
        cmd = state.get("command")
        args = list(state.get("args") or [])
        if cmd in {"competition", "reply", "marketingproposals"}:
            # Refresh in place and preserve the current approach/style when possible.
            if cmd == "reply":
                sess["ui"] = state
                current = str(state.get("title") or "").split("·", 1)[-1].strip().lower()
                mode = {"direct":"direct", "casual":"casual", "strategic":"strategic", "user pov":"user", "marketer":"marketer", "dev dm":"dev_dm", "x reply":"x_reply"}.get(current, "auto")
                out, st = await intel.run_reply_assistant(sess.get("sources"), sess.get("last_text") or "", mode=mode, perspective=mode)
            else:
                current = str(state.get("title") or "").lower()
                style = "full"
                for candidate in ("short", "founder_dm", "x_dm", "job", "partner", "community", "30day", "direct", "casual", "strategic", "user_pov", "marketer", "dev_dm"):
                    if candidate.replace("_", " ") in current:
                        style = candidate
                        break
                out, st = await intel.run_marketing_proposals(sess.get("sources"), style=style, prior_text=sess.get("last_text") or "")
            state.update({"pages": split_pages(out), "base_pages": split_pages(out), "page": 0, "view": "main", "last_text": out, "status": st})
            sess["last_text"] = out
            await edit_state(q.message, state)
            return
        # Refresh a generic command in place.
        parsed = intel.parse_user_input(args)
        sources = state.get("sources")
        if any([parsed.x_handles, parsed.websites, parsed.telegrams, parsed.contracts]):
            sources = await intel.collect_sources(parsed, bot=context.bot)
        fn_map = {
            "market": intel.run_marketing_audit, "marketingaudit": intel.run_marketing_audit,
            "positioning": intel.run_positioning, "campaigns": intel.run_campaigns,
            "marketgaps": intel.run_marketgaps, "opportunities": intel.run_opportunities,
            "report": intel.run_report, "fullpack": intel.run_report,
            "funnels": intel.run_funnels, "marketingfunnels": intel.run_funnels,
            "suggestmarketing": intel.run_suggest_marketing, "marketingideas": intel.run_suggest_marketing,
            "organicmarketing": intel.run_organic, "zeromarketing": intel.run_zero, "0marketing": intel.run_zero,
        }
        fn = fn_map.get(cmd)
        if cmd == "partnerships" and sources:
            try:
                out, st = await intel.run_partnerships(sources, mode="ideas")
            except Exception:
                log.exception("refresh failed /partnerships")
                out, st = "Something went wrong during Refresh. Try again.", "ERROR"
            state.update({"pages": split_pages(out), "base_pages": split_pages(out), "page": 0, "view": "main", "sources": sources, "last_text": out, "status": st})
            sess["sources"] = sources
            sess["last_text"] = out
            await edit_state(q.message, state)
            return
        if fn:
            try:
                out, st = await fn(sources or {})
            except Exception:
                log.exception("refresh failed /%s", cmd)
                out, st = "Something went wrong during Refresh. Try again.", "ERROR"
            state.update({"pages": split_pages(out), "base_pages": split_pages(out), "page": 0, "view": "main", "sources": sources, "last_text": out, "status": st})
            sess["sources"] = sources
            sess["last_text"] = out
        await edit_state(q.message, state)
        return
    if action == "examples":
        if not state.get("examples_pages"):
            out, _ = await intel.run_examples(state.get("command", "market"), state.get("sources"), state.get("last_text", ""))
            state["examples_pages"] = split_pages(out)
        state["pages"] = state["examples_pages"]
        state["page"] = 0
        state["view"] = "examples"
        await edit_state(q.message, state)
        return


async def cb_reportview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not update.effective_user or not q.message:
        return
    await q.answer("Researching…")
    sess = session(context, update.effective_user.id)
    sources = sess.get("sources")
    if not sources:
        await q.message.edit_text("Run /report on a project first.")
        return
    section = q.data.split(":", 1)[1]
    prompts = {
        "overview": "Give only the most useful strategic overview.",
        "positioning": "Focus only on positioning and messaging.",
        "content": "Focus only on content and X/content execution.",
        "community": "Focus only on community and participation loops.",
        "acquisition": "Focus only on acquisition and funnel entry points.",
        "partnerships": "Focus only on partnerships and collaboration formats.",
        "competition": "Focus only on competitive landscape and what to study.",
        "opportunities": "Focus only on the highest-value opportunities with concrete execution.",
    }
    base = intel.evidence_brief(sources)
    out, st = await intel.complete_fast(f"""{intel.HUMAN_VOICE}

SECTION: {section}
INSTRUCTION: {prompts.get(section, section)}

PROJECT EVIDENCE:
{base}

Give a concise mobile-friendly section with concrete examples. Do not write a full report.""", max_tokens=1500)
    state = await make_state(context, update.effective_user.id, title=f"📊 REPORT · {section}", command="report", args=[], text=out or "No section available.", sources=sources, status=st)
    state["special_keyboard"] = report_keyboard()
    await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=report_keyboard())


async def cb_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not q.message:
        return
    await q.answer("Opening…")
    command = q.data.split(":", 1)[1]
    # Keep the same chat/project context: if a command needs a project it can reuse the current session.
    await q.message.edit_text(f"Running /{command} with the current project…", disable_web_page_preview=True)
    if command == "competition":
        # Synthetic command context isn't needed; invoke directly with current session sources.
        sess = session(context, update.effective_user.id)
        src = sess.get("sources")
        if not src:
            await q.message.edit_text("Run a project command first so Competition has a project to research.")
            return
        # Directly perform competition using stored source object.
        text, st, names = await intel.discover_competitors(src, mode="similar", exclude=[], batch_size=5)
        sid = secrets.token_hex(8)
        context.application.bot_data.setdefault("competition", {})[sid] = {"sources": src, "user_id": update.effective_user.id, "shown": names, "by_mode": {"similar": names}, "views": {"similar": split_pages(text)}, "current_mode": "similar", "page": 0, "examples": {}}
        pages = context.application.bot_data["competition"][sid]["views"]["similar"]
        await q.message.edit_text(render_html("🏆 COMPETITION · Similar products", pages[0], 0, len(pages)), parse_mode="HTML", reply_markup=competition_keyboard(sid, 0, len(pages)))
        return
    # Commands that can reuse the current project are executed against the same message.
    if command in {"market", "positioning", "campaigns", "funnels", "suggestmarketing", "organicmarketing", "zeromarketing", "marketgaps", "opportunities", "fullpack"}:
        await run_command(command, update, context, [])
        return
    if command == "marketingproposals":
        sess = session(context, update.effective_user.id)
        sources = sess.get("sources")
        if not sources:
            await q.message.edit_text("Run a project command first so I have project research for the proposal.")
            return
        out, st = await intel.run_marketing_proposals(sources, style="full")
        state = await make_state(context, update.effective_user.id, title="📋 MARKETING PROPOSAL", command="marketingproposals", args=[], text=out, sources=sources, status=st)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Short", callback_data="prop:short"), InlineKeyboardButton("📩 Founder/Dev DM", callback_data="prop:founder_dm")],
            [InlineKeyboardButton("🐦 X DM", callback_data="prop:x_dm"), InlineKeyboardButton("💼 Job pitch", callback_data="prop:job")],
            [InlineKeyboardButton("🤝 Partnership", callback_data="prop:partner"), InlineKeyboardButton("👥 Community", callback_data="prop:community")],
            [InlineKeyboardButton("📅 30-day", callback_data="prop:30day"), InlineKeyboardButton("🔀 More", callback_data="prop:shuffle")],
            [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
        ])
        state["special_keyboard"] = kb
        await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
        return
    if command == "partnerships":
        sess = session(context, update.effective_user.id)
        sources = sess.get("sources")
        if not sources:
            await q.message.edit_text("Run a project command first so I have project research for partnerships.")
            return
        out, st = await intel.run_partnerships(sources, mode="ideas")
        state = await make_state(context, update.effective_user.id, title="🤝 PARTNERSHIPS", command="partnerships", args=[], text=out, sources=sources, status=st)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Ideas", callback_data="partner:ideas"), InlineKeyboardButton("🤝 Projects", callback_data="partner:project")],
            [InlineKeyboardButton("🌐 Ecosystem", callback_data="partner:ecosystem"), InlineKeyboardButton("🔌 Integrations", callback_data="partner:integration")],
            [InlineKeyboardButton("📣 Creators", callback_data="partner:creator"), InlineKeyboardButton("🎙️ Spaces/AMAs", callback_data="partner:spaces")],
            [InlineKeyboardButton("📰 Media", callback_data="partner:media"), InlineKeyboardButton("🎯 Campaigns", callback_data="partner:campaign")],
            [InlineKeyboardButton("💡 Examples", callback_data="ui:examples"), InlineKeyboardButton("🔄 Refresh", callback_data="ui:refresh")],
            [InlineKeyboardButton("◀️ Back", callback_data="ui:prev"), InlineKeyboardButton("▶️ Next", callback_data="ui:next")],
        ])
        state["special_keyboard"] = kb
        await q.message.edit_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
        return
    if command == "watchlist":
        rows = await context.application.bot_data["db"].watchlist(update.effective_user.id)
        text = "\n".join(f"• {r['key']}" for r in rows[:40]) or "Empty."
        await q.message.edit_text("⭐ <b>WATCHLIST</b>\n\n" + html.escape(text), parse_mode="HTML")
        return
    if command == "alerts":
        await q.message.edit_text("🚨 <b>ALERTS</b>\n\nWatch targets are stored. Automatic push-diff checks are not enabled yet.", parse_mode="HTML")
        return
    if command == "competitor":
        await q.message.edit_text("Use /competitor with the project and comparable name you want to inspect.")
        return
    if command == "compare":
        await q.message.edit_text("Use /compare with two project sources separated by |.")
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user or not update.message:
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    low = text.lower()
    triggers = ("reply", "dm", "what can i say", "quick idea", "marketing idea", "propose", "proposal", "pitch", "approach them", "dev", "founder", "another angle", "make it")
    if not any(t in low for t in triggers):
        return
    sess = session(context, update.effective_user.id)
    out, st = await intel.run_reply_assistant(sess.get("sources"), text, prior_options=sess.get("options") or [])
    state = await make_state(context, update.effective_user.id, title="💬 ASSISTANT", command="reply", args=[], text=out, sources=sess.get("sources"), status=st)
    await update.message.reply_text(render_html(state["title"], state["pages"][0], 0, len(state["pages"])), parse_mode="HTML", disable_web_page_preview=True, reply_markup=generic_keyboard(state))


async def on_start(app: Application) -> None:
    db = DB(config.DATABASE_PATH)
    await db.connect()
    app.bot_data["db"] = db
    app.bot_data["sessions"] = {}
    app.bot_data["competition"] = {}
    if config.ALLOWED_USER_IDS:
        app.bot_data["owners"] = list(config.ALLOWED_USER_IDS)
    else:
        raw = await db.get_meta("owner_id")
        app.bot_data["owners"] = [int(raw)] if raw and raw.isdigit() else []
    commands = [
        ("start", "Start"), ("help", "Help"), ("market", "Marketing audit"), ("marketingaudit", "Marketing audit"),
        ("positioning", "Positioning"), ("competition", "Competition intelligence"), ("competitor", "Comparable deep dive"),
        ("campaigns", "Campaigns"), ("marketingfunnels", "Funnel audit"), ("funnels", "Funnel audit"),
        ("suggestmarketing", "Marketing ideas"), ("organicmarketing", "Organic marketing"), ("zeromarketing", "$0 marketing"),
        ("marketgaps", "Marketing gaps"), ("opportunities", "Opportunities"), ("compare", "Compare"),
        ("report", "Full report"), ("fullpack", "Full pack"), ("marketingproposals", "Marketing proposal"),
        ("reply", "Reply assistant"), ("marketingreply", "Marketing reply"), ("shuffle", "Shuffle"),
        ("partnerships", "Partnerships"), ("watch", "Watch"), ("watchlist", "Watchlist"), ("unwatch", "Unwatch"),
        ("alerts", "Alerts"), ("settings", "Settings"),
    ]
    try:
        await app.bot.set_my_commands([BotCommand(a, b) for a, b in commands])
    except Exception:
        log.exception("set_my_commands failed")
    me = await app.bot.get_me()
    log.info("Online @%s build=%s tavily=%s", me.username, config.BUILD, bool(config.TAVILY_API_KEY))


async def on_stop(app: Application) -> None:
    db = app.bot_data.get("db")
    if db:
        await db.close()


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(on_start).post_shutdown(on_stop).build()

    aliases = {
        "market": "market", "marketing": "market", "marketingaudit": "marketingaudit", "positioning": "positioning",
        "campaigns": "campaigns", "marketgaps": "marketgaps", "opportunities": "opportunities", "report": "report",
        "fullpack": "fullpack", "funnels": "funnels", "marketingfunnels": "marketingfunnels", "marketingfunnel": "funnels",
        "suggestmarketing": "suggestmarketing", "organicmarketing": "organicmarketing",
        "zeromarketing": "zeromarketing", "0marketing": "0marketing", "freegrowth": "zeromarketing",
        "freeMarketing": "zeromarketing", "marketingideas": "marketingideas", "ideas": "marketingideas",
    }
    for cmd, engine in aliases.items():
        app.add_handler(CommandHandler(cmd, lambda u, c, e=engine: run_command(e, u, c, list(c.args or []))))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("competition", cmd_competition))
    app.add_handler(CommandHandler("competitor", cmd_competitor))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("marketingproposals", cmd_proposals))
    app.add_handler(CommandHandler("marketingproposal", cmd_proposals))
    app.add_handler(CommandHandler("proposals", cmd_proposals))
    app.add_handler(CommandHandler("reply", cmd_reply))
    app.add_handler(CommandHandler("marketingreply", cmd_reply))
    app.add_handler(CommandHandler("suggestreply", cmd_reply))
    app.add_handler(CommandHandler("partnerships", cmd_partnerships))
    app.add_handler(CommandHandler("partnership", cmd_partnerships))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("shuffle", cmd_shuffle))
    app.add_handler(CommandHandler("fullback", lambda u, c: run_command("report", u, c, list(c.args or []))))

    app.add_handler(CallbackQueryHandler(cb_competition, pattern=r"^cmp:"))
    app.add_handler(CallbackQueryHandler(cb_proposal, pattern=r"^prop:"))
    app.add_handler(CallbackQueryHandler(cb_reply, pattern=r"^reply:"))
    app.add_handler(CallbackQueryHandler(cb_partner, pattern=r"^partner:"))
    app.add_handler(CallbackQueryHandler(cb_ui, pattern=r"^ui:"))
    app.add_handler(CallbackQueryHandler(cb_reportview, pattern=r"^reportview:"))
    app.add_handler(CallbackQueryHandler(cb_commands, pattern=r"^cmd:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Polling %s", config.BUILD)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
