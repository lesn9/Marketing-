import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class Settings:
    bot_token: str
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    x_bearer_token: Optional[str] = None
    etherscan_api_key: Optional[str] = None
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise ValueError("BOT_TOKEN is required")
        return cls(
            bot_token=token,
            groq_api_key=os.getenv("GROQ_API_KEY"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            x_bearer_token=os.getenv("X_BEARER_TOKEN"),
            etherscan_api_key=os.getenv("ETHERSCAN_API_KEY"),
            solana_rpc_url=os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
        )

settings = Settings.from_env()
