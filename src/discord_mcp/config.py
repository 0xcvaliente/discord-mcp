from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", description="MCP server host")
    port: int = Field(default=8000, description="MCP server port")
    log_level: str = Field(default="INFO", description="Logging level")


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DISCORD_",
        case_sensitive=False,
        extra="ignore",
    )

    max_shards: int = Field(default=1, description="Maximum number of shards")
    session_timeout: int = Field(default=300, description="Session timeout in seconds")
    reconnect_attempts: int = Field(default=5, description="Number of reconnection attempts")
    reconnect_delay: int = Field(
        default=1, description="Delay between reconnection attempts in seconds"
    )


class EventStreamSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EVENT_STREAM_",
        case_sensitive=False,
        extra="ignore",
    )

    buffer_size: int = Field(default=100, description="Event stream buffer size")
    timeout: int = Field(default=30, description="Event stream timeout in seconds")


class MarketBoardSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MARKET_BOARD_",
        case_sensitive=False,
        extra="ignore",
    )

    webhook_url: str = Field(default="", description="Discord webhook URL the board posts to")
    coins: str = Field(
        default="bitcoin,ethereum,solana", description="Comma-separated CoinGecko coin ids"
    )
    interval: int = Field(default=300, description="Seconds between board updates")
    state_file: str = Field(
        default=".market_board_state.json",
        description="File storing the board message id so restarts edit the same message",
    )
    coingecko_api_key: str = Field(default="", description="Optional CoinGecko Demo API key")


class Settings:
    def __init__(self):
        self.mcp = MCPSettings()
        self.discord = DiscordSettings()
        self.event_stream = EventStreamSettings()
        self.market_board = MarketBoardSettings()

    @property
    def project_root(self) -> Path:
        return Path(__file__).parent.parent.parent


settings = Settings()
