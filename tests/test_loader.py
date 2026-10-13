"""Tests for config.loader module."""
import json

import pytest

from minibot.config.loader import ConfigError, load_config
from minibot.config.schema import Config


class TestLoadConfigMissingFile:
    def test_returns_defaults_when_file_absent(self, tmp_path):
        config = load_config(tmp_path / "does-not-exist.json")
        assert isinstance(config, Config)
        assert config.agents.defaults.model == "minimax/MiniMax-M2.5"


class TestLoadConfigValidFile:
    def test_reads_values_from_file(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({
                "locale": "zh_TW",
                "agents": {"defaults": {"model": "custom/model", "max_tokens": 1234}},
            }),
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.locale == "zh_TW"
        assert config.agents.defaults.model == "custom/model"
        assert config.agents.defaults.max_tokens == 1234


class TestLoadConfigFailsLoudly:
    """設定檔存在但無法載入時必須拋錯，不可靜默退回預設值。

    這是回歸測試：過去的實作會 print 一行訊息後 return Config()，
    導致使用者自訂的 model / workspace / max_tokens 無聲消失。
    """

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_invalid_schema_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"agents": {"defaults": {"max_tokens": "not-an-int"}}}),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_config(path)

    def test_non_object_root_raises(self, tmp_path):
        """JSON 根為 null 時，過去會在 _decrypt_sensitive_fields 爆 AttributeError。"""
        path = tmp_path / "config.json"
        path.write_text("null", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_error_message_includes_path(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ broken", encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            load_config(path)
        assert str(path) in str(excinfo.value)
