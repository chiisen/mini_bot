"""Tests for config.schema module."""
import pytest
from minibot.config.schema import (
    AgentDefaults,
    AgentsConfig,
    ProviderConfig,
    ProvidersConfig,
    TelegramConfig,
    ChannelsConfig,
    Config,
)


class TestAgentDefaults:
    def test_default_values(self):
        defaults = AgentDefaults()
        assert defaults.workspace == "~/.minibot/workspace"
        assert defaults.model == "minimax/MiniMax-M2.5"
        assert defaults.max_tokens == 8192
        assert defaults.temperature == 0.7

    def test_custom_values(self):
        agent = AgentDefaults(model="gpt-4", temperature=0.5)
        assert agent.model == "gpt-4"
        assert agent.temperature == 0.5


class TestProvidersConfig:
    def test_default_providers(self):
        providers = ProvidersConfig()
        assert hasattr(providers, "minimax")
        assert hasattr(providers, "openai")
        assert providers.minimax.api_key == ""


class TestTelegramConfig:
    def test_default_token_empty(self):
        config = TelegramConfig()
        assert config.bot_token == ""


class TestConfig:
    def test_config_default_values(self):
        config = Config()
        assert config.agents is not None
        assert config.providers is not None
        assert config.channels is not None


class TestEnvOverrides:
    """環境變數覆寫行為 — 涵蓋 env validator 的回歸測試。

    此類測試必須明確設定環境變數；conftest 的 autouse fixture
    已先清除，因此不受開發機環境影響。
    """

    def test_minimax_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        assert Config().providers.minimax.api_key == "env-key"

    def test_minimax_api_base_from_env(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        monkeypatch.setenv("MINIMAX_API_BASE", "https://example.invalid/v1")
        assert Config().providers.minimax.api_base == "https://example.invalid/v1"

    def test_env_key_overrides_config_file_key(self, monkeypatch):
        """環境變數必須勝過 config 檔案值（alias/欄位名同時存在時以 alias 為準）。"""
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        config = Config.model_validate(
            {"providers": {"minimax": {"apiKey": "file-key"}}}
        )
        assert config.providers.minimax.api_key == "env-key"

    def test_config_file_key_used_when_env_absent(self):
        config = Config.model_validate(
            {"providers": {"minimax": {"apiKey": "file-key"}}}
        )
        assert config.providers.minimax.api_key == "file-key"

    def test_telegram_token_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        assert Config().channels.telegram.bot_token == "env-token"

    def test_env_token_overrides_config_file_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        config = Config.model_validate(
            {"channels": {"telegram": {"botToken": "file-token"}}}
        )
        assert config.channels.telegram.bot_token == "env-token"

    def test_defaults_when_env_absent(self):
        """無環境變數且無檔案值時，應落回空字串預設值。"""
        config = Config()
        assert config.providers.minimax.api_key == ""
        assert config.channels.telegram.bot_token == ""
