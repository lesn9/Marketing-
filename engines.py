"""AI router + marketing / competition strategy engines."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

import config
from sources import evidence_brief, fetch_website

log = logging.getLogger("mkt.engines")

SYSTEM = """You are an elite Web3 marketing strategist, growth analyst, and competitive intelligence advisor.

HARD RULES:
1. Always move: OBSERVATION → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY.
2. Reject generic advice that fits 100 random projects. Be specific to THIS evidence.
3. Never invent X posts, follower counts, Telegram activity, campaigns, or product features.
4. If a source was unavailable, note it briefly and continue.
5. No price promises. Distinguish FACT vs INFERENCE vs RECOMMENDATION.
6. Telegram-mobile format: short sections, bullets, emoji only on headings.
7. Competitors MUST be real crypto/Web3 projects only — never Web2 SaaS or generic brands.
8. Do not invent competitor social accounts. Say "Not found / not publicly verified" when unknown.
"""

GATE = """
QUALITY GATE: If you are only describing, dig deeper into diagnosis + recommendation + execution.
If advice could apply to any project, make it specific to this product/type/channels/evidence.
"""

COMPETITOR_RULES = """
COMPETITOR TYPE — CRYPTO/WEB3 ONLY.
Do NOT return Web2 companies, traditional businesses, non-crypto SaaS, hypothetical projects, or articles.

For each competitor provide a structured profile:
🏷️ Project — what it is
🔗 Website — URL or "Not found / not publicly verified"
🐦 X — @handle or "Not found / not publicly verified"
💬 Telegram — URL or "Not found / not publicly verified"
⛓️ Blockchain — chain/contract if known, else unknown
🎯 Why comparable
📣 Marketing / 🐦 X strategy / 💬 Community / 🌐 Website-UX / 💎 Value prop / 🎯 Positioning
🧠 What appears to work
💡 What the analyzed project can learn
🔧 How to adapt (not copy)

"Better" must be dimension-specific with reasoning.
Label confidence: 🟢 Strong match | 🟡 Relevant match | ⚠️ Possible match / limited evidence
If a candidate is only from model knowledge and not verified via a live page, label:
⚠️ INFERRED CANDIDATE (not fully live-verified)
Never present inferred candidates as fully researched.
"""


async def complete(prompt: str, *, max_tokens: int = 3000) -> tuple[str | None, str]:
    if config.GROQ_API_KEY:
        t, s = await _openai_compatible(
            "https://api.groq.com/openai/v1/chat/completions",
            config.GROQ_API_KEY,
            [config.GROQ_MODEL, "llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
            prompt, max_tokens,
        )
        if t:
            return t, s
    if config.OPENROUTER_API_KEY:
        t, s = await _openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions",
            config.OPENROUTER_API_KEY,
            [config.OPENROUTER_MODEL, "meta-llama/llama-3.3-70b-instruct:free", "openrouter/auto"],
            prompt, max_tokens,
            extra_headers={
                "HTTP-Referer": "https://github.com/web3-marketing-intel",
                "X-Title": "Web3 Marketing Intelligence",
            },
        )
        if t:
            return t, s
    if not config.GROQ_API_KEY and not config.OPENROUTER_API_KEY:
        return None, "AI_NOT_CONFIGURED"
    return None, "AI_PROVIDER_ERROR"


async def _openai_compatible(
    url: str, key: str, models: list[str], prompt: str, max_tokens: int,
    extra_headers: dict | None = None,
) -> tuple[str | None, str]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(timeout=75) as client:
            for model in models:
                if not model:
                    continue
                resp = await client.post(
                    url, headers=headers,
                    json={
                        "model": model,
                        "temperature": 0.5,
                        "max_tokens": max_tokens,
                        "messages": [
                            {"role": "system", "content": SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                if resp.status_code == 401:
                    return None, "AI_AUTH_FAILED"
                if resp.status_code == 429:
                    return None, "AI_RATE_LIMITED"
                if resp.status_code >= 400:
                    log.warning("AI %s: %s", resp.status_code, (resp.text or "")[:180])
                    continue
                text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                if text:
                    return text, "AI_SUCCESS"
    except httpx.TimeoutException:
        return None, "AI_TIMEOUT"
    except Exception as exc:
        log.warning("AI error: %s", exc)
        return None, "AI_PROVIDER_ERROR"
    return None, "AI_MODEL_ERROR"


async def run_market(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
STRATEGIC MARKETING AUDIT.

EVIDENCE:
{evidence_brief(sources)}

Sections:
🎯 POSITIONING (current / diagnosis / recommendation + example line)
🧠 NARRATIVE (current + what to push)
💎 VALUE PROPOSITION
👥 AUDIENCE (primary/secondary, objections)
✍️ MESSAGING & CONTENT (more/less/instead + 2 example posts)
🚀 ACQUISITION & COMMUNITY
📣 ONE CAMPAIGN (objective, audience, mechanism, X, TG, measure)
🕳️ GAPS (only evidenced)
📋 THIS WEEK — 3 concrete NOW actions
"""
    text, st = await complete(prompt, max_tokens=3200)
    return text or _fallback("market", sources, st), st


async def run_positioning(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
POSITIONING deep-dive.

EVIDENCE:
{evidence_brief(sources)}

Current positioning, weaknesses, recommended + 2 alternatives,
example homepage headline, X bio, pinned post, messaging pillars.
Cite evidence for each recommendation.
"""
    text, st = await complete(prompt, max_tokens=2200)
    return text or _fallback("positioning", sources, st), st


async def run_campaigns(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Propose 3 project-specific campaigns (not generic influencer spam).

EVIDENCE:
{evidence_brief(sources)}

Each: Concept, Objective, Audience, Message, Mechanism, X exec, TG exec, assets, CTA, measurement.
"""
    text, st = await complete(prompt, max_tokens=2600)
    return text or _fallback("campaigns", sources, st), st


async def run_marketgaps(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Marketing gaps only if evidenced.

EVIDENCE:
{evidence_brief(sources)}

Each gap: FACT → DIAGNOSIS → RECOMMENDATION → EXECUTION → WHY
Tag: QUICK WIN | HIGH IMPACT/LOW EFFORT | LONGER-TERM
"""
    text, st = await complete(prompt, max_tokens=2400)
    return text or _fallback("marketgaps", sources, st), st


async def run_opportunities(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
5–8 growth opportunities ranked:
HIGH IMPACT/LOW EFFORT · QUICK WIN · HIGH IMPACT/HIGH EFFORT · LONGER-TERM

EVIDENCE:
{evidence_brief(sources)}

Each: WHAT / WHY / HOW / WHO / WHERE / SUCCESS LOOKS LIKE
Ban empty phrases like "build community" without a mechanism.
"""
    text, st = await complete(prompt, max_tokens=2600)
    return text or _fallback("opportunities", sources, st), st


async def run_report(sources: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Full strategy report (mobile-scannable):

EVIDENCE:
{evidence_brief(sources)}

🎯 Executive Strategy
🔎 Current Situation
📣 Marketing Diagnosis
🎯 Positioning
🧠 Narrative
💎 Value Prop
👥 Audience
🐦 X Strategy
💬 Community
🌐 Website/Conversion
📣 One campaign
🏆 Competitive notes (brief, Web3 only)
🕳️ Gaps
💡 Top opportunities
✍️ Content pillars
📋 Action Plan NOW / NEXT / LATER
"""
    text, st = await complete(prompt, max_tokens=4000)
    return text or _fallback("report", sources, st), st


async def run_compare(sa: dict[str, Any], sb: dict[str, Any]) -> tuple[str, str]:
    prompt = f"""{GATE}
Compare two Web3 projects. No overall winner score.

A:
{evidence_brief(sa)}

B:
{evidence_brief(sb)}

Dimensions: Positioning, Website/UX, X/content, Community, Messaging, Acquisition, Differentiation.
End with WHAT A LEARNS FROM B and WHAT B LEARNS FROM A (concrete adaptations).
"""
    text, st = await complete(prompt, max_tokens=3000)
    return text or "Compare failed — check AI keys.", st


async def run_competitor_focus(sources: dict[str, Any], focus: str) -> tuple[str, str]:
    prompt = f"""{GATE}
{COMPETITOR_RULES}

Subject evidence:
{evidence_brief(sources)}

User competitor focus: {focus}

Deep-dive that comparable (Web3 only). What they do, marketing, what works, what subject can adapt, what not to copy, 3 tests.
"""
    text, st = await complete(prompt, max_tokens=2400)
    return text or _fallback("competitor", sources, st), st


# ---------- Competition discovery with batches / modes ----------

MODES = {
    "similar": "Most relevant comparable Web3 projects (product/problem/audience).",
    "product": "Similar products solving a similar problem.",
    "architecture": "Similar product/mechanism architecture.",
    "social": "Relevant Web3 projects with stronger X/social execution.",
    "marketing": "Relevant Web3 projects with stronger marketing execution.",
    "positioning": "Relevant Web3 projects with clearer positioning.",
    "ux": "Relevant Web3 projects with stronger website/product UX.",
    "community": "Relevant Web3 projects with stronger community/support.",
    "growth": "Relevant Web3 projects with notable growth/campaign strategies.",
    "product_leaders": "Relevant projects with stronger product experience/value delivery.",
}


async def discover_competitors(
    sources: dict[str, Any],
    *,
    mode: str = "similar",
    exclude: list[str] | None = None,
    batch_size: int = 5,
) -> tuple[str, str, list[str]]:
    """Returns (report_text, ai_status, list_of_competitor_names_for_session)."""
    exclude = exclude or []
    mode_desc = MODES.get(mode, MODES["similar"])
    prompt = f"""{GATE}
{COMPETITOR_RULES}

You are running COMPETITOR DISCOVERY for a Web3 project.

MODE: {mode} — {mode_desc}

SUBJECT EVIDENCE:
{evidence_brief(sources)}

ALREADY SHOWN (do not repeat names, URLs, or aliases):
{exclude if exclude else "(none)"}

Return exactly {batch_size} NEW real crypto/Web3 competitors (not Web2).

For EACH competitor use this template:

🏆 Comparable Web3 Project — [Name]
Confidence: 🟢/🟡/⚠️
Research status: INFERRED CANDIDATE or PARTIALLY RESEARCHED or VERIFIED
Why comparable: ...
🌐 Website: url or Not found / not publicly verified
🐦 X: @handle or Not found / not publicly verified
💬 Telegram: url or Not found / not publicly verified
⛓️ Blockchain: ...
What they appear stronger at (dimension-specific): ...
What subject can learn: ...
How to adapt: ...

After all competitors, add:
📋 NEXT ACTIONS for the subject (3 bullets)

Also at the very end, on one line only, output machine list:
NAMES: name1 | name2 | name3 | ...
"""
    text, st = await complete(prompt, max_tokens=3500)
    if not text:
        return _fallback("competition", sources, st), st, []

    # Try live-verify websites mentioned
    urls = re.findall(r"https?://[^\s\)\]]+", text)
    verified_notes = []
    seen_url = set()
    for url in urls[:6]:
        if url in seen_url or "t.me/" in url or "x.com/" in url:
            continue
        seen_url.add(url)
        w = await fetch_website(url)
        if w.get("ok"):
            verified_notes.append(f"✓ Live-checked {url} — title: {w.get('title') or 'ok'}")
        else:
            verified_notes.append(f"✗ Could not live-check {url}: {w.get('error')}")

    names = []
    m = re.search(r"NAMES:\s*(.+)$", text, re.I | re.M)
    if m:
        names = [n.strip() for n in m.group(1).split("|") if n.strip()]
        text = re.sub(r"\n?NAMES:\s*.+$", "", text, flags=re.I | re.M).strip()

    if verified_notes:
        text += "\n\n🔍 Live verification attempts:\n" + "\n".join(verified_notes)

    text += (
        "\n\n⚠️ Discovery uses AI shortlisting + best-effort website checks. "
        "Candidates labeled INFERRED were not fully live-verified. "
        "Use buttons below for more / different modes."
    )
    return text, st, names


async def enrich_x_analysis_via_ai(sources: dict[str, Any]) -> str:
    """When no X API: structured X marketing analysis from handle + cross-links only."""
    x = sources.get("x") or {}
    if not x.get("handle"):
        return ""
    if x.get("mode") == "api" and x.get("recent_tweets"):
        return ""  # already have live data in evidence
    prompt = f"""Analyze X marketing for @{x.get('handle')} for a Web3 project.

Rules:
- You may use general knowledge of public crypto projects carefully.
- You MUST label uncertainty.
- Do NOT invent specific recent tweet text, exact follower counts, or engagement numbers.
- Focus on positioning of the handle, likely content posture, and strategic recommendations.

Other evidence:
{evidence_brief(sources)}

Output short sections:
🐦 X POSITIONING (what the account appears to stand for)
DIAGNOSIS (content/marketing gaps likely)
RECOMMENDATIONS (specific)
2 example post angles (not fake metrics)
"""
    text, _ = await complete(prompt, max_tokens=1200)
    return text or ""


def _fallback(kind: str, sources: dict[str, Any], status: str) -> str:
    has_web = bool((sources.get("website") or {}).get("ok"))
    has_x = bool((sources.get("x") or {}).get("ok"))
    lines = [
        f"⚠️ AI unavailable ({status}). Evidence-only brief for {kind}:",
        f"Sources: web={'✓' if has_web else '—'} X={'✓' if has_x else '—'}",
    ]
    if has_web:
        w = sources["website"]
        lines.append(f"Site: {w.get('title')}")
        if not w.get("has_community_link"):
            lines.append(
                "🕳️ GAP: No obvious public TG/Discord on website → add community CTA in header/hero."
            )
    if has_x:
        lines.append(f"X: @{sources['x'].get('handle')} mode={sources['x'].get('mode')}")
    lines.append("Set GROQ_API_KEY or OPENROUTER_API_KEY and retry.")
    return "\n".join(lines)
