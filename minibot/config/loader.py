"""Config loading with encryption support."""

import copy
import json
import os
import base64
import hashlib
from pathlib import Path
from typing import Any, Callable
from cryptography.fernet import Fernet
from pydantic import ValidationError
from minibot.config.schema import Config
from minibot.i18n import t as _t

_fernet: Fernet | None = None
_encryption_key_env = "MINIBOT_CONFIG_KEY"

PROVIDER_NAMES = ("minimax", "openrouter", "anthropic", "openai", "deepseek", "gemini")

# 敏感欄位在設定結構中的實際路徑。
#
# 注意 provider 金鑰位於 `providers.<name>.apiKey`，**不是最上層的 <name>.apiKey**。
# 這裡過去寫成後者，導致 provider 金鑰從未被加解密、以明文落盤；而 telegram
# token 因為路徑剛好寫對而正常運作。同一份邏輯原本寫了兩遍（加密、解密各一遍），
# 因此只錯在其中一遍——收斂成單一表格可避免再次漂移。
SENSITIVE_FIELDS: tuple[tuple[str, ...], ...] = tuple(
    ("providers", name, "apiKey") for name in PROVIDER_NAMES
) + (("channels", "telegram", "botToken"),)


class ConfigError(Exception):
    """設定檔存在但無法讀取、解析或驗證時拋出（檔案不存在不算錯誤）。"""


def _get_fernet() -> Fernet | None:
    """取得或建立 Fernet 加密實例。"""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get(_encryption_key_env)
    if not key:
        return None

    key_bytes = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    _fernet = Fernet(key_bytes)
    return _fernet


def _encrypt_value(value: str) -> str:
    """加密敏感值。"""
    fernet = _get_fernet()
    if fernet is None or not value:
        return value
    return fernet.encrypt(value.encode()).decode()


def _decrypt_value(value: str) -> str:
    """解密敏感值。"""
    fernet = _get_fernet()
    if fernet is None or not value:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        # 金鑰變更、內容損壞、或本來就是明文（舊版寫入的設定檔）時原樣保留，
        # 不讓載入失敗——否則使用者會因為換金鑰而完全無法啟動。
        return value


def _map_sensitive_fields(
    data: dict[str, Any], transform: Callable[[str], str]
) -> dict[str, Any]:
    """依 `SENSITIVE_FIELDS` 轉換敏感欄位。

    對輸入做深拷貝後才操作：`data` 通常是 `model_dump()` 的結果，但淺拷貝會讓
    巢狀的 provider／channels 子 dict 與輸入共用，導致呼叫端的物件被就地改動。
    """
    result = copy.deepcopy(data)

    for path in SENSITIVE_FIELDS:
        parent: Any = result
        for key in path[:-1]:
            if not isinstance(parent, dict):
                parent = None
                break
            parent = parent.get(key)
        if isinstance(parent, dict) and parent.get(path[-1]):
            parent[path[-1]] = transform(parent[path[-1]])

    return result


def _encrypt_sensitive_fields(data: dict[str, Any]) -> dict[str, Any]:
    """加密敏感欄位。"""
    return _map_sensitive_fields(data, _encrypt_value)


def _decrypt_sensitive_fields(data: dict[str, Any]) -> dict[str, Any]:
    """解密敏感欄位。"""
    return _map_sensitive_fields(data, _decrypt_value)


def get_config_path() -> Path:
    """取得設定檔路徑（~/.minibot/config.json）。"""
    return Path.home() / ".minibot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """載入設定檔。

    - 檔案不存在：回傳預設值
    - 檔案存在但無法讀取、解析或驗證：拋出 ConfigError

    過去這裡失敗時會靜默退回 Config()，導致使用者自訂的設定無聲消失，
    因此改為明確報錯。
    """
    path = config_path or get_config_path()
    if not path.exists():
        return Config()

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(_t("error.config_load_failed", path=path, error=e)) from e

    if not isinstance(data, dict):
        raise ConfigError(_t(
            "error.config_load_failed",
            path=path,
            error="config root must be a JSON object",
        ))

    data = _decrypt_sensitive_fields(data)

    try:
        return Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_t("error.config_load_failed", path=path, error=e)) from e


def save_config(config: Config, config_path: Path | None = None) -> None:
    """儲存設定至磁碟（敏感欄位會加密）。"""
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True)
    data = _encrypt_sensitive_fields(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def generate_encryption_key() -> str:
    """產生新的加密金鑰（供使用者設定環境變數用）。"""
    return Fernet.generate_key().decode()
