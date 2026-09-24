import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class AIProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class AIEngine:
    def __init__(self, groq_key: Optional[str], openrouter_key: Optional[str]):
        self.groq_key = groq_key
        self.openrouter_key = openrouter_key

    async def chat(self, system: str, user: str, model: str = "llama-3.3-70b-versatile") -> str:
        if not self.groq_key and not self.openrouter_key:
            raise AIProviderError("AI_NOT_CONFIGURED", "No AI provider keys configured")

        providers = []
        if self.groq_key:
            providers.append(
                (
                    "groq",
                    self.groq_key,
                    "https://api.groq.com/openai/v1/chat/completions",
                    "llama-3.3-70b-versatile",
                )
            )
        if self.openrouter_key:
            providers.append(
                (
                    "openrouter",
                    self.openrouter_key,
                    "https://openrouter.ai/api/v1/chat/completions",
                    "meta-llama/llama-3.3-70b-instruct",
                )
            )

        errors = []
        for name, key, url, default_model in providers:
            try:
                return await self._call(url, key, system, user, default_model)
            except AIProviderError as e:
                errors.append(f"{name}: {e.code}")
                logger.warning(f"AI provider {name} failed: {e}")
                continue

        raise AIProviderError("AI_PROVIDER_ERROR", f"All providers failed: {', '.join(errors)}")

    async def _call(self, url: str, key: str, system: str, user: str, model: str) -> str:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException:
                raise AIProviderError("AI_TIMEOUT", "Request timed out")
            except Exception as e:
                raise AIProviderError("AI_PROVIDER_ERROR", str(e)[:200])

            if resp.status_code == 401:
                raise AIProviderError("AI_AUTH_FAILED", "Invalid API key")
            if resp.status_code == 429:
                raise AIProviderError("AI_RATE_LIMITED", "Rate limit exceeded")
            if resp.status_code >= 500:
                raise AIProviderError("AI_PROVIDER_ERROR", f"Server error {resp.status_code}")
            if resp.status_code != 200:
                raise AIProviderError(
                    "AI_PROVIDER_ERROR",
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )

            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise AIProviderError("AI_MODEL_ERROR", "Unexpected response format")


MARKETING_SYSTEM = """You are an expert Web3 marketing and competition intelligence analyst.
Rules:
- Only use facts from the provided evidence. Never invent data.
- Clearly separate FACT, INFERENCE, and OPPORTUNITY.
- If a source is missing, say so and continue with available evidence.
- Be concise, mobile-friendly, and use the exact section structure requested.
- No numerical scores (no 8/10 style).
- Output clean text ready for Telegram (use the emoji section headers provided).
"""
