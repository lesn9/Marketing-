"""
Full intelligence core: sources + AI router + marketing/competition engines.
All research and strategy logic lives here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

import config

log = logging.getLogger("mkt.intel")

# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------

SYSTEM = """You are an elite Web3 marketing strategist, growth analyst, and competitive intelligence advisor.

HARD RULES:
1. Always move: CURRENT STATE → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY.
2. Reject generic advice that fits 100 random projects. Be specific to THIS evidence.
3. Never invent X posts, follower counts, Telegram activity, campaigns, partnerships, or product features.
4. If a source was unavailable, note it briefly and continue with other evidence.
5. No price promises or guaranteed returns. Distinguish FACT vs INFERENCE vs RECOMMENDATION.
6. Telegram-mobile format: clear sections, bullets, emoji ONLY on section headings.
7. Competitors MUST be real crypto/Web3 projects only — never Web2 SaaS or generic brands.
8. Do not invent competitor social accounts. Say "Not found / not publicly verified" when unknown.
9. Tier competitors: TOP-TIER BENCHMARKS / ESTABLISHED / MID-TIER GROWING / EMERGING SAME-STAGE.
   Explain WHY each tier. Same-stage projects are often more useful than only listing Uniswap-scale giants.
10. Recommendations must say WHAT / HOW / FOR WHOM / WHERE — not "improve marketing".
"""

GATE = """
QUALITY GATE before you finish:
- Did I diagnose a real issue (not just describe)?
- Is every recommendation specific to THIS project type, stage, and evidence?
- Did I give execution examples (posts, CTAs, campaign mechanics)?
- For competitors: did I separate top-tier benchmarks from same-stage growth lessons?
If any answer is no, improve the answer.
"""

COMPETITOR_RULES = """
CRYPTO/WEB3 COMPETITORS ONLY.
Never Web2 companies.

Tiers (required):
🔵 TOP-TIER BENCHMARKS — category leaders (useful for brand/UX/distribution lessons; not always copyable at small scale)
🟣 ESTABLISHED COMPARABLES — meaningful market presence, relevant product
🟢 MID-TIER / GROWING — growing projects with transferable tactics
🟡 EMERGING / SAME-STAGE — newer projects whose growth moves may be realistic to adapt

For each competitor:
🏷️ Project | Confidence 🟢/🟡/⚠️ | Research status
🌐 Website | 🐦 X | 💬 Telegram | ⛓️ Chain
Why comparable | What appears stronger (dimension-specific)
What subject can learn | How to adapt | What NOT to copy

Label AI-memory-only picks: ⚠️ INFERRED CANDIDATE
"""


async def complete(prompt: str, *, max_tokens: int = 3200) -> tuple[str | None, str]:
    """Groq → OpenRouter with broad model fallbacks. Returns (text, status)."""
    errors: list[str] = []

    if config.GROQ_API_KEY:
        models = [
            config.GROQ_MODEL,
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
        ]
        text, status, detail = await _chat(
            "https://api.groq.com/openai/v1/chat/completions",
            config.GROQ_API_KEY,
            models,
            prompt,
            max_tokens,
        )
        if text:
            return text, status
        errors.append(f"groq:{status}:{detail}")

    if config.OPENROUTER_API_KEY:
        models = [
            config.OPENROUTER_MODEL,
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-2-9b-it:free",
            "mistralai/mistral-7b-instruct:free",
            "openrouter/auto",
            "nousresearch/hermes-3-llama-3.1-405b:free",
        ]
        text, status, detail = await _chat(
            "https://openrouter.ai/api/v1/chat/completions",
            config.OPENROUTER_API_KEY,
            models,
            prompt,
            max_tokens,
            extra={
                "HTTP-Referer": "https://github.com/web3-marketing-intel",
                "X-Title": "Web3 Marketing Intelligence",
            },
        )
        if text:
            return text, status
        errors.append(f"openrouter:{status}:{detail}")

    if not config.GROQ_API_KEY and not config.OPENROUTER_API_KEY:
        return None, "AI_NOT_CONFIGURED"

    log.warning("AI all failed: %s", " | ".join(errors)[:500])
    # Prefer most specific status
    if any("AUTH" in e for e in errors):
        return None, "AI_AUTH_FAILED"
    if any("RATE" in e for e in errors):
        return None, "AI_RATE_LIMITED"
    if any("TIMEOUT" in e for e in errors):
        return None, "AI_TIMEOUT"
    return None, "AI_PROVIDER_ERROR"


async def _chat(
    url: str,
    key: str,
    models: list[str],
    prompt: str,
    max_tokens: int,
    extra: dict | None = None,
) -> tuple[str | None, str, str]:
    headers = {
        "Authorization": f"Bearer {key.strip()}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    last_detail = ""
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            seen = set()
            for model in models:
                if not model or model in seen:
                    continue
                seen.add(model)
                try:
                    resp = await client.post(
                        url,
                        headers=headers,
                        json={
                            "model": model,
                            "temperature": 0.45,
                            "max_tokens": max_tokens,
                            "messages": [
                                {"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                except httpx.TimeoutException:
                    return None, "AI_TIMEOUT", "timeout"
                except Exception as exc:
                    last_detail = str(exc)[:120]
                    continue
                if resp.status_code == 401:
                    return None, "AI_AUTH_FAILED", "401"
                if resp.status_code == 429:
                    last_detail = "429"
                    continue  # try next model
                if resp.status_code >= 400:
                    last_detail = f"{resp.status_code}:{(resp.text or '')[:120]}"
                    log.warning("AI model %s failed: %s", model, last_detail)
                    continue
                data = resp.json()
                text = (
                    (data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if text:
                    return text, "AI_SUCCESS", model
                last_detail = "empty content"
    except Exception as exc:
        return None, "AI_PROVIDER_ERROR", str(exc)[:120]
    return None, "AI_MODEL_ERROR", last_detail


# ---------------------------------------------------------------------------
# Input parsing + sources
# ---------------------------------------------------------------------------

PROJECT_TYPES = {
    "meme", "utility", "defi", "infrastructure", "gaming", "ai", "consumer",
    "social", "depin", "rwa", "trading", "prediction", "nft", "launchpad",
    "protocol", "ecosystem", "dex", "wallet", "other",
}


@dataclass
class ParsedInput:
    raw: str
    x_handles: list[str] = field(default_factory=list)
    websites: list[str] = field(default_factory=list)
    telegrams: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    project_type: str | None = None
    extra_context: str = ""
    competitor_focus: str | None = None  # for /competitor last token(s)


def parse_user_input(args: list[str], *, competitor_mode: bool = False) -> ParsedInput:
    raw = " ".join(args).strip()
    result = ParsedInput(raw=raw)
    if not raw:
        return result
    tokens = list(args)

    if competitor_mode and len(tokens) >= 2:
        # Last token(s) that are NOT urls/@ as competitor name — simple: last non-url token
        # Prefer: everything after first url/@handle cluster
        # Spec: /competitor https://malaswap.com Uniswap
        focus_parts: list[str] = []
        subject_parts: list[str] = []
        seen_subject = False
        for i, tok in enumerate(tokens):
            is_src = bool(
                tok.startswith("http")
                or tok.startswith("@")
                or tok.startswith("0x")
                or "t.me/" in tok
                or "x.com/" in tok
            )
            if is_src or (not seen_subject and re.fullmatch(r"[A-Za-z0-9_]{2,20}", tok)):
                subject_parts.append(tok)
                seen_subject = True
            else:
                focus_parts.append(tok)
        # If we never split, last word is focus
        if not focus_parts and len(tokens) >= 2:
            focus_parts = [tokens[-1]]
            subject_parts = tokens[:-1]
        result.competitor_focus = " ".join(focus_parts).strip() or None
        tokens = subject_parts

    for i, tok in enumerate(tokens):
        low = tok.lower().strip(",")
        if low in PROJECT_TYPES:
            result.project_type = low
            ctx = " ".join(tokens[i + 1 :]).strip()
            if ctx and not result.competitor_focus:
                result.extra_context = ctx
            tokens = tokens[:i]
            break

    blob = " ".join(tokens)
    for m in re.finditer(r"https?://[^\s]+", blob):
        url = m.group(0).rstrip(").,]")
        low = url.lower()
        if "x.com/" in low or "twitter.com/" in low:
            result.x_handles.append(url)
        elif "t.me/" in low or "telegram.me/" in low:
            result.telegrams.append(url)
        else:
            result.websites.append(url)
    for m in re.finditer(r"@([A-Za-z0-9_]{1,30})", blob):
        result.x_handles.append("@" + m.group(1))
    for m in re.finditer(r"\b(0x[a-fA-F0-9]{40})\b", blob):
        result.contracts.append(m.group(1))

    cleaned = re.sub(r"https?://[^\s]+", " ", blob)
    cleaned = re.sub(r"@\w+", " ", cleaned)
    cleaned = re.sub(r"0x[a-fA-F0-9]{40}", " ", cleaned)
    for part in cleaned.split():
        part = part.strip(",. ")
        if not part or part.lower() in PROJECT_TYPES:
            continue
        if "." in part and " " not in part:
            result.websites.append("https://" + part if not part.startswith("http") else part)
        elif re.fullmatch(r"[A-Za-z0-9_]{2,20}", part):
            if part.lower() not in {h.lstrip("@").lower() for h in result.x_handles}:
                result.x_handles.append("@" + part)

    result.x_handles = _dedupe(result.x_handles)
    result.websites = _dedupe(result.websites)
    result.telegrams = _dedupe(result.telegrams)
    result.contracts = _dedupe(result.contracts)
    return result


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def extract_handle(value: str) -> str | None:
    value = (value or "").strip()
    if value.startswith("@"):
        return value[1:].split("/")[0]
    m = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)", value, re.I)
    if m and m.group(1).lower() not in {"intent", "share", "i", "home", "search"}:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        return value
    return None


async def fetch_website(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_type": "website", "url": url, "ok": False, "error": None,
        "title": None, "meta_description": None, "h1": [], "headlines": [],
        "text_sample": "", "links": {"x": [], "telegram": [], "discord": [], "docs": []},
        "ctas": [], "has_community_link": False,
    }
    if not url.startswith("http"):
        url = "https://" + url
        out["url"] = url
    try:
        async with httpx.AsyncClient(
            timeout=18, follow_redirects=True,
            headers={"User-Agent": "Web3MarketingIntel/3.0"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                out["error"] = f"HTTP {resp.status_code}"
                return out
            soup = BeautifulSoup(resp.text[:220_000], "lxml")
            out["ok"] = True
            if soup.title and soup.title.string:
                out["title"] = soup.title.string.strip()[:200]
            md = soup.find("meta", attrs={"name": "description"}) or soup.find(
                "meta", attrs={"property": "og:description"}
            )
            if md and md.get("content"):
                out["meta_description"] = md["content"].strip()[:400]
            for h in soup.find_all(["h1", "h2"])[:14]:
                t = h.get_text(" ", strip=True)
                if t:
                    out["headlines"].append(t[:180])
                    if h.name == "h1":
                        out["h1"].append(t[:180])
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            out["text_sample"] = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:4000]
            for a in soup.find_all("a", href=True)[:220]:
                href = a["href"].strip()
                full = urljoin(url, href)
                low = full.lower()
                label = a.get_text(" ", strip=True)[:80]
                if ("x.com/" in low or "twitter.com/" in low) and "intent" not in low:
                    out["links"]["x"].append(full)
                elif "t.me/" in low or "telegram.me/" in low:
                    out["links"]["telegram"].append(full)
                elif "discord" in low:
                    out["links"]["discord"].append(full)
                elif "gitbook" in low or "/docs" in low:
                    out["links"]["docs"].append(full)
                if any(k in (label + href).lower() for k in ("join", "launch", "app", "buy", "docs", "community", "trade")):
                    if label:
                        out["ctas"].append({"text": label, "href": full})
            for k in out["links"]:
                out["links"][k] = _dedupe(out["links"][k])[:8]
            out["has_community_link"] = bool(out["links"]["telegram"] or out["links"]["discord"])
            out["ctas"] = out["ctas"][:15]
    except Exception as exc:
        out["error"] = str(exc)[:120]
        log.warning("website %s: %s", url, exc)
    return out


async def fetch_x(handle_or_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_type": "x", "handle": None, "ok": False, "error": None,
        "name": None, "bio": None, "followers": None, "following": None,
        "tweet_count": None, "created_at": None, "url": None,
        "recent_tweets": [], "mode": "none", "note": None,
    }
    handle = extract_handle(handle_or_url)
    out["handle"] = handle
    if not handle:
        out["error"] = "Could not parse X handle"
        return out

    if config.X_BEARER_TOKEN:
        try:
            headers = {"Authorization": f"Bearer {config.X_BEARER_TOKEN}"}
            async with httpx.AsyncClient(timeout=20) as client:
                u = await client.get(
                    f"https://api.x.com/2/users/by/username/{handle}",
                    headers=headers,
                    params={"user.fields": "description,public_metrics,created_at,url"},
                )
                if u.status_code == 200 and (u.json().get("data") or {}).get("id"):
                    data = u.json()["data"]
                    out["ok"] = True
                    out["mode"] = "api"
                    out["name"] = data.get("name")
                    out["bio"] = data.get("description")
                    out["url"] = data.get("url")
                    out["created_at"] = data.get("created_at")
                    m = data.get("public_metrics") or {}
                    out["followers"] = m.get("followers_count")
                    out["following"] = m.get("following_count")
                    out["tweet_count"] = m.get("tweet_count")
                    tw = await client.get(
                        f"https://api.x.com/2/users/{data['id']}/tweets",
                        headers=headers,
                        params={
                            "max_results": 8,
                            "tweet.fields": "created_at,public_metrics,text",
                            "exclude": "retweets,replies",
                        },
                    )
                    if tw.status_code == 200:
                        for t in tw.json().get("data") or []:
                            out["recent_tweets"].append({
                                "text": t.get("text"),
                                "created_at": t.get("created_at"),
                                "metrics": t.get("public_metrics") or {},
                            })
                    return out
        except Exception as exc:
            log.warning("X API: %s", exc)

    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Web3MarketingIntel/3.0)"},
        ) as client:
            resp = await client.get(f"https://x.com/{handle}")
            if resp.status_code == 200 and len(resp.text) > 500:
                soup = BeautifulSoup(resp.text, "lxml")
                title = soup.title.string.strip() if soup.title and soup.title.string else None
                desc = None
                md = soup.find("meta", attrs={"property": "og:description"})
                if md and md.get("content"):
                    desc = md["content"].strip()[:400]
                out["ok"] = True
                out["mode"] = "public_html"
                out["name"] = title
                out["bio"] = desc
                out["url"] = f"https://x.com/{handle}"
                return out
    except Exception as exc:
        log.warning("X HTML @%s: %s", handle, exc)

    out["ok"] = True
    out["mode"] = "handle_only"
    out["url"] = f"https://x.com/{handle}"
    out["note"] = (
        "LIVE X DATA UNAVAILABLE (no API / page blocked). "
        "Do not invent followers, posts, or engagement. Analyze handle + cross-links only."
    )
    return out


async def fetch_telegram(bot, value: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_type": "telegram", "username": None, "ok": False, "error": None,
        "title": None, "description": None, "members": None, "type": None, "invite_url": None,
    }
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)", value or "", re.I)
    username = m.group(1) if m else (value[1:] if (value or "").startswith("@") else None)
    if not username and value and re.fullmatch(r"[A-Za-z0-9_]{4,}", value or ""):
        username = value
    out["username"] = username
    if not username:
        out["error"] = "Could not parse Telegram"
        return out
    out["invite_url"] = f"https://t.me/{username}"
    if bot is None:
        out["error"] = "Bot unavailable"
        return out
    try:
        chat = await bot.get_chat(f"@{username}")
        out["ok"] = True
        out["title"] = getattr(chat, "title", None) or getattr(chat, "full_name", None)
        out["description"] = getattr(chat, "description", None) or getattr(chat, "bio", None)
        out["type"] = str(getattr(chat, "type", "") or "")
        try:
            out["members"] = await bot.get_chat_member_count(chat.id)
        except Exception:
            pass
    except Exception as exc:
        out["error"] = str(exc)[:160]
    return out


async def collect_sources(parsed: ParsedInput, bot=None) -> dict[str, Any]:
    sources: dict[str, Any] = {
        "project_type": parsed.project_type,
        "extra_context": parsed.extra_context,
        "contracts": parsed.contracts,
        "limitations": [],
    }
    if parsed.websites:
        w = await fetch_website(parsed.websites[0])
        sources["website"] = w
        if not w.get("ok"):
            sources["limitations"].append(f"Website: {w.get('error')}")
        else:
            for u in (w.get("links") or {}).get("x") or []:
                if u not in parsed.x_handles:
                    parsed.x_handles.append(u)
            for u in (w.get("links") or {}).get("telegram") or []:
                if u not in parsed.telegrams:
                    parsed.telegrams.append(u)
    if parsed.x_handles:
        x = await fetch_x(parsed.x_handles[0])
        sources["x"] = x
        if x.get("mode") == "handle_only":
            sources["limitations"].append("X: LIVE X DATA UNAVAILABLE — handle only")
        elif not x.get("ok"):
            sources["limitations"].append(f"X: {x.get('error')}")
    if parsed.telegrams:
        tg = await fetch_telegram(bot, parsed.telegrams[0])
        sources["telegram"] = tg
        if not tg.get("ok"):
            sources["limitations"].append(f"Telegram: {tg.get('error')}")
    if not any([
        (sources.get("website") or {}).get("ok"),
        (sources.get("x") or {}).get("ok"),
        (sources.get("telegram") or {}).get("ok"),
        sources.get("contracts"),
    ]):
        sources["limitations"].append("No usable sources from input")
    if not sources.get("project_type"):
        blob = ((sources.get("website") or {}).get("text_sample") or "") + " "
        blob += str((sources.get("x") or {}).get("bio") or "")
        low = blob.lower()
        if any(k in low for k in ("meme", "pepe", "dog coin", "culture coin")):
            sources["project_type"] = "meme (inferred)"
        elif any(k in low for k in ("dex", "swap", "liquidity", "amm")):
            sources["project_type"] = "dex/defi (inferred)"
        elif any(k in low for k in ("defi", "stake", "lend", "yield")):
            sources["project_type"] = "defi (inferred)"
        elif any(k in low for k in ("launchpad", "ido", "presale")):
            sources["project_type"] = "launchpad (inferred)"
        elif any(k in low for k in ("nft", "collectible")):
            sources["project_type"] = "nft (inferred)"
        elif any(k in low for k in ("ai ", "agent", "llm")):
            sources["project_type"] = "ai (inferred)"
    return sources


def evidence_brief(sources: dict[str, Any]) -> str:
    parts: list[str] = []
    if sources.get("project_type"):
        parts.append(f"PROJECT_TYPE: {sources['project_type']}")
    if sources.get("extra_context"):
        parts.append(f"USER_CONTEXT: {sources['extra_context']}")
    x = sources.get("x")
    if x:
        if x.get("ok"):
            parts.append(
                f"X (@{x.get('handle')}) mode={x.get('mode')}\n"
                f"  name={x.get('name')} bio={x.get('bio')}\n"
                f"  followers={x.get('followers')} tweets={x.get('tweet_count')} url={x.get('url')}\n"
                f"  note={x.get('note')}"
            )
            for t in (x.get("recent_tweets") or [])[:8]:
                m = t.get("metrics") or {}
                parts.append(f"  POST: {t.get('text','')[:220]} [likes={m.get('like_count')}]")
        else:
            parts.append(f"X UNAVAILABLE: {x.get('error')}")
    w = sources.get("website")
    if w:
        if w.get("ok"):
            parts.append(
                f"WEBSITE {w.get('url')}\n  title={w.get('title')}\n  meta={w.get('meta_description')}\n"
                f"  h1={w.get('h1')}\n  headlines={w.get('headlines')[:10]}\n  ctas={w.get('ctas')[:10]}\n"
                f"  links_x={w.get('links',{}).get('x')}\n  links_tg={w.get('links',{}).get('telegram')}\n"
                f"  links_docs={w.get('links',{}).get('docs')}\n  has_community={w.get('has_community_link')}\n"
                f"  text={w.get('text_sample','')[:2500]}"
            )
        else:
            parts.append(f"WEBSITE UNAVAILABLE: {w.get('error')}")
    tg = sources.get("telegram")
    if tg:
        if tg.get("ok"):
            parts.append(
                f"TELEGRAM @{tg.get('username')} title={tg.get('title')} members={tg.get('members')}\n"
                f"  desc={tg.get('description')}"
            )
        else:
            parts.append(f"TELEGRAM UNAVAILABLE: {tg.get('error')}")
    if sources.get("contracts"):
        parts.append(f"CONTRACTS: {sources['contracts']}")
    if sources.get("limitations"):
        parts.append("LIMITATIONS:\n  - " + "\n  - ".join(sources["limitations"]))
    return "\n\n".join(parts) if parts else "No sources."


def sources_label(sources: dict[str, Any]) -> str:
    bits = []
    if (sources.get("website") or {}).get("ok"):
        bits.append("web")
    if (sources.get("x") or {}).get("ok"):
        bits.append(f"X({(sources.get('x') or {}).get('mode')})")
    if (sources.get("telegram") or {}).get("ok"):
        bits.append("TG")
    if sources.get("contracts"):
        bits.append("CA")
    return " · ".join(bits) if bits else "none"


def _fallback(kind: str, sources: dict[str, Any], status: str) -> str:
    has_web = bool((sources.get("website") or {}).get("ok"))
    has_x = bool((sources.get("x") or {}).get("ok"))
    lines = [
        f"⚠️ AI analysis temporarily unavailable ({status}).",
        "Factual sources collected (strategy layer needs AI):",
        f"web={'✓' if has_web else '—'} · X={'✓' if has_x else '—'}",
    ]
    if has_web:
        w = sources["website"]
        lines.append(f"Site: {w.get('title')}")
        if w.get("meta_description"):
            lines.append(f"Meta: {w.get('meta_description')[:200]}")
        if not w.get("has_community_link"):
            lines.append(
                "🕳️ GAP: No obvious public TG/Discord link on website — "
                "add a primary community CTA in header/hero."
            )
        if w.get("ctas"):
            lines.append("CTAs seen: " + ", ".join((c.get("text") or "")[:40] for c in w["ctas"][:5]))
    if has_x:
        lines.append(f"X: @{(sources.get('x') or {}).get('handle')} mode={(sources.get('x') or {}).get('mode')}")
    if sources.get("limitations"):
        lines.append("Limits: " + "; ".join(sources["limitations"][:4]))
    lines.append("Retry in a minute. If this persists, check /settings (key present vs model/rate-limit).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strategy engines
# ---------------------------------------------------------------------------

async def run_marketing_audit(sources: dict[str, Any]) -> tuple[str, str]:
    """Deep Marketing Intelligence Audit — primary engine for /market /marketingaudit."""
    prompt = f"""{GATE}

Produce a full MARKETING INTELLIGENCE AUDIT for this Web3 project.

EVIDENCE:
{evidence_brief(sources)}

You MUST cover these sections (skip only if zero evidence, and say so):

📣 MARKETING INTELLIGENCE AUDIT
PROJECT / CATEGORY / MARKET / CURRENT POSITION (label inferences)

🎯 POSITIONING — Current · Diagnosis · Recommended · Alternatives · example homepage headline · X bio · pinned concept

📖 CORE NARRATIVE — Current · Diagnosis · Recommended pillars · example hooks

💎 VALUE PROPOSITION — Current · Diagnosis · Recommended · proof needed (no price promises)

👥 AUDIENCE — Primary/secondary · objections · message · channel · CTA per segment

🗣️ MESSAGING — Current themes · what to repeat/simplify/remove · pillars · example posts

📈 ACQUISITION — Current channels · missing · recommended channel strategy

✍️ CONTENT — What to post more/less/stop/start/test · series ideas · 3 example posts

💬 COMMUNITY — Onboarding · engagement loops · ATTENTION→PARTICIPATION→CONTRIBUTION→RETENTION→ADVOCACY

🚀 CAMPAIGNS — Existing if evidenced · 2 recommended campaigns with full mechanics

🤝 SOCIAL PROOF — Existing · missing · POTENTIAL TARGET types (never invent existing relationships)

🌐 CRYPTO NARRATIVE — Current vs recommended (only if product supports it)

🏆 COMPETITIVE LANDSCAPE — Brief tiered Web3 comparables (top-tier vs same-stage)

🧩 DIFFERENTIATION — What this project should own

🕳️ GAPS — each: what missing · why · what to do · how · example

💡 OPPORTUNITIES — prioritized practical opportunities

🎯 STRATEGIC DIRECTION — start / more / less / stop / test

⚡ EXECUTION — NOW · NEXT · LATER (concrete)

Every major block: CURRENT → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY.
"""
    text, st = await complete(prompt, max_tokens=4500)
    return text or _fallback("marketingaudit", sources, st), st


async def run_positioning(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Deep POSITIONING strategy (not a website paraphrase).

EVIDENCE:
{evidence_brief(sources)}

Answer:
- What category is it competing in?
- Current positioning & what it communicates
- Unclear / generic / differentiated?
- What should it own in the user's mind?
- Competitor mental territory (Web3 only, brief)
- Positioning gaps
- Strengthen / change / abandon
- Alternatives
- Messaging pillars
- Homepage headline · X bio · pinned post · elevator pitch · CTA examples
"""
    text, st = await complete(prompt, max_tokens=2800)
    return text or _fallback("positioning", sources, st), st


async def run_funnels(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Marketing FUNNEL analysis adapted to this project type.

EVIDENCE:
{evidence_brief(sources)}

Stages (adapt labels to meme vs DeFi vs game vs AI as appropriate):
AWARENESS → INTEREST → CONSIDERATION → TRUST → CONVERSION → ACTIVATION → RETENTION → ADVOCACY

For each stage:
- current evidence
- channels
- weak/missing
- tactics to add
- example content/CTA
- measurement idea

Do not force one generic funnel on every project type.
End with top 5 funnel fixes prioritized.
"""
    text, st = await complete(prompt, max_tokens=3000)
    return text or _fallback("funnels", sources, st), st


async def run_suggest_marketing(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Generate PROJECT-SPECIFIC marketing opportunities (not a generic tactic dump).

EVIDENCE:
{evidence_brief(sources)}

Only recommend tactics that fit this project type, stage, and evidence.
For EACH opportunity:
WHAT · WHY IT FITS · WHO · WHERE · HOW · EXAMPLE · PURPOSE · MEASURE

Consider when relevant: micro-KOLs, X Spaces, AMAs, quests, UGC, referrals, ambassador,
educational threads, ecosystem co-marketing, regional communities, product-led loops.
Do NOT list everything — only what fits.
"""
    text, st = await complete(prompt, max_tokens=3000)
    return text or _fallback("suggestmarketing", sources, st), st


async def run_organic(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
ORGANIC growth plan for this specific project (minimal paid ads).

EVIDENCE:
{evidence_brief(sources)}

Cover: X organic, content loops, community loops, founder-led, product-led, education,
recurring series, partnerships, UGC, ambassadors, Spaces, ecosystem participation.
Concrete plan with weekly rhythm examples. Project-specific only.
"""
    text, st = await complete(prompt, max_tokens=2800)
    return text or _fallback("organic", sources, st), st


async def run_zero(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
$0 BUDGET marketing plan — only actions possible with zero paid spend.

EVIDENCE:
{evidence_brief(sources)}

Specific actions (not "post more"). Include founder content, UGC, Spaces, Reddit contribution,
ecosystem chats, micro-creator outreach (relationship not payment), AMAs, educational threads,
build-in-public, community challenges, referral mechanics that cost $0.
Each: WHAT · HOW · WHO · SUCCESS SIGNAL
"""
    text, st = await complete(prompt, max_tokens=2600)
    return text or _fallback("zeromarketing", sources, st), st


async def run_campaigns(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Campaign strategy.

EVIDENCE:
{evidence_brief(sources)}

Existing campaigns only if evidenced.
Propose 3 campaigns. Each: name, objective, audience, concept, mechanism, message,
X / TG / community / creator execution, assets, CTA, timeline, measurement, why it fits.
"""
    text, st = await complete(prompt, max_tokens=2800)
    return text or _fallback("campaigns", sources, st), st


async def run_marketgaps(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Marketing GAPS only if evidenced.

EVIDENCE:
{evidence_brief(sources)}

Each gap: FACT → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY
Tags: QUICK WIN | HIGH IMPACT/LOW EFFORT | LONGER-TERM
Cover positioning, messaging, content, audience, website, community, funnel, social proof, acquisition.
"""
    text, st = await complete(prompt, max_tokens=2600)
    return text or _fallback("marketgaps", sources, st), st


async def run_opportunities(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Prioritized opportunities from evidence.

EVIDENCE:
{evidence_brief(sources)}

Categories: QUICK WIN · HIGH IMPACT/LOW EFFORT · HIGH IMPACT/HIGH EFFORT · LONGER-TERM
Each: WHAT · WHY · HOW · WHO · WHERE · SUCCESS LOOKS LIKE
Ban empty "build community" without mechanism.
"""
    text, st = await complete(prompt, max_tokens=2600)
    return text or _fallback("opportunities", sources, st), st


async def run_report(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Comprehensive strategy report (mobile-scannable, not a novel).

EVIDENCE:
{evidence_brief(sources)}

Include: Executive strategy · Current situation · Marketing diagnosis · Positioning · Narrative ·
Value prop · Audience · X · Community · Website/conversion · Funnel snapshot · Campaign ·
Competitive tiers (brief) · Gaps · Opportunities · Organic + $0 highlights · Content pillars ·
Action plan NOW/NEXT/LATER with concrete actions and examples.
"""
    text, st = await complete(prompt, max_tokens=4500)
    return text or _fallback("report", sources, st), st


async def run_compare(sa: dict[str, Any], sb: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Compare two Web3 projects. No overall winner score.

A:
{evidence_brief(sa)}

B:
{evidence_brief(sb)}

Dimensions: Positioning, Product, Website/UX, Messaging, X, Content, Community, Marketing,
Campaigns, Acquisition, Funnel, Audience, Differentiation, Growth.
End with WHAT A LEARNS FROM B and WHAT B LEARNS FROM A (concrete adaptations).
"""
    text, st = await complete(prompt, max_tokens=3200)
    return text or "Compare failed — AI unavailable.", st


async def run_competitor_focus(sources: dict[str, Any], focus: str) -> tuple[str, str]:
    prompt = f"""{GATE}
{COMPETITOR_RULES}

Subject evidence:
{evidence_brief(sources)}

Competitor focus: {focus}

Deep-dive this comparable (Web3 only):
What it is, tier, marketing mechanisms (not just "strong brand"),
what works and WHY, what subject can adapt at its stage, what NOT to copy,
3 concrete tests. Provide Website/X/TG if known or "Not found / not publicly verified".
"""
    text, st = await complete(prompt, max_tokens=2800)
    return text or _fallback("competitor", sources, st), st


MODES = {
    "similar": "Most relevant comparable Web3 projects.",
    "product": "Similar products / problem solved.",
    "architecture": "Similar architecture/mechanism.",
    "social": "Stronger X/social execution (relevant category).",
    "marketing": "Stronger marketing execution.",
    "positioning": "Clearer positioning examples.",
    "ux": "Stronger website/product UX.",
    "community": "Stronger community/support.",
    "growth": "Notable growth/campaign strategies.",
    "product_leaders": "Stronger product experience/value delivery.",
    "samestage": "Emerging / same-stage growth comparables (prioritize).",
}


async def discover_competitors(
    sources: dict[str, Any],
    *,
    mode: str = "similar",
    exclude: list[str] | None = None,
    batch_size: int = 6,
) -> tuple[str, str, list[str]]:
    exclude = exclude or []
    mode_desc = MODES.get(mode, MODES["similar"])
    prompt = f"""{GATE}
{COMPETITOR_RULES}

COMPETITOR DISCOVERY mode={mode}: {mode_desc}

SUBJECT:
{evidence_brief(sources)}

ALREADY SHOWN (do not repeat names/aliases/URLs):
{exclude if exclude else "(none)"}

Return {batch_size} NEW real Web3 competitors.
MUST include mix of tiers when possible — do NOT only list Uniswap-scale giants.
Prefer at least 2 🟡 EMERGING/SAME-STAGE or 🟢 MID-TIER when mode allows.

Template per competitor:
🏆 [Name] — tier emoji
Confidence · Research status
Why comparable · Website · X · Telegram · Chain
Stronger at (dimension) · Learn · Adapt · Don't copy

Then: 📋 3 NEXT ACTIONS for subject
Final line only: NAMES: name1 | name2 | ...
"""
    text, st = await complete(prompt, max_tokens=3800)
    if not text:
        return _fallback("competition", sources, st), st, []

    urls = re.findall(r"https?://[^\s\)\]\>]+", text)
    notes = []
    seen = set()
    for url in urls[:5]:
        if url in seen or "t.me/" in url or "x.com/" in url or "twitter.com" in url:
            continue
        seen.add(url)
        w = await fetch_website(url)
        if w.get("ok"):
            notes.append(f"✓ Live-checked {url} — {w.get('title') or 'ok'}")
        else:
            notes.append(f"✗ Live-check failed {url}: {w.get('error')}")

    names: list[str] = []
    m = re.search(r"NAMES:\s*(.+)$", text, re.I | re.M)
    if m:
        names = [n.strip() for n in m.group(1).split("|") if n.strip()]
        text = re.sub(r"\n?NAMES:\s*.+$", "", text, flags=re.I | re.M).strip()

    if notes:
        text += "\n\n🔍 Live verification:\n" + "\n".join(notes)
    text += (
        "\n\n⚠️ AI shortlist + best-effort site checks. "
        "INFERRED candidates are not fully live-verified. Use More / mode buttons."
    )
    return text, st, names
