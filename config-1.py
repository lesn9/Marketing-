import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    bot_token: str
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    x_bearer_token: Optional[str] = None
    etherscan_api_key: Optional[str] = None
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"


def load_settings() -> Settings:
    """Load settings from environment. Fails clearly if BOT_TOKEN is missing."""
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("BOT_TOKEN is missing. Add it in Railway → Variables.")
        raise RuntimeError(
            "BOT_TOKEN is required. "
            "Go to Railway → your service → Variables and add BOT_TOKEN."
        )

    settings = Settings(
        bot_token=token.strip(),
        groq_api_key=(os.getenv("GROQ_API_KEY") or "").strip() or None,
        openrouter_api_key=(os.getenv("OPENROUTER_API_KEY") or "").strip() or None,
        x_bearer_token=(os.getenv("X_BEARER_TOKEN") or "").strip() or None,
        etherscan_api_key=(os.getenv("ETHERSCAN_API_KEY") or "").strip() or None,
        solana_rpc_url=(os.getenv("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com").strip(),
    )

    # Log presence (never log the actual keys)
    logger.info("Environment check:")
    logger.info(f"  BOT_TOKEN          : {'✅ set' if settings.bot_token else '❌ missing'}")
    logger.info(f"  GROQ_API_KEY       : {'✅ set' if settings.groq_api_key else '⚠️  not set'}")
    logger.info(f"  OPENROUTER_API_KEY : {'✅ set' if settings.openrouter_api_key else '⚠️  not set'}")
    logger.info(f"  X_BEARER_TOKEN     : {'✅ set' if settings.x_bearer_token else '⚠️  not set'}")

    return settings
