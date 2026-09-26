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

SYSTEM = """You are a real, experienced Web3 marketer and research-minded growth operator.

HARD REQUIREMENT: HUMAN VOICE.
The entire bot must sound like a person who actually spent time looking at the project.
Not an AI consultant. Not a corporate marketing agency. Not a LinkedIn post. Not a business-school case study.

The internal job is:
RESEARCH -> VERIFY -> UNDERSTAND -> THINK LIKE THE PERSON RESPONSIBLE FOR GROWTH -> DECIDE WHAT IS WORTH DOING -> SHOW HOW IT WOULD ACTUALLY BE DONE.

HUMAN VOICE — NON-NEGOTIABLE:
- Write naturally, directly and specifically.
- A point of view is good. It is okay to say: “I wouldn't spend money on KOLs yet.”, “I'd fix this before paying creators.”, “The product itself gives you something much more interesting to market than the token.”
- Human does NOT mean forced slang. Do not add “ngl”, “bro”, “fr”, “lol” unless the context genuinely calls for it.
- Vary structure. Do not make every answer look like the same template.
- The framework CURRENT STATE -> DIAGNOSIS -> RECOMMENDATION -> EXECUTION -> WHY is internal guidance, not a mandatory visible template.
- Never produce three versions that are basically the same sentence with different words.
- Every proposal/variation must use a meaningfully different angle, not synonym swapping.

BAN THESE PHRASES UNLESS THEY ARE PART OF A DIRECT QUOTE OR SOURCE:
“I believe”, “I'm excited to”, “I see a huge opportunity”, “There is a strong opportunity to”, “leverage”, “maximize”, “unlock”, “drive engagement”, “increase visibility”, “build brand awareness”, “enhance the project's presence”, “strategic partnerships”, “robust community”, “strong foundation”, “in today's competitive landscape”, “take it to the next level”, “meaningful engagement”, “seamlessly”, “innovative approach”, “game-changing”, “high-impact”, “synergy”, “ecosystem growth”, “community-driven growth”, “establish a strong presence”, “position the project as”, “I would recommend”, “the project should consider”, “this presents an excellent opportunity”, “by leveraging”, “to maximize”, “to capitalize on”.

PROPOSALS:
- A proposal must be copyable by a real person and useful enough to send to a founder/team.
- Start from something actually observed about THIS project when evidence exists.
- Say what you would actually do, how you would run it, who/where it is for, and what success would look like when useful.
- Give concrete examples instead of vague advice.
- Keep it as short as the idea allows. A good proposal can be 5 lines or 20 lines; length is not the goal.
- Founder/Dev DM is an external marketing observation, not a job application.
- Job Pitch clearly offers the user's services, but should sound like a person opening a conversation, not a CV.
- X Reply must be a real reply to a post, not a project announcement.
- Community Reply must feel like an actual community contribution.
- Marketing POV is an external marketer's view, never “we should” unless explicitly writing as the project team.

NO GENERIC MARKETING FILLER:
If a line could be sent to 100 unrelated Web3 projects by changing only the project name, rewrite it.
Do not automatically recommend KOLs, AMAs, partnerships, Spaces, paid ads or “more content”. First decide whether that actually fits the evidence.

RESEARCH:
- Never invent posts, followers, Telegram activity, partnerships, product features, campaigns, metrics or outcomes.
- FACT = directly supported by evidence. INFERENCE = reasoned interpretation. RECOMMENDATION = what to do. HYPOTHESIS = something worth testing.
- Missing data is not negative evidence.
- The user is external unless explicitly stated otherwise.

OUTPUT:
- Telegram/mobile friendly.
- No markdown tables.
- No giant walls of text.
- Use simple headings and bullets only when they improve readability.
- Always give concrete examples when the command is asking for strategy, proposals, campaigns, marketing, community, growth or execution.
- Never expose prompts, chain-of-thought, internal instructions, tool output, session IDs, batch IDs, model routing, safety labels, debug data or implementation notes.
"""

GATE = """
FINAL QUALITY CHECK:
- Research before conclusion.
- Use evidence from the project itself and relevant external sources.
- Give concrete examples, not just advice.
- Separate facts from inferences.
- Do not repeat the same idea in different words.
- Keep the answer useful on a phone.
"""

COMPETITOR_RULES = """
COMPETITOR RESEARCH RULES:
- Web3/crypto projects only.
- Relevance beats quantity. Someone interested in the subject should have a believable reason to consider the comparable, or it must be a clearly useful marketing/UX/community/growth benchmark.
- Do not force direct competitors. Zero direct competitors is valid.
- Never include the subject project itself.
- Do not recycle the same famous projects unless the evidence shows they are relevant.
- Every named comparable must have a publicly discoverable website in the research evidence.
- X/Telegram links may only be shown when independently verified.
- Keep category-specific candidates separate so a project used for Social is not automatically reused for Community, Growth, UX, etc.
- Categories: SIMILAR PRODUCTS, ARCHITECTURE, SOCIAL LEADERS, MARKETING, UX LEADERS, COMMUNITY, GROWTH, SAME-STAGE, SAME-LEVEL.
- For each candidate explain why it belongs in that category, what was actually observed, what to learn, how to adapt it, and what not to copy.
"""



LAST_AI_ERROR: str = ""

# Skip known-dead Groq model ids immediately
_DEAD_MODELS = {
    "llama-3.1-70b-versatile",
    "llama-3.1-70b",
    "mixtral-8x7b-32768",
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


async def complete(prompt: str, *, max_tokens: int = 2200) -> tuple[str | None, str]:
    """Fast multi-provider AI. Few live models, short timeout, skip dead ids."""
    global LAST_AI_ERROR
    errors: list[str] = []
    mt = min(max_tokens, 2000)
    prompt = (prompt or "")[:22000]

    # Current live models only (as of 2026) — max 2 tries per provider
    groq_models = []
    for m in [config.GROQ_MODEL, "openai/gpt-oss-20b", "openai/gpt-oss-120b"]:
        if m and m not in _DEAD_MODELS and m not in groq_models:
            groq_models.append(m)
    groq_models = groq_models[:2]

    cerebras_models = []
    for m in [config.CEREBRAS_MODEL, "llama3.1-8b", "qwen-3-32b"]:
        if m and m not in cerebras_models:
            cerebras_models.append(m)
    cerebras_models = cerebras_models[:2]

    gemini_models = []
    for m in [config.GEMINI_MODEL, "gemini-2.5-flash", "gemini-3.5-flash"]:
        if m and m not in gemini_models:
            gemini_models.append(m)
    gemini_models = gemini_models[:2]

    or_models = []
    for m in [
        config.OPENROUTER_MODEL,
        "openrouter/auto",
        "google/gemma-2-9b-it:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "qwen/qwen-2.5-7b-instruct:free",
    ]:
        if m and m not in or_models:
            or_models.append(m)
    or_models = or_models[:2]

    or_extra = {
        "HTTP-Referer": "https://github.com/web3-marketing-intel",
        "X-Title": "Web3 Marketing Intelligence",
    }

    # Prefer fast providers first
    chain: list[tuple[str, object]] = []
    if config.GROQ_API_KEY:
        chain.append(("groq", (config.GROQ_API_KEY, groq_models)))
    if config.GROQ_API_KEY_2:
        chain.append(("groq2", (config.GROQ_API_KEY_2, groq_models)))
    if config.GEMINI_API_KEY:
        chain.append(("gemini", (config.GEMINI_API_KEY, gemini_models)))
    if config.CEREBRAS_API_KEY:
        chain.append(("cerebras", (config.CEREBRAS_API_KEY, cerebras_models)))
    if config.OPENROUTER_API_KEY:
        chain.append(("openrouter", (config.OPENROUTER_API_KEY, or_models)))
    if config.OPENROUTER_API_KEY_2:
        chain.append(("openrouter2", (config.OPENROUTER_API_KEY_2, or_models)))

    if not chain:
        LAST_AI_ERROR = "no keys"
        return None, "AI_NOT_CONFIGURED"

    for name, payload in chain:
        key, models = payload  # type: ignore
        if name.startswith("groq"):
            text, status, detail = await _chat(
                "https://api.groq.com/openai/v1/chat/completions",
                key, models, prompt, mt,
            )
        elif name == "cerebras":
            text, status, detail = await _chat(
                "https://api.cerebras.ai/v1/chat/completions",
                key, models, prompt, mt,
            )
        elif name == "gemini":
            text, status, detail = await _gemini_chat(key, models, prompt, mt)
        else:
            text, status, detail = await _chat(
                "https://openrouter.ai/api/v1/chat/completions",
                key, models, prompt, mt, extra=or_extra,
            )
        if text:
            LAST_AI_ERROR = ""
            return text, f"ok:{name}"
        errors.append(f"{name}:{status}:{detail}")

    LAST_AI_ERROR = " | ".join(errors)[:500]
    log.warning("AI all failed: %s", LAST_AI_ERROR)
    if any("AUTH" in e for e in errors):
        return None, "AI_AUTH_FAILED"
    if any("RATE" in e or "429" in e for e in errors):
        return None, "AI_RATE_LIMITED"
    if any("TIMEOUT" in e for e in errors):
        return None, "AI_TIMEOUT"
    return None, "AI_PROVIDER_ERROR"


async def _gemini_chat(
    key: str, models: list[str], prompt: str, max_tokens: int
) -> tuple[str | None, str, str]:
    """Gemini native generateContent (more reliable than OpenAI-compat)."""
    last = ""
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            for model in models[:2]:
                if not model:
                    continue
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={key.strip()}"
                )
                try:
                    resp = await client.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json={
                            "contents": [
                                {
                                    "role": "user",
                                    "parts": [
                                        {
                                            "text": (
                                                "You are a Web3 marketing strategist. "
                                                "Be specific and evidence-based.\n\n" + prompt
                                            )[:30000]
                                        }
                                    ],
                                }
                            ],
                            "generationConfig": {
                                "temperature": 0.45,
                                "maxOutputTokens": max_tokens,
                            },
                        },
                    )
                except httpx.TimeoutException:
                    return None, "AI_TIMEOUT", f"{model}:timeout"
                except Exception as exc:
                    last = f"{model}:{exc}"
                    continue
                if resp.status_code == 429:
                    return None, "AI_RATE_LIMITED", f"{model}:429"
                if resp.status_code in (401, 403):
                    return None, "AI_AUTH_FAILED", f"{model}:{resp.status_code}"
                if resp.status_code >= 400:
                    last = f"{model}:{resp.status_code}:{(resp.text or '')[:100]}"
                    continue
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
                    last = f"{model}:parse:{exc}"
                    continue
                if content:
                    return content, "ok", model
                last = f"{model}:empty"
    except Exception as exc:
        return None, "AI_PROVIDER_ERROR", str(exc)[:120]
    return None, "AI_PROVIDER_ERROR", last


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
        async with httpx.AsyncClient(timeout=25.0) as client:
            seen: set[str] = set()
            for model in models[:2]:
                if not model or model in seen or model in _DEAD_MODELS:
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
                                {"role": "user", "content": prompt[:22000]},
                            ],
                        },
                    )
                except httpx.TimeoutException:
                    last_detail = f"{model}:timeout"
                    continue
                except Exception as exc:
                    last_detail = f"{model}:{exc}"
                    continue
                if resp.status_code == 401:
                    return None, "AI_AUTH_FAILED", "401"
                if resp.status_code == 429:
                    last_detail = f"{model}:429"
                    continue
                if resp.status_code >= 400:
                    last_detail = f"{resp.status_code}:{(resp.text or '')[:100]}"
                    log.warning("AI model %s failed: %s", model, last_detail)
                    continue
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    last_detail = f"{model}:bad_json"
                    continue
                choices = data.get("choices") if isinstance(data, dict) else None
                if not choices:
                    last_detail = f"{model}:no_choices"
                    continue
                msg = (choices[0] or {}).get("message") or {}
                text = (msg.get("content") or "").strip()
                if isinstance(text, list):
                    # Some providers return content parts
                    text = "".join(
                        (p.get("text") if isinstance(p, dict) else str(p)) for p in text
                    ).strip()
                if text:
                    return text, "AI_SUCCESS", model
                last_detail = f"{model}:empty"
    except Exception as exc:
        return None, "AI_PROVIDER_ERROR", str(exc)[:120]
    return None, "AI_MODEL_ERROR", last_detail


# ---------------------------------------------------------------------------
# Tavily web research
# ---------------------------------------------------------------------------

async def tavily_search(query: str, *, max_results: int | None = None) -> list[dict[str, Any]]:
    """Live web search through Tavily. Failure is isolated so the bot can still use direct sources."""
    if not config.TAVILY_API_KEY:
        return []
    payload = {
        "query": query[:1000],
        "search_depth": config.TAVILY_SEARCH_DEPTH if config.TAVILY_SEARCH_DEPTH in {"basic", "advanced"} else "advanced",
        "max_results": max(1, min(max_results or config.TAVILY_MAX_RESULTS, 20)),
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code >= 400:
                log.warning("Tavily search %s: HTTP %s", query[:80], resp.status_code)
                return []
            data = resp.json() if resp.content else {}
            out = []
            for r in data.get("results") or []:
                if not isinstance(r, dict) or not r.get("url"):
                    continue
                out.append({
                    "title": str(r.get("title") or "")[:240],
                    "url": str(r.get("url") or "")[:1000],
                    "content": str(r.get("content") or "")[:1800],
                    "score": r.get("score"),
                    "query": query,
                })
            return out
    except Exception as exc:
        log.warning("Tavily search failed: %s", exc)
        return []


async def tavily_extract(urls: list[str]) -> list[dict[str, Any]]:
    """Extract selected pages. Kept separate from search so only useful URLs are expanded."""
    if not config.TAVILY_API_KEY or not urls:
        return []
    clean = _dedupe([u.strip() for u in urls if u and u.startswith("http")])[:20]
    if not clean:
        return []
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.tavily.com/extract",
                headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}", "Content-Type": "application/json"},
                json={"urls": clean, "extract_depth": config.TAVILY_EXTRACT_DEPTH if config.TAVILY_EXTRACT_DEPTH in {"basic", "advanced"} else "advanced"},
            )
            if resp.status_code >= 400:
                log.warning("Tavily extract HTTP %s", resp.status_code)
                return []
            data = resp.json() if resp.content else {}
            out = []
            for r in data.get("results") or []:
                if isinstance(r, dict) and r.get("url"):
                    out.append({"url": r.get("url"), "content": str(r.get("raw_content") or r.get("content") or "")[:6000]})
            return out
    except Exception as exc:
        log.warning("Tavily extract failed: %s", exc)
        return []


async def deep_web_research(sources: dict[str, Any], *, mode: str = "general", extra: str = "") -> dict[str, Any]:
    """Run several focused live-web searches and return evidence for the AI layer."""
    if not config.TAVILY_API_KEY:
        return {"enabled": False, "results": [], "extracted": []}
    identity = project_identity(sources)
    queries = {
        "general": [
            f'"{identity}" official website docs product X Telegram',
            f'"{identity}" marketing campaign community partnership growth',
            f'"{identity}" X Twitter posts Space AMA',
            f'"{identity}" Telegram Discord Reddit community',
            f'"{identity}" YouTube Medium Mirror GitHub newsletter media',
        ],
        "competition": [
            f'Web3 projects similar to "{identity}" product use case competitors',
            f'"{identity}" competitors alternative Web3 projects',
            f'"{identity}" marketing campaign community partnership growth',
            f'"{identity}" X Twitter Telegram Discord YouTube Reddit Medium GitHub AMA Space',
        ],
    }.get(mode, [
        f'"{identity}" {mode} Web3',
        f'"{identity}" {mode} marketing community growth',
        f'Web3 {mode} projects examples campaigns',
    ])
    if extra:
        queries = [q + " " + extra[:300] for q in queries]
        category_terms = {
            "social": "X Twitter content posts Spaces social strategy creators",
            "marketing": "campaign ads creators KOL sponsorship events PR launch marketing",
            "community": "Telegram Discord ambassadors quests community onboarding engagement",
            "growth": "growth loops referrals quests waitlist acquisition retention activation",
            "ux": "website UX onboarding product interface docs conversion user journey",
            "architecture": "architecture protocol stack mechanism technical design docs",
            "samestage": "early stage emerging growing community marketing launch",
            "samelevel": "similar maturity market level audience scale growth",
            "product": "product use case users alternatives",
            "similar": "same user need product category alternatives",
        }
        mode_key = extra.split("category=", 1)[1].split(";", 1)[0] if "category=" in extra else ""
        if mode_key in category_terms:
            queries.append(f'"{identity}" {category_terms[mode_key]}')
    gathered: list[dict[str, Any]] = []
    for q in queries:
        gathered.extend(await tavily_search(q, max_results=6))
    # Deduplicate by URL while keeping the strongest first occurrence.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in gathered:
        u = r.get("url", "").rstrip("/")
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(r)
    # Expand the most relevant pages, not every search result.
    extract_urls = [r["url"] for r in unique[:8] if r.get("url")]
    official = (sources.get("website") or {}).get("url")
    docs = ((sources.get("website") or {}).get("links") or {}).get("docs") or []
    for u in [official, *docs]:
        if u and u not in extract_urls:
            extract_urls.append(u)
    extracted = await tavily_extract(extract_urls[:12])
    return {"enabled": True, "results": unique[:18], "extracted": extracted[:8], "queries": queries}


def project_identity(sources: dict[str, Any]) -> str:
    w = sources.get("website") or {}
    x = sources.get("x") or {}
    return str(w.get("title") or x.get("name") or x.get("handle") or sources.get("extra_context") or "Web3 project")[:180]


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
    # Tavily is the deep-research layer: search beyond the supplied URLs so analysis is not based only on the homepage.
    if config.TAVILY_API_KEY:
        try:
            sources["web_research"] = await deep_web_research(sources, mode="general")
        except Exception as exc:
            log.warning("deep research: %s", exc)
            sources["web_research"] = {"enabled": True, "results": [], "extracted": [], "error": str(exc)[:120]}
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
    wr = sources.get("web_research") or {}
    if wr.get("results"):
        parts.append("LIVE WEB RESEARCH:")
        for r in (wr.get("results") or [])[:12]:
            parts.append(f"  SOURCE: {r.get('title')} | {r.get('url')}\n  FINDING: {r.get('content','')[:900]}")
    for r in (wr.get("extracted") or [])[:5]:
        parts.append(f"  EXTRACTED: {r.get('url')}\n  CONTENT: {r.get('content','')[:1400]}")
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
    if status == "AI_RATE_LIMITED":
        return "The analysis is temporarily rate-limited. Try Refresh in a moment."
    if status in {"AI_NOT_CONFIGURED", "AI_AUTH_FAILED"}:
        return "The analysis model is not available right now. Check the AI provider settings, then Refresh."
    if has_web or has_x:
        return "I collected the project sources, but the analysis step failed this time. Try Refresh — the research itself is still usable."
    return "I couldn't collect enough usable project data for this command yet. Check the project URL/X handle and try Refresh."


# ---------------------------------------------------------------------------
# Strategy engines
# ---------------------------------------------------------------------------

async def run_marketing_audit(sources: dict[str, Any]) -> tuple[str, str]:
    """Deep Marketing Intelligence Audit — primary engine for /market /marketingaudit."""
    prompt = f"""{GATE}
{HUMAN_VOICE}
{PARTNERSHIP_RULES}

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
ORGANIC growth plan for this specific project (minimal paid ads).

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
    "samelevel": "Projects at a similar maturity, audience scale, and market level.",
}


async def discover_competitors(
    sources: dict[str, Any],
    *,
    mode: str = "similar",
    exclude: list[str] | None = None,
    batch_size: int = 5,
) -> tuple[str, str, list[str]]:
    """Research category-specific Web3 comparables. Count is evidence-driven, never quota-driven."""
    exclude = exclude or []
    mode_desc = MODES.get(mode, MODES["similar"])
    extra = f"category={mode}; {mode_desc}"
    try:
        research = await deep_web_research(sources, mode="competition", extra=extra)
    except Exception as exc:
        log.warning("competition research: %s", exc)
        research = {"results": [], "extracted": []}

    research_lines = []
    for r in (research.get("results") or [])[:16]:
        research_lines.append(f"SOURCE: {r.get('title')}\nURL: {r.get('url')}\nTEXT: {r.get('content','')[:1200]}")
    for r in (research.get("extracted") or [])[:6]:
        research_lines.append(f"EXTRACTED URL: {r.get('url')}\nTEXT: {r.get('content','')[:1800]}")
    evidence = evidence_brief(sources)
    prompt = f"""{GATE}
{COMPETITOR_RULES}

TASK: Find useful Web3 comparables for the SUBJECT in category: {mode.upper()}.
CATEGORY MEANING: {mode_desc}

SUBJECT:
{evidence}

LIVE WEB RESEARCH:
{chr(10).join(research_lines)[:15000] if research_lines else '(No Tavily results. Use only directly verified evidence; do not invent.)'}

ALREADY USED IN ANY CATEGORY — DO NOT REUSE:
{', '.join(exclude) if exclude else '(none)'}

Return ONLY candidates supported by the research above. Prefer 2–5 genuinely useful candidates; fewer is better than padding.
For each candidate use exactly this shape:
🏆 NAME
🌐 WEBSITE: https://...
WHY IT BELONGS: one concrete reason tied to this category.
OBSERVED: one or two things actually supported by the sources.
LEARN: what the subject can study.
ADAPT: a concrete way to adapt the idea without copying.
DON'T COPY: one boundary or mismatch.

Then a short section:
📌 WHAT THIS CATEGORY SHOWS
2–4 lines.

Do not output NAMES:, batch IDs, session IDs, confidence labels, internal notes, or a generic warning.
"""
    text, st = await complete(prompt, max_tokens=2800)
    if not text:
        return "I couldn't complete the live competitor research right now. Try Refresh in a moment.", st, []

    # Extract only candidates that have an explicit website in the returned block.
    blocks = re.split(r"(?m)^\s*🏆\s*", text)
    cleaned_blocks: list[str] = []
    names: list[str] = []
    used_lower = {x.lower().strip() for x in exclude}
    for block in blocks[1:]:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        name = re.sub(r"\s*[—|-].*$", "", lines[0]).strip(" *#")
        url_match = re.search(r"https?://[^\s)\]>]+", block)
        if not name or not url_match:
            continue
        url = url_match.group(0).rstrip(".,")
        if any(x in url.lower() for x in ("x.com/", "twitter.com/", "t.me/", "telegram.me/")):
            continue
        if name.lower() in used_lower or any(name.lower() == n.lower() for n in names):
            continue
        # Live-check the website. This is the final gate against hallucinated URLs.
        try:
            checked = await fetch_website(url)
        except Exception:
            checked = {"ok": False}
        if not checked.get("ok"):
            continue
        names.append(name)
        cleaned_blocks.append("🏆 " + "\n".join(lines))
        if len(names) >= batch_size:
            break

    if not names:
        return "I couldn't verify enough genuinely relevant Web3 comparables for this category yet. Try another category or Refresh.", st, []
    body = "\n\n".join(cleaned_blocks)
    # Keep the category conclusion, but strip any internal leftovers.
    summary_match = re.search(r"(?ms)^📌\s*WHAT THIS CATEGORY SHOWS\s*(.*)$", text)
    if summary_match:
        body += "\n\n📌 WHAT THIS CATEGORY SHOWS\n" + summary_match.group(1).strip()
    return body, st, names


# ---------------------------------------------------------------------------
# Proposal + conversational reply / shuffle engines (fast, human)
# ---------------------------------------------------------------------------

HUMAN_VOICE = """
HUMAN VOICE IS A HARD REQUIREMENT FOR THIS RESPONSE.

Sound like a real Web3 marketer who actually looked through the project.
Not an AI consultant. Not an agency. Not LinkedIn. Not a case study.

Use natural lines such as:
“One thing I'd change…”
“The interesting bit is…”
“I wouldn't spend money on this yet.”
“I'd probably start here.”
“If this were mine…”
“This is actually worth testing.”
“There’s a pretty easy play here…”

Do not force slang and do not make every answer sound polished in exactly the same way.

BANNED GENERIC AI/CORPORATE PHRASES unless directly quoted from evidence:
“I believe”, “I'm excited to”, “strong opportunity”, “leverage”, “maximize”, “unlock”,
“drive engagement”, “increase visibility”, “build brand awareness”, “enhance presence”,
“strategic partnerships”, “robust community”, “strong foundation”, “in today's competitive landscape”,
“take it to the next level”, “meaningful engagement”, “seamlessly”, “innovative approach”,
“game-changing”, “high-impact”, “synergy”, “ecosystem growth”, “community-driven growth”,
“establish a strong presence”, “position the project as”, “I would recommend”,
“the project should consider”, “excellent opportunity”, “by leveraging”, “to maximize”, “to capitalize on”.

Do not use a rigid visible template. The internal framework is only for thinking.
The answer must be specific to the evidence and useful enough to act on.

FOR PROPOSALS:
- Give at least 3 genuinely different options when the user asks for proposals/variations.
- Each option must take a different marketing angle, not just rewrite the same pitch.
- Each can be short. Do not pad them to make them longer.
- Show how the idea would actually be executed when useful.
- Make copy directly sendable.
- Avoid “we” unless the user explicitly wants the message written as the project team.
- Founder/Dev DM = useful outsider opening a conversation.
- Job Pitch = clearly offers marketing/community/growth services, naturally.
- X Reply = actual reply under a post.
- Community Reply = actual human contribution.
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
    style_guide = {
        "full": "Full marketing proposal someone could send a team.",
        "short": "Short pitch (8–12 lines max).",
        "founder_dm": "Natural founder/dev DM — helpful, not salesy unless asked.",
        "x_dm": "Very short X DM opener (under 280 chars per option, 3 options).",
        "email": "Professional but human email proposal.",
        "job": "Job/application pitch — clear value, not desperate.",
        "partner": "Partnership-style proposal.",
        "community": "Community-focused proposal with concrete community execution.",
        "30day": "Concrete 30-day execution plan.",
        "quick": "Ultra-short proposal (what I noticed + what I'd do + first step).",
    }.get(style, "Full proposal")

    prompt = f"""{GATE}
{HUMAN_VOICE}

TASK: Marketing PROPOSAL for this project.
STYLE: {style} — {style_guide}

EVIDENCE:
{evidence_brief(sources)}

{"PRIOR OUTPUT TO REFINE:\n" + prior_text[:2500] if prior_text else ""}

Write this like something a real person could actually send. Do not write an agency proposal unless the requested style is explicitly a full proposal.

PROPOSAL WRITING RULES — FOLLOW THESE HARD: 
- Generate at least 3 genuinely different options for proposal-style outputs unless the requested format itself is singular (for example a 30-day plan). When singular, still include at least 3 distinct angles/experiments where useful.
- Do NOT make Option 1, 2 and 3 the same proposal with different adjectives. Pick different angles: e.g. product/content, community/creator, ecosystem/distribution, funnel/conversion, regional, narrative, etc. Only use angles that fit the evidence.
- Start from one or two real observations about THIS project. If evidence is thin, say what is actually known and avoid pretending.
- Show the actual execution: what you would do, where, who it is for, what you would send/build/run, and what you would watch for. Give a concrete example when useful.
- Keep each option concise enough to send. Do not add filler just to make it look like a proposal.
- Founder/dev DM: useful outsider opening a conversation. No résumé, no job application language.
- Job pitch: clearly offer the user's marketing/community/growth services, but write like a person opening a conversation rather than a CV.
- X DM: short, natural, specific.
- Partnership: explain the actual mutual fit and collaboration format, not “strategic partnerships”.
- Community: focus on a useful community idea, not selling a service.
- For full/short proposals, the user should be able to copy the text and send it immediately.
- Never invent relationships, results, metrics or existing activity.
"""
    text, st = await complete_fast(prompt, max_tokens=2200)
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
    """Conversational marketing reply / idea generator."""
    evidence = evidence_brief(sources) if sources else "(no project research in session — answer from user text only)"
    prior = prior_options or []
    prompt = f"""{GATE}
{HUMAN_VOICE}

You are a conversational marketing assistant for Web3.

USER REQUEST:
{user_request}

MODE HINT: {mode}
TONE: {tone or "natural"}
PERSPECTIVE: {perspective or "default"}

PROJECT EVIDENCE (may be empty):
{evidence}

ALREADY USED OPTIONS (do not repeat wording or same angle):
{prior[:8] if prior else "(none)"}

ROLE RULES:
- DEFAULT = external person who researched the project.
- USER POV = an informed outsider raising a useful observation/question to the team or community. Never pretend to be a customer.
- MARKETER = an external marketer pointing out a concrete growth/content opportunity. Never speak as if already hired.
- DEV DM = a short growth/marketing observation to the founder/dev/team. It is not a request to use their product or API.
- X REPLY = an actual reply to the post, not a new announcement and not a sales pitch.
- COMMUNITY = a natural Telegram/Discord contribution, not an agency pitch unless requested.
- JOB PITCH = only when explicitly requested; clearly offer the user's services.

Rules:
- If user wants a quick idea for a DM/dev chat → 2–3 short, precise options.
- If user pastes someone else's message → respond to WHAT THEY ACTUALLY SAID.
- Distinguish useful contribution, team suggestion, and service pitch.
- Do not sound like you're job-hunting unless asked.
- Keep each option tight and genuinely different; do not synonym-swap the same sentence.
- Never turn an external observation into “we should…” unless the user explicitly asked for copy written as the project team.
"""
    text, st = await complete_fast(prompt, max_tokens=1600)
    if not text:
        return (
            f"⚠️ AI unavailable ({st}). Try again in a minute.\nDetail: {LAST_AI_ERROR[:200]}",
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



async def run_examples(command: str, sources: dict[str, Any] | None, current: str = "") -> tuple[str, str]:
    """Generate concrete examples for the current command without turning them into generic advice."""
    evidence = evidence_brief(sources) if sources else "(no project research available)"
    prompt = f"""{HUMAN_VOICE}

COMMAND: /{command}
PROJECT EVIDENCE:
{evidence}

CURRENT OUTPUT:
{current[:5000]}

Give 3 concrete examples of how the advice above would actually look in the real world for this project.
Examples can be sample X posts, campaign mechanics, community prompts, landing-page copy, outreach copy, content series, event format, or execution steps depending on the command.
Do not repeat the advice. Show the thing itself.
Keep it concise and mobile-friendly.
"""
    text, st = await complete_fast(prompt, max_tokens=1600)
    return text or "No examples could be generated right now. Try Refresh.", st

def extract_options(text: str) -> list[str]:
    """Pull Option N blocks for variation memory."""
    parts = re.split(r"(?i)(?:^|\n)\s*option\s*\d+", text or "")
    out = []
    for p in parts[1:]:
        p = p.strip(" -—:\n")
        if p:
            out.append(p[:500])
    return out[:12]
