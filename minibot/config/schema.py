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
        api_key = os.environ.get("MINIMAX_API_KEY")
        if api_key and isinstance(data, dict):
            # 以 alias 鍵名寫入：pydantic 在 alias 與欄位名同時存在時以 alias 為準，
            # 若寫入 snake_case 會讓 config 檔案的值反過來蓋掉環境變數。
            minimax = data.get("minimax")
            minimax = dict(minimax) if isinstance(minimax, dict) else {}
            minimax["apiKey"] = api_key
            api_base = os.environ.get("MINIMAX_API_BASE")
            if api_base:
                minimax["apiBase"] = api_base
            data["minimax"] = minimax
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
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if bot_token and isinstance(data, dict):
            # 同 ProvidersConfig：使用 alias 鍵名以確保覆蓋檔案值。
            telegram = data.get("telegram")
            telegram = dict(telegram) if isinstance(telegram, dict) else {}
            telegram["botToken"] = bot_token
            data["telegram"] = telegram
        return data


class Config(Base):
    """主配置類別。"""
    locale: str = "en"
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)

