"""Configuration schema (minimal)."""

import os
from typing import Any
from pydantic import BaseModel, Field, ConfigDict, model_validator
from pydantic.alias_generators import to_camel


class Base(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentDefaults(Base):
    """Agent 預設設定。"""
    workspace: str = "~/.minibot/workspace"
    model: str = "minimax/MiniMax-M2.5"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    memory_window: int = 50


class AgentsConfig(Base):
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    api_key: str = ""
    api_base: str | None = None


class ProvidersConfig(Base):
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: dict[str, Any]) -> dict[str, Any]:
        """優先從環境變數讀取 API Key。"""
        if os.environ.get("MINIMAX_API_KEY"):
            data.setdefault("minimax", ProviderConfig())
            data["minimax"]["api_key"] = os.environ.get("MINIMAX_API_KEY")
            if os.environ.get("MINIMAX_API_BASE"):
                data["minimax"]["api_base"] = os.environ.get("MINIMAX_API_BASE")
        return data


class TelegramConfig(Base):
    """Telegram Bot 設定。"""
    bot_token: str = ""


class ChannelsConfig(Base):
    """頻道設定。"""
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: dict[str, Any]) -> dict[str, Any]:
        """優先從環境變數讀取 Telegram 配置。"""
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            data.setdefault("telegram", TelegramConfig())
            data["telegram"]["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN")
        return data


class Config(Base):
    """主配置類別。"""
    locale: str = "en"
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)

