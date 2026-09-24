"""Input parsing + website / X / Telegram source collection (no X bearer required)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

import config

log = logging.getLogger("mkt.sources")

PROJECT_TYPES = {
    "meme", "utility", "defi", "infrastructure", "gaming", "ai", "consumer",
    "social", "depin", "rwa", "trading", "prediction", "nft", "launchpad",
    "protocol", "ecosystem", "other",
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


def parse_user_input(args: list[str]) -> ParsedInput:
    raw = " ".join(args).strip()
    result = ParsedInput(raw=raw)
    if not raw:
        return result
    tokens = list(args)
    for i, tok in enumerate(tokens):
        low = tok.lower().strip(",")
        if low in PROJECT_TYPES:
            result.project_type = low
            ctx = " ".join(tokens[i + 1 :]).strip()
            if ctx:
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
            headers={"User-Agent": "Web3MarketingIntel/2.0"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                out["error"] = f"HTTP {resp.status_code}"
                return out
            soup = BeautifulSoup(resp.text[:200_000], "lxml")
            out["ok"] = True
            if soup.title and soup.title.string:
                out["title"] = soup.title.string.strip()[:200]
            md = soup.find("meta", attrs={"name": "description"}) or soup.find(
                "meta", attrs={"property": "og:description"}
            )
            if md and md.get("content"):
                out["meta_description"] = md["content"].strip()[:400]
            for h in soup.find_all(["h1", "h2"])[:12]:
                t = h.get_text(" ", strip=True)
                if t:
                    out["headlines"].append(t[:180])
                    if h.name == "h1":
                        out["h1"].append(t[:180])
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            out["text_sample"] = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:3500]
            for a in soup.find_all("a", href=True)[:200]:
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
                if any(k in (label + href).lower() for k in ("join", "launch", "app", "buy", "docs", "community")):
                    if label:
                        out["ctas"].append({"text": label, "href": full})
            for k in out["links"]:
                out["links"][k] = _dedupe(out["links"][k])[:8]
            out["has_community_link"] = bool(out["links"]["telegram"] or out["links"]["discord"])
            out["ctas"] = out["ctas"][:12]
    except Exception as exc:
        out["error"] = str(exc)[:120]
        log.warning("website %s: %s", url, exc)
    return out


async def fetch_x(handle_or_url: str) -> dict[str, Any]:
    """X without required bearer: optional API if key exists, else public page + AI-ready stub."""
    out: dict[str, Any] = {
        "source_type": "x", "handle": None, "ok": False, "error": None,
        "name": None, "bio": None, "followers": None, "following": None,
        "tweet_count": None, "created_at": None, "url": None,
        "recent_tweets": [], "mode": "none",
    }
    handle = extract_handle(handle_or_url)
    out["handle"] = handle
    if not handle:
        out["error"] = "Could not parse X handle"
        return out

    # 1) Optional bearer
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
                        params={"max_results": 8, "tweet.fields": "created_at,public_metrics,text", "exclude": "retweets,replies"},
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
            log.warning("X API failed: %s", exp := exc)

    # 2) Best-effort public HTML (often blocked; still try)
    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Web3MarketingIntel/2.0)"},
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
    except Exception as exp:
        log.warning("X public HTML failed @%s: %s", handle, exp)

    # 3) AI-ready stub — analysis will use handle + other sources; not live metrics
    out["ok"] = True
    out["mode"] = "handle_only"
    out["url"] = f"https://x.com/{handle}"
    out["error"] = None
    out["note"] = (
        "No X API key and public page not readable. "
        "AI will analyze using the handle and any cross-links from website/Telegram; "
        "it must NOT invent live follower counts or specific recent posts."
    )
    return out


async def fetch_telegram(bot, value: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_type": "telegram", "username": None, "ok": False, "error": None,
        "title": None, "description": None, "members": None, "type": None,
        "invite_url": None,
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
            sources["limitations"].append(
                "X: handle only (no API / public HTML) — AI must not invent live metrics or posts"
            )
        elif not x.get("ok"):
            sources["limitations"].append(f"X: {x.get('error')}")
    if parsed.telegrams:
        tg = await fetch_telegram(bot, parsed.telegrams[0])
        sources["telegram"] = tg
        if not tg.get("ok"):
            sources["limitations"].append(f"Telegram: {tg.get('error')}")
    if not any([
        sources.get("website", {}).get("ok"),
        sources.get("x", {}).get("ok"),
        sources.get("telegram", {}).get("ok"),
        sources.get("contracts"),
    ]):
        sources["limitations"].append("No usable sources from input")
    if not sources.get("project_type"):
        blob = (sources.get("website") or {}).get("text_sample") or ""
        blob += " " + str((sources.get("x") or {}).get("bio") or "")
        low = blob.lower()
        if any(k in low for k in ("meme", "pepe", "dog coin", "culture")):
            sources["project_type"] = "meme (inferred)"
        elif any(k in low for k in ("defi", "stake", "swap", "lend")):
            sources["project_type"] = "defi (inferred)"
        elif any(k in low for k in ("ai agent", " artificial", "llm")):
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
                f"  h1={w.get('h1')}\n  headlines={w.get('headlines')[:8]}\n  ctas={w.get('ctas')[:8]}\n"
                f"  links_x={w.get('links',{}).get('x')}\n  links_tg={w.get('links',{}).get('telegram')}\n"
                f"  links_docs={w.get('links',{}).get('docs')}\n  has_community={w.get('has_community_link')}\n"
                f"  text={w.get('text_sample','')[:2000]}"
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
