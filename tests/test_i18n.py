"""Tests for i18n locale resolution."""
import json
from pathlib import Path

import pytest

from minibot.config.schema import Config
from minibot.i18n import SUPPORTED_LOCALES, detect_locale, get_locale, init, set_locale


@pytest.fixture(autouse=True)
def restore_locale():
    """i18n 的語系是模組級全域狀態，測試後還原以免污染其他測試。"""
    original = get_locale()
    yield
    set_locale(original)


class TestLocalePriority:
    """README 記載的優先順序：config.json > 環境變數 > 預設。"""

    def test_env_used_when_config_unspecified(self, monkeypatch):
        """config.json 未指定 locale 時應採用環境變數。

        回歸測試：`Config.locale` 過去預設為 "en"，因其為合法語系會蓋掉
        環境變數，使 README 記載的 `MINIBOT_LOCALE` 完全失效。
        """
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_TW")
        assert detect_locale(Config().locale) == "zh_TW"

    def test_env_used_when_config_key_absent(self, monkeypatch):
        """設定檔存在但沒有 locale 欄位時，同樣應落回環境變數。"""
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_CN")
        config = Config.model_validate({"agents": {"defaults": {"model": "x"}}})
        assert detect_locale(config.locale) == "zh_CN"

    def test_config_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_TW")
        config = Config.model_validate({"locale": "en"})
        assert detect_locale(config.locale) == "en"

    def test_default_when_nothing_set(self):
        assert detect_locale(None) == "en"

    def test_unsupported_env_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_LOCALE", "fr")
        assert detect_locale(None) == "en"

    def test_unsupported_config_value_falls_back_to_default(self):
        assert detect_locale("fr") == "en"


class TestConfigLocaleDefault:
    def test_locale_defaults_to_unset(self):
        """未提供 locale 時應為 None，而非偽裝成 "en"。"""
        assert Config().locale is None

    def test_explicit_locale_is_kept(self):
        assert Config.model_validate({"locale": "zh_TW"}).locale == "zh_TW"


class TestInitUsesDetectedLocale:
    def test_init_applies_env_locale(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_TW")
        init(config_locale=Config().locale)
        assert get_locale() == "zh_TW"

    def test_init_applies_config_locale(self, monkeypatch):
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_TW")
        init(config_locale=Config.model_validate({"locale": "zh_CN"}).locale)
        assert get_locale() == "zh_CN"


class TestLocaleFileParity:
    """三個語系檔的鍵集合必須完全一致。

    加字串時只改一個語系檔不會有任何測試失敗，但 `t()` 找不到鍵時會**原樣回傳
    鍵名**（`t("error.empty_reply")` → `"error.empty_reply"`），使用者就會在對話
    裡看到這串識別字而不是錯誤訊息。這道檢查把「三語系同步」從口頭約定變成
    可執行的條件。
    """

    @staticmethod
    def _load(locale: str) -> dict:
        path = Path(__file__).resolve().parents[1] / "minibot" / "i18n" / "locales" / f"{locale}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_all_locales_define_the_same_keys(self):
        baseline_locale = SUPPORTED_LOCALES[0]
        baseline = self._load(baseline_locale)

        for locale in SUPPORTED_LOCALES[1:]:
            keys = self._load(locale)
            missing = sorted(set(baseline) - set(keys))
            extra = sorted(set(keys) - set(baseline))
            assert not missing, f"{locale} 缺少 {baseline_locale} 有的鍵：{missing}"
            assert not extra, f"{locale} 多了 {baseline_locale} 沒有的鍵：{extra}"

    def test_no_locale_has_blank_translation(self):
        for locale in SUPPORTED_LOCALES:
            blanks = sorted(k for k, v in self._load(locale).items() if not str(v).strip())
            assert not blanks, f"{locale} 有空翻譯：{blanks}"
