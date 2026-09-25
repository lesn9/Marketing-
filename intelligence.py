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

SYSTEM = """You are an elite Web3 marketing strategist and growth analyst.
Never expose internal planning, step lists, or "thinking process".
Never invent metrics, followers, partnerships, campaigns, or competitors.
Never speak as the project. Default user role: external marketer/observer.
Output finished intelligence only — Telegram-readable, human, specific.
"""

GATE = """
QUALITY GATE:
- Research-backed only. Never invent followers, TG members, partnerships, metrics, or product claims.
- FACT vs INFERENCE: label inferences. Missing data = UNAVAILABLE, not "inactive".
- User is EXTERNAL (outside the project) unless they explicitly say otherwise. Never speak AS the project.
- Project-specific: if a line could apply to any Web3 project, rewrite it.
- Telegram-native: short blocks, no markdown tables, no giant numbered lists, no corporate AI phrases
  ("leverage", "maximize", "in today's landscape", "excellent opportunity").
- Prefer 3–4 priorities over dumping every channel/tactic.
"""

COMPETITOR_RULES = """
COMPETITORS — relevance first, not keywords.

A candidate is a competitor ONLY if someone interested in THIS project might reasonably
consider the other instead (same product need, audience, behavior, or narrative space).

NOT enough: both Web3, both AI, both have tokens, both have Telegram, both "gaming".

Categories (do not mix):
DIRECT — meaningful product/use-case overlap
INDIRECT — different product, same attention/audience/narrative
ATTENTION BENCHMARK — not a competitor; useful attention comparison
MARKETING BENCHMARK — tactic study only
ECOSYSTEM COMPARABLE — structural, not competitive

Zero direct competitors is VALID. Never force 4 names.
Never include the subject project itself.
Never recycle Golem/Render/Chainlink/Ankr/Ocean unless research shows real fit.

For each kept candidate:
Why comparable (1–2 specific sentences)
Overlap dimensions
What they do (factual)
What to study / What NOT to copy
Source / ⚠️ INFERRED if not verified
"""



def scrub_internal(text: str) -> str:
    """Remove leaked chain-of-thought / session metadata from model output."""
    if not text:
        return text or ""
    import re as _re
    # Drop "thinking process" blocks
    text = _re.sub(
        r"(?is)here'?s? a thinking process:.*?(?=\n🏆|\n📊|\n📣|\n🎯|\n⚡|\n🔎|$)",
        "",
        text,
    )
    text = _re.sub(r"(?im)^\s*(batch\s*\d+|session\s+[a-f0-9]+|user safety:.*|ai shortlist.*|best-effort.*)\s*$", "", text)
    text = _re.sub(r"(?im)^\s*(new=\d+\s*·\s*shown=\d+|sources:\s*web)\s*$", "", text)
    text = _re.sub(r"(?im)^\s*\d+\.\s*Analyze the .+\s*$", "", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


LAST_AI_ERROR: str = ""
AI_ATTEMPTS: list[str] = []  # internal diagnostics for /settings

# One recovery model if Railway still has a deprecated id (404 model_not_found)
CURRENT_FALLBACKS = {
    "groq": "openai/gpt-oss-20b",
    "groq2": "openai/gpt-oss-20b",
    "gemini": "gemini-3.5-flash",
    "cerebras": "llama3.1-8b",
    "openrouter": "openrouter/free",
    "openrouter2": "openrouter/free",
}


def _any_ai_key() -> bool:
    return bool(
        config.GROQ_API_KEY
        or config.GROQ_API_KEY_2
        or config.OPENROUTER_API_KEY
        or config.OPENROUTER_API_KEY_2
        or config.GEMINI_API_KEY
        or config.CEREBRAS_API_KEY
    )


def _classify_http(status_code: int) -> str:
    if status_code in (401, 403):
        return "AI_AUTH_FAILED"
    if status_code == 429:
        return "AI_RATE_LIMITED"
    if status_code == 404:
        return "AI_MODEL_ERROR"
    if status_code >= 500:
        return "AI_PROVIDER_ERROR"
    if status_code >= 400:
        return "AI_MODEL_ERROR"
    return "AI_PROVIDER_ERROR"


async def complete(prompt: str, *, max_tokens: int = 2200) -> tuple[str | None, str]:
    """One configured model per provider. Fail → next provider. No stale hardcoded models."""
    global LAST_AI_ERROR, AI_ATTEMPTS
    AI_ATTEMPTS = []
    errors: list[str] = []
    mt = min(max_tokens, 2000)
    prompt = (prompt or "")[:22000]

    or_extra = {
        "HTTP-Referer": "https://github.com/web3-marketing-intel",
        "X-Title": "Web3 Marketing Intelligence",
    }

    # (name, kind, key, model, extra)
    chain: list[tuple[str, str, str, str, dict | None]] = []
    if config.GROQ_API_KEY and config.GROQ_MODEL:
        chain.append(("groq", "openai", config.GROQ_API_KEY, config.GROQ_MODEL, None))
    if config.GROQ_API_KEY_2:
        m2 = config.GROQ_MODEL_2 or config.GROQ_MODEL
        if m2:
            chain.append(("groq2", "openai", config.GROQ_API_KEY_2, m2, None))
    if config.GEMINI_API_KEY and config.GEMINI_MODEL:
        chain.append(("gemini", "gemini", config.GEMINI_API_KEY, config.GEMINI_MODEL, None))
    if config.CEREBRAS_API_KEY and config.CEREBRAS_MODEL:
        chain.append(("cerebras", "openai", config.CEREBRAS_API_KEY, config.CEREBRAS_MODEL, None))
    if config.OPENROUTER_API_KEY and config.OPENROUTER_MODEL:
        chain.append(("openrouter", "openai", config.OPENROUTER_API_KEY, config.OPENROUTER_MODEL, or_extra))
    if config.OPENROUTER_API_KEY_2 and config.OPENROUTER_MODEL:
        chain.append(("openrouter2", "openai", config.OPENROUTER_API_KEY_2, config.OPENROUTER_MODEL, or_extra))

    if not chain:
        LAST_AI_ERROR = "no keys or models configured"
        return None, "AI_NOT_CONFIGURED"

    def _url_for(name: str) -> str:
        if name.startswith("groq"):
            return "https://api.groq.com/openai/v1/chat/completions"
        if name.startswith("cerebras"):
            return "https://api.cerebras.ai/v1/chat/completions"
        return "https://openrouter.ai/api/v1/chat/completions"

    for name, kind, key, model, extra in chain:
        models_to_try = [model]
        fb = CURRENT_FALLBACKS.get(name)
        if fb and fb != model:
            models_to_try.append(fb)

        text, status, detail = None, "AI_PROVIDER_ERROR", ""
        for mid in models_to_try:
            if kind == "gemini":
                text, status, detail = await _gemini_one(key, mid, prompt, mt)
            else:
                text, status, detail = await _openai_one(
                    _url_for(name), key, mid, prompt, mt, extra=extra,
                )
            AI_ATTEMPTS.append(f"{name}|{mid}|{status}|{detail[:80]}")
            log.info("AI attempt %s model=%s status=%s detail=%s", name, mid, status, detail[:120])
            if text:
                LAST_AI_ERROR = ""
                return scrub_internal(text), f"ok:{name}:{mid}"
            # Only recover once on model-not-found; other errors → next provider
            if status != "AI_MODEL_ERROR":
                break
        errors.append(f"{name}:{status}:{detail}")

    LAST_AI_ERROR = " | ".join(errors)[:600]
    log.warning("AI all failed: %s", LAST_AI_ERROR)
    # Prefer most specific overall status
    joined = " ".join(errors)
    if "AI_AUTH_FAILED" in joined:
        return None, "AI_AUTH_FAILED"
    if "AI_RATE_LIMITED" in joined or "429" in joined:
        return None, "AI_RATE_LIMITED"
    if "AI_TIMEOUT" in joined:
        return None, "AI_TIMEOUT"
    if "AI_MODEL_ERROR" in joined:
        return None, "AI_MODEL_ERROR"
    return None, "AI_PROVIDER_ERROR"


async def _gemini_one(
    key: str, model: str, prompt: str, max_tokens: int
) -> tuple[str | None, str, str]:
    """Single Gemini model via native generateContent."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key.strip()}"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{
                            "role": "user",
                            "parts": [{
                                "text": (
                                    "You are a Web3 marketing strategist. "
                                    "Be specific and evidence-based.\\n\\n" + prompt
                                )[:30000]
                            }],
                        }],
                        "generationConfig": {
                            "temperature": 0.45,
                            "maxOutputTokens": max_tokens,
                        },
                    },
                )
            except httpx.TimeoutException:
                return None, "AI_TIMEOUT", f"{model}:timeout"
            except Exception as exc:
                return None, "AI_PROVIDER_ERROR", f"{model}:{exc}"

            if resp.status_code >= 400:
                kind = _classify_http(resp.status_code)
                return None, kind, f"{model}:{resp.status_code}:{(resp.text or '')[:160]}"

            data = resp.json() if resp.content else {}
            try:
                parts = (
                    ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")
                    or []
                )
                content = "".join(
                    (p.get("text") or "") for p in parts if isinstance(p, dict)
                ).strip()
            except Exception as exc:
                return None, "AI_PROVIDER_ERROR", f"{model}:parse:{exc}"
            if content:
                return content, "ok", model
            return None, "AI_PROVIDER_ERROR", f"{model}:empty"
    except Exception as exc:
        return None, "AI_PROVIDER_ERROR", str(exc)[:160]


async def _openai_one(
    url: str,
    key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    extra: dict | None = None,
) -> tuple[str | None, str, str]:
    """Single OpenAI-compatible chat completion (Groq / Cerebras / OpenRouter)."""
    headers = {
        "Authorization": f"Bearer {key.strip()}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
                            {"role": "user", "content": prompt[:22000]},
                        ],
                    },
                )
            except httpx.TimeoutException:
                return None, "AI_TIMEOUT", f"{model}:timeout"
            except Exception as exc:
                return None, "AI_PROVIDER_ERROR", f"{model}:{exc}"

            if resp.status_code >= 400:
                kind = _classify_http(resp.status_code)
                return None, kind, f"{model}:{resp.status_code}:{(resp.text or '')[:160]}"

            try:
                data = resp.json() if resp.content else {}
            except Exception:
                return None, "AI_PROVIDER_ERROR", f"{model}:bad_json"

            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                return None, "AI_PROVIDER_ERROR", f"{model}:no_choices"

            msg = (choices[0] or {}).get("message") or {}
            text = msg.get("content") or ""
            if isinstance(text, list):
                text = "".join(
                    (p.get("text") if isinstance(p, dict) else str(p)) for p in text
                )
            text = str(text).strip()
            if text:
                return text, "ok", model
            return None, "AI_PROVIDER_ERROR", f"{model}:empty"
    except Exception as exc:
        return None, "AI_PROVIDER_ERROR", str(exc)[:160]




# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

PROJECT_TYPES = {
    "meme", "defi", "nft", "gamefi", "ai", "rwa", "depin", "infra",
    "l2", "wallet", "exchange", "social", "dao", "tooling", "other",
}


@dataclass
class ParsedInput:
    raw: str = ""
    x_handles: list = field(default_factory=list)
    websites: list = field(default_factory=list)
    telegrams: list = field(default_factory=list)
    contracts: list = field(default_factory=list)
    project_type: str | None = None
    extra_context: str | None = None
    competitor_focus: str | None = None


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


def _parse_html_site(url: str, html: str, out: dict[str, Any]) -> dict[str, Any]:
    soup = BeautifulSoup(html[:220_000], "lxml")
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
    return out


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
    # Strip trailing junk / fix common typos in user paste
    url = url.strip().rstrip(").,]}")
    out["url"] = url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    last_err = None
    for verify in (True, False):
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, verify=verify, headers=headers,
            ) as client:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                return _parse_html_site(str(resp.url), resp.text, out)
        except Exception as exc:
            last_err = str(exc)[:160]
            log.warning("website verify=%s %s: %s", verify, url, exc)
            continue
    out["error"] = last_err or "fetch failed"
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
        "Sources collected OK — the AI API call failed (not the website scrape).",
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
    if has_x:
        lines.append(f"X: @{(sources.get('x') or {}).get('handle')} mode={(sources.get('x') or {}).get('mode')}")
    if LAST_AI_ERROR:
        lines.append(f"Detail: {LAST_AI_ERROR[:300]}")
    if status == "AI_RATE_LIMITED":
        lines.append(
            "⏳ Rate limit — wait 1–5 minutes. Free Groq/OpenRouter limits are easy to hit "
            "after a few long /market or /funnels reports. Try again shortly."
        )
    else:
        lines.append(
            "Retry in 1–2 minutes. Check Groq console usage / OpenRouter credits. "
            "Optional: set GROQ_MODEL=llama-3.1-8b-instant"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strategy engines
# ---------------------------------------------------------------------------

async def run_marketing_audit(sources: dict[str, Any]) -> tuple[str, str]:
    """Deep Marketing Intelligence Audit — primary engine for /market /marketingaudit."""
    prompt = f"""{GATE}
{HUMAN_VOICE}
{PARTNERSHIP_RULES}

Produce a MARKETING AUDIT for this project (Telegram-readable).

Start with:
⚡ QUICK TAKE

Then only sections you have evidence for:
CURRENT STATE · WHAT'S WORKING · MAIN GAPS · PRIORITIES (max 4) · DO THIS FIRST

No markdown tables. No dumping every channel. No inventing social metrics.
Missing source data = UNAVAILABLE. User is external observer.

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

🤝 PARTNERSHIPS & COLLABORATIONS — 3–4 fit-checked ideas (potential partners only unless evidenced)

🎯 STRATEGIC DIRECTION — start / more / less / stop / test

⚡ EXECUTION — NOW · NEXT · LATER (concrete)

Every major block: CURRENT → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY.
"""
    text, st = await complete(prompt, max_tokens=2000)
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
    text, st = await complete(prompt, max_tokens=2000)
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
    text, st = await complete(prompt, max_tokens=2200)
    return text or _fallback("funnels", sources, st), st


SUGGEST_LIBRARY = """
SELECT only fitting tactics (never dump entire catalogue):
Awareness, Community, Partnerships, X growth, Acquisition, Product-led, Campaigns,
Education, Regional/ecosystem, PR/distribution, Creator/KOL, Retention, Discovery, $0/organic.
"""

PROOF_RULES = """
For each strong idea include:
💡 Idea · 🎯 Why it fits THIS project · 🧪 Real Web3 example · 🔗 Proof
(prefer official X/TG/blog; if only agency/third-party say so; if none: ⚠️ Inference — no documented result)
📈 What happened (only if documented) · 🧠 Why it may have worked · 🔄 How to adapt · ⚠️ Transferability
NEVER invent metrics or case studies.
"""


async def run_suggest_marketing(
    sources: dict[str, Any],
    *,
    focus: str = "best",
    prior: str = "",
) -> tuple[str, str]:
    focus_help = {
        "best": "Top 4 opportunities to test first",
        "awareness": "Awareness & reach only",
        "community": "Community growth only",
        "partnerships": "Partnerships & collabs only",
        "x": "X growth only",
        "acquisition": "Acquisition only",
        "product": "Product-led only",
        "campaigns": "Campaigns & activations only",
        "education": "Education & authority only",
        "regional": "Ecosystem & regional only",
        "pr": "PR & distribution only",
        "creators": "Creator/KOL only",
        "retention": "Retention & advocacy only",
        "discovery": "Discovery & search only",
        "zero": "$0 / organic only",
        "more": "More ideas without repeating prior",
        "proof": "Deepen proof for PRIOR ideas",
        "adapt": "Adaptation plans for PRIOR ideas",
    }.get(focus, "Top opportunities")
    prompt = f"""{GATE}
{HUMAN_VOICE}
{PARTNERSHIP_RULES}
{PROOF_RULES}
{SUGGEST_LIBRARY}

/suggestmarketing FOCUS={focus} ({focus_help})

EVIDENCE:
{evidence_brief(sources)}

{"PRIOR:\n" + prior[:2800] if prior else ""}

Output:
💡 Suggested Marketing — why these fit now
Then 3–4 ideas each with full proof block above.
No generic influencer/post-more advice. No fabricated case studies.
"""
    text, st = await complete(prompt, max_tokens=2400)
    return text or _fallback("suggestmarketing", sources, st), st


async def run_partnerships(
    sources: dict[str, Any],
    *,
    mode: str = "ideas",
    prior: str = "",
) -> tuple[str, str]:
    mode_help = {
        "ideas": "Best-fit partnership & collaboration ideas",
        "project": "Project-to-project / protocol partnerships",
        "ecosystem": "Chain/ecosystem collaborations",
        "integration": "Product/wallet/DEX/integration opportunities",
        "creator": "Creator / micro-KOL collaboration angles",
        "community": "Community-to-community collaborations",
        "spaces": "X Spaces / AMA / podcast formats",
        "media": "Media / newsletter / PR angles",
        "campaign": "Joint campaign / quest / competition ideas",
        "cross": "Cross-promotion with mutual benefit",
        "pitch": "3–4 natural outreach pitches to POTENTIAL partners",
        "plan": "Campaign plan for the top partnership idea",
        "best": "Only the highest-fit 3 options, ranked",
    }.get(mode, "Partnership ideas")
    prompt = f"""{GATE}
{HUMAN_VOICE}
{PARTNERSHIP_RULES}

TASK: Partnership & collaboration intelligence.
MODE: {mode} — {mode_help}

EVIDENCE:
{evidence_brief(sources)}

{"PRIOR:\n" + prior[:2000] if prior else ""}

Give 3–4 DISTINCT options with WHAT · WHY FIT · WHO · FORMAT · EXECUTION · PURPOSE · MEASURE.
Never invent existing relationships. Prefer partner types unless a name is in evidence.
"""
    text, st = await complete_fast(prompt, max_tokens=1800)
    return text or _fallback("partnerships", sources, st), st


async def run_organic(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
ORGANIC growth for THIS project only (minimal paid).

Telegram format:
⚡ QUICK TAKE (1–3 sentences)
🔎 CURRENT STATE (short bullets of verified observations only)
🎯 MAIN GAPS (3 max)
🚀 WHAT I WOULD DO (3–4 priorities: TITLE / What / Why / How)
👉 DO THIS FIRST (one action)
No markdown tables. No generic "post more / host AMA / use KOLs" without project-specific why.
If X/TG activity was not verified, say UNAVAILABLE — do not call it inactive.

EVIDENCE:
{evidence_brief(sources)}

Cover: X organic, content loops, community loops, founder-led, product-led, education,
recurring series, partnerships, UGC, ambassadors, Spaces, ecosystem participation.
Concrete plan with weekly rhythm examples. Project-specific only.
"""
    text, st = await complete(prompt, max_tokens=2000)
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
    text, st = await complete(prompt, max_tokens=2000)
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
    text, st = await complete(prompt, max_tokens=2000)
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
    text, st = await complete(prompt, max_tokens=2200)
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
    text, st = await complete(prompt, max_tokens=2000)
    return text or _fallback("competitor", sources, st), st


MODES = {
    "similar": "Most relevant comparable Web3 projects by product + audience.",
    "product": "Similar product / problem solved — different projects than other modes.",
    "architecture": "Similar architecture/mechanism — not the same list as product mode.",
    "social": "Projects that execute better on X/social in a related niche — NEW names.",
    "marketing": "Projects with stronger marketing execution — NEW names, not social duplicates.",
    "positioning": "Clearer positioning examples — NEW names.",
    "ux": "Stronger website/product UX — NEW names.",
    "community": "Stronger community systems — NEW names, not growth/social duplicates.",
    "growth": "Notable growth tactics — NEW names, not community/social duplicates.",
    "product_leaders": "Stronger product experience — NEW names.",
    "samestage": "Same-stage comparables only — avoid giants unless truly same stage.",
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

Return up to {batch_size} NEW candidates only if genuinely comparable.\nCRITICAL: mode={mode} means pick projects that excel on THAT dimension.\nDo NOT reuse the same projects across modes. Different mode → different projects.\nIf mode is community → projects known for community. social → strong X. growth → growth tactics.\nproduct → similar product. architecture → similar mechanism. samestage → similar stage.\n
Zero is valid if none pass the relevance test.
Do NOT invent bird-themed tokens or keyword matches.
Do NOT include subject project itself.

Template per competitor (clean user output — no session/batch/safety metadata):
🏆 NAME
Type: Direct / Indirect / Attention benchmark / Marketing benchmark
Why it matters: 1–2 specific sentences
🌐 Website: https://... (required if known, else Not publicly verified)
𝕏 X: @handle or Not publicly verified
Community: Telegram/Discord if verified, else Not publicly verified
What they do: short factual
What to study: specific
What NOT to copy: specific

Then if useful: 👉 What subject can adapt
Final line only: NAMES: name1 | name2 | ...
"""
    text, st = await complete(prompt, max_tokens=3800)
    text = scrub_internal(text or "")
    if not text:
        return scrub_internal(_fallback("competition", sources, st)), st, []

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
    if "⚠️" not in text and "INFERRED" in text.upper():
        text += "\n\n⚠️ Some candidates are inferred — verify websites before acting."
    return text, st, names


# ---------------------------------------------------------------------------
# Proposal + conversational reply / shuffle engines (fast, human)
# ---------------------------------------------------------------------------

HUMAN_VOICE = """
HUMAN VOICE — NON-NEGOTIABLE.
Sound like a real marketer who looked at THIS project.
Ban: "I believe", "I'm excited", "strong opportunity", "leverage", "maximize", "unlock",
"drive engagement", "increase visibility", "build brand awareness", "strategic partnerships",
"robust community", "in today's competitive landscape", "take it to the next level",
"I would recommend", "the project should consider", "this presents an excellent opportunity",
"by leveraging", "to maximize", "synergy", "game-changing", "high-impact", "seamlessly".
Prefer: "I'd test…", "I'd leave alone…", "Honestly I wouldn't…", "The interesting part is…",
"If this were mine…", "I'd start here…".
Never show thinking process / numbered internal steps / "Analyze the Request".

Write like a sharp human Web3 marketer who looked at THIS project — not corporate AI.
Ban: "excellent opportunity", "leverage", "in today's landscape", "significantly enhance",
"maximize growth", "robust strategy", "drive engagement", "build awareness", "foster community".
Prefer: "One thing I'd test…", "I'd lean into…", "Honestly I'd…", "There's a gap between…"

ROLE DEFAULT: user is OUTSIDE the project (external marketer / observer).
Never speak as the project ("Join our…", "We're launching…").
Never assume user is a customer, investor, partner, or employee unless they say so.

USER POV = knowledgeable outsider raising a useful observation to the team.
MARKETER POV = external marketer pointing at a growth opportunity.
DEV DM = outreach about marketing/growth — NOT "I want to use your product".
JOB PITCH = only when explicitly requested.
X REPLY = something that fits under a project post.
"""

PARTNERSHIP_RULES = """
🤝 PARTNERSHIP & COLLABORATION
Never only say "partner with influencers/projects."
Evaluate fit: audience overlap · complementary product · ecosystem relevance · mutual benefit · realistic · format.
Labels: POTENTIAL partner | OBSERVED (only if evidenced) | INFERRED candidate.
Consider when relevant: protocol/ecosystem, integrations, wallets/DEX, chain collabs, micro-KOLs,
community-to-community, Spaces/AMAs, podcasts/media, regional, joint quests/campaigns, referrals, launch partners.
Each idea: WHAT · WHY FIT · WHO (type) · FORMAT · EXECUTION · PURPOSE · MEASURE.
"""


async def complete_fast(prompt: str, *, max_tokens: int = 1400) -> tuple[str | None, str]:
    """Shorter path for replies/proposals — prioritizes speed."""
    return await complete(prompt, max_tokens=max_tokens)


async def run_marketing_proposals(
    sources: dict[str, Any],
    *,
    style: str = "full",
    prior_text: str = "",
) -> tuple[str, str]:
    """Human proposals — never agency templates, tables, or fake org charts."""
    style = (style or "full").lower()
    style_rules = {
        "full": (
            "One coherent proposal a marketer would paste into a doc for a team. "
            "Max ~350 words. Sections only if needed: What I noticed / What I'd do / First 2 weeks. "
            "NO tables. NO owner columns. NO 'Marketing Lead'."
        ),
        "short": "8–12 lines max. What I noticed + what I'd test first + one CTA.",
        "founder_dm": (
            "FOUNDER/DEV DM only. 2–3 options. Each = a message someone could send in TG/X DM. "
            "Conversational. No résumé. No 'I'm ready to jump in'. No team org chart."
        ),
        "x_dm": "3 short X DM openers, each under 280 characters. Natural.",
        "email": "One short human email (not a deck). Subject line + body.",
        "job": (
            "JOB/SERVICE PITCH: external marketer offering help. Natural. Specific to THIS project. "
            "2 options. Not a CV dump. Not 'comprehensive Web3 marketing strategies'."
        ),
        "partner": "Partnership outreach message — mutual value, specific. 2 options.",
        "30day": "Loose 30-day plan in plain sentences/weeks. NO markdown tables. NO role assignments.",
        "quick": "What I noticed (2 lines) + what I'd do (3 lines) + first step (1 line).",
    }.get(style, "Short human proposal.")

    prompt = f"""{HUMAN_VOICE}

You write messages a real Web3 marketer would actually send.
NEVER use markdown tables.
NEVER invent team roles (Marketing Lead, Community Manager, Analytics Lead).
NEVER write "I'm ready to jump in" / "get the community buzzing" / "comprehensive strategy".
NEVER write generic airdrop+AMA+Discord playbooks unless the research specifically supports them.
If social/community data was NOT verified, say that — do not invent TG/Discord plans as if they are missing for sure.

STYLE: {style}
{style_rules}

PROJECT EVIDENCE (only use what is here):
{evidence_brief(sources)}

{"REFINE THIS PRIOR TEXT (keep same facts, make more human):\n" + prior_text[:2000] if prior_text else ""}

Could this text be sent unchanged to 500 random projects? If yes, rewrite until it is specific to THIS project.
Output finished copy only. No thinking process.
"""
    text, st = await complete_fast(prompt, max_tokens=1400)
    text = scrub_internal(text or "")
    # Strip markdown tables if model still emits them
    if text and "|" in text and "---" in text:
        lines = []
        for ln in text.splitlines():
            if ln.strip().startswith("|") or set(ln.strip()) <= set("|-: "):
                continue
            lines.append(ln)
        text = "\n".join(lines).strip()
    return text or _fallback("proposals", sources, st), st


async def run_reply_assistant(
    sources: dict[str, Any] | None,
    user_request: str,
    *,
    mode: str = "auto",
    tone: str = "",
    perspective: str = "",
    prior_options: list[str] | None = None,
) -> tuple[str, str]:
    """Generate actual sendable replies — NEVER a marketing audit."""
    evidence = evidence_brief(sources) if sources else "(no project research — answer from user text only)"
    # Cap evidence so model cannot expand into a full audit
    if len(evidence) > 1800:
        evidence = evidence[:1800] + "\n…(truncated)"
    prior = prior_options or []
    mode_map = {
        "auto": "Infer: direct reply / X reply / community / founder DM / marketing observation",
        "x_reply": "X REPLY only — short text for under a project post",
        "community": "COMMUNITY REPLY — natural TG/Discord message",
        "dev_dm": "FOUNDER/DEV DM — marketing/growth outreach, not product-usage request",
        "observation": "Short marketing observation the user can drop in chat",
        "job": "Service/job pitch — only this mode may sell the user's marketing help",
        "reply": "Natural conversational reply to what was said",
    }
    prompt = f"""{HUMAN_VOICE}

TASK: /reply — conversational RESPONSE generator.
You write messages the user can SEND or POST.
You do NOT write marketing audits, competitor lists, CURRENT STATE, DIAGNOSIS, EXECUTION, WHY sections, or weekly plans.

USER REQUEST / MESSAGE TO RESPOND TO:
{user_request}

MODE: {mode} — {mode_map.get(mode, mode)}
TONE: {tone or "natural"}
PERSPECTIVE: {perspective or "external outsider"}

OPTIONAL PROJECT CONTEXT (use only if it helps the reply; do not expand into research):
{evidence}

AVOID repeating these prior options:
{prior[:6] if prior else "(none)"}

OUTPUT RULES:
- 2–3 options max. Each option = 1–4 sentences.
- Label: Option 1 / Option 2 / Option 3
- Respond to WHAT WAS SAID or answer the user's ask directly.
- External marketer/observer by default — never speak as the project.
- No tables, no competitor tiering, no "CURRENT STATE".
- If mode is x_reply: write under-the-post style only.
- If mode is dev_dm: founder outreach about a marketing/growth idea.
- If mode is job: only then position user as offering marketing help.
"""
    text, st = await complete_fast(prompt, max_tokens=900)
    if not text:
        return (
            f"⚠️ AI unavailable ({st}). Retry shortly.\n{LAST_AI_ERROR[:180]}",
            st,
        )
    return text, st


async def run_shuffle(
    original: str,
    *,
    instruction: str = "genuinely different human variations",
    sources: dict[str, Any] | None = None,
    prior: list[str] | None = None,
) -> tuple[str, str]:
    prompt = f"""{HUMAN_VOICE}

Shuffle the text below into 3–4 GENUINELY different human variations.
Same intelligence and facts. Different voice, structure, approach.
NOT synonym swaps. NOT corporate AI tone.

INSTRUCTION: {instruction}

ORIGINAL:
{original[:3500]}

PROJECT CONTEXT (optional):
{evidence_brief(sources) if sources else "(none)"}

AVOID repeating these prior variations:
{prior or "(none)"}

Output:
Option 1 — [label]
...
Option 2 — [label]
...
Option 3 — [label]
"""
    text, st = await complete_fast(prompt, max_tokens=1500)
    return text or f"⚠️ Shuffle failed ({st}). Wait and retry.", st


def extract_options(text: str) -> list[str]:
    """Pull Option N blocks for variation memory."""
    parts = re.split(r"(?i)(?:^|\n)\s*option\s*\d+", text or "")
    out = []
    for p in parts[1:]:
        p = p.strip(" -—:\n")
        if p:
            out.append(p[:500])
    return out[:12]
