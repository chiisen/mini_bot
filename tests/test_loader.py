"""Tests for config.loader module."""
import json

import pytest

from minibot.config import loader
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


class TestEncryptionKeyHandling:
    """加密路徑是「預設環境永遠跑不到」的分支（需設定 MINIBOT_CONFIG_KEY）。

    #1 的既有觀察：已測模組幾乎都因「路徑從未被執行」而藏有缺陷，因此這裡
    優先覆蓋有／無金鑰的雙路徑，而非只驗證預設（無金鑰）行為。
    """

    @pytest.fixture(autouse=True)
    def reset_fernet_cache(self, monkeypatch):
        """`_fernet` 是模組級快取，必須逐測重置，否則金鑰會跨測試殘留。"""
        monkeypatch.setattr(loader, "_fernet", None)

    def test_get_fernet_returns_none_without_key(self):
        assert loader._get_fernet() is None

    def test_get_fernet_creates_from_env_key(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "my-secret-passphrase")
        assert loader._get_fernet() is not None

    def test_get_fernet_caches_instance(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "my-secret-passphrase")
        assert loader._get_fernet() is loader._get_fernet()

    def test_generate_encryption_key_is_usable(self, monkeypatch):
        key = loader.generate_encryption_key()
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", key)
        assert loader._get_fernet() is not None

    def test_empty_env_key_is_treated_as_absent(self, monkeypatch):
        """空字串必須等同未設定，否則會用可預測的金鑰加密。"""
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "")
        assert loader._get_fernet() is None


class TestValueEncryption:
    @pytest.fixture(autouse=True)
    def reset_fernet_cache(self, monkeypatch):
        monkeypatch.setattr(loader, "_fernet", None)

    @pytest.fixture
    def with_key(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "passphrase")

    def test_round_trip_with_key(self, with_key):
        cipher = loader._encrypt_value("sk-secret-123")
        assert cipher != "sk-secret-123"
        assert loader._decrypt_value(cipher) == "sk-secret-123"

    def test_passthrough_without_key(self):
        assert loader._encrypt_value("sk-secret") == "sk-secret"
        assert loader._decrypt_value("sk-secret") == "sk-secret"

    def test_empty_value_passthrough(self, with_key):
        assert loader._encrypt_value("") == ""
        assert loader._decrypt_value("") == ""

    def test_ciphertext_differs_each_time(self, with_key):
        """Fernet 含隨機 IV，同一明文不應產生相同密文。"""
        assert loader._encrypt_value("same") != loader._encrypt_value("same")

    def test_decrypt_returns_input_when_not_ciphertext(self, with_key):
        """金鑰變更或內容損壞時回傳原值，而非拋錯中斷啟動。"""
        assert loader._decrypt_value("not-a-valid-token") == "not-a-valid-token"


class TestSensitiveFieldEncryption:
    @pytest.fixture(autouse=True)
    def reset_fernet_cache(self, monkeypatch):
        monkeypatch.setattr(loader, "_fernet", None)

    @pytest.fixture
    def with_key(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "passphrase")

    def test_encrypts_provider_api_key(self, with_key):
        """回歸測試：provider 金鑰位於 providers.<name>.apiKey。

        路徑過去寫成最上層的 `<name>.apiKey`，因此這裡**從未真正加密**——
        `save_config` 一律把 API Key 以明文寫入磁碟。telegram token 因為路徑
        正確而正常，所以缺陷不易察覺。
        """
        data = {"providers": {"minimax": {"apiKey": "sk-abc"}}}
        out = loader._encrypt_sensitive_fields(data)
        assert out["providers"]["minimax"]["apiKey"] != "sk-abc"
        assert loader._decrypt_sensitive_fields(out)["providers"]["minimax"][
            "apiKey"
        ] == "sk-abc"

    def test_encrypts_telegram_bot_token(self, with_key):
        data = {"channels": {"telegram": {"botToken": "123:ABC"}}}
        out = loader._encrypt_sensitive_fields(data)
        assert out["channels"]["telegram"]["botToken"] != "123:ABC"
        assert loader._decrypt_sensitive_fields(out)["channels"]["telegram"][
            "botToken"
        ] == "123:ABC"

    def test_covers_all_declared_providers(self, with_key):
        data = {"providers": {name: {"apiKey": "sk-x"} for name in loader.PROVIDER_NAMES}}
        out = loader._encrypt_sensitive_fields(data)
        assert all(
            out["providers"][name]["apiKey"] != "sk-x" for name in loader.PROVIDER_NAMES
        )

    def test_skips_empty_and_absent_fields(self, with_key):
        data = {
            "providers": {"minimax": {"apiKey": ""}, "openrouter": {"apiBase": "url"}}
        }
        out = loader._encrypt_sensitive_fields(data)
        assert out["providers"]["minimax"]["apiKey"] == ""
        assert out["providers"]["openrouter"] == {"apiBase": "url"}

    def test_passthrough_without_key(self):
        data = {"providers": {"minimax": {"apiKey": "sk-abc"}}}
        assert loader._encrypt_sensitive_fields(data) == data

    def test_does_not_mutate_input(self, with_key):
        """淺拷貝會讓巢狀子 dict 與輸入共用，導致呼叫端被就地改動。"""
        data = {"providers": {"minimax": {"apiKey": "sk-abc"}}}
        loader._encrypt_sensitive_fields(data)
        assert data["providers"]["minimax"]["apiKey"] == "sk-abc"

    def test_multiple_secrets_transform_independently(self, with_key):
        data = {
            "providers": {
                "minimax": {"apiKey": "sk-mini"},
                "openrouter": {"apiKey": "sk-open"},
            },
            "channels": {"telegram": {"botToken": "123:TG"}},
        }
        out = loader._decrypt_sensitive_fields(loader._encrypt_sensitive_fields(data))
        assert out["providers"]["minimax"]["apiKey"] == "sk-mini"
        assert out["providers"]["openrouter"]["apiKey"] == "sk-open"
        assert out["channels"]["telegram"]["botToken"] == "123:TG"


class TestSaveAndLoadRoundTrip:
    """真正的使用者可見性質：密鑰加密落盤，載入後還原為明文。"""

    @pytest.fixture(autouse=True)
    def reset_fernet_cache(self, monkeypatch):
        monkeypatch.setattr(loader, "_fernet", None)

    def test_secret_not_stored_in_plaintext(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "passphrase")
        path = tmp_path / "nested" / "config.json"
        config = Config.model_validate({"providers": {"minimax": {"apiKey": "sk-super-secret"}}})

        loader.save_config(config, path)

        assert path.exists(), "save_config 應建立上層目錄"
        assert "sk-super-secret" not in path.read_text(encoding="utf-8")

    def test_load_restores_plaintext_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "passphrase")
        path = tmp_path / "config.json"
        config = Config.model_validate({"providers": {"minimax": {"apiKey": "sk-super-secret"}}})

        loader.save_config(config, path)

        assert loader.load_config(path).providers.minimax.api_key == "sk-super-secret"

    def test_round_trip_without_key_keeps_plaintext(self, tmp_path):
        path = tmp_path / "config.json"
        config = Config.model_validate({"providers": {"minimax": {"apiKey": "sk-plain"}}})

        loader.save_config(config, path)

        assert "sk-plain" in path.read_text(encoding="utf-8")
        assert loader.load_config(path).providers.minimax.api_key == "sk-plain"

    def test_wrong_key_leaves_ciphertext_not_crash(self, tmp_path, monkeypatch):
        """金鑰變更後載入不應拋錯——密文原樣留下（可用 config-show 察覺異常）。"""
        path = tmp_path / "config.json"
        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "key-A")
        loader.save_config(
            Config.model_validate({"providers": {"minimax": {"apiKey": "sk-x"}}}), path
        )

        monkeypatch.setenv("MINIBOT_CONFIG_KEY", "key-B")
        monkeypatch.setattr(loader, "_fernet", None)
        loaded = loader.load_config(path)
        assert loaded.providers.minimax.api_key not in ("", "sk-x")
