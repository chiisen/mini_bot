"""i18n 多語系模組。"""

import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ["en", "zh_TW", "zh_CN"]

_translations: dict[str, dict[str, str]] = {}
_current_locale: str = DEFAULT_LOCALE


def _get_locales_dir() -> Path:
    return Path(__file__).parent / "locales"


def _load_translations():
    global _translations
    for locale in SUPPORTED_LOCALES:
        file_path = _get_locales_dir() / f"{locale}.json"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                _translations[locale] = json.load(f)


def set_locale(locale: str) -> str:
    global _current_locale
    if locale in SUPPORTED_LOCALES:
        _current_locale = locale
        return locale
    return DEFAULT_LOCALE


def get_locale() -> str:
    return _current_locale


def detect_locale(config_locale: str | None = None) -> str:
    if config_locale and config_locale in SUPPORTED_LOCALES:
        return config_locale
    env_locale = os.environ.get("MINIBOT_LOCALE")
    if env_locale and env_locale in SUPPORTED_LOCALES:
        return env_locale
    return DEFAULT_LOCALE


def t(key: str, **kwargs) -> str:
    if not _translations:
        _load_translations()
    translations = _translations.get(_current_locale, _translations.get(DEFAULT_LOCALE, {}))
    text = translations.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    return text


def init(config_locale: str | None = None):
    _load_translations()
    set_locale(detect_locale(config_locale))
