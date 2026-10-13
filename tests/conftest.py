import pytest
from pathlib import Path


@pytest.fixture
def project_root():
    return Path(__file__).parent.parent


@pytest.fixture
def minibot_package():
    return Path(__file__).parent.parent / "minibot"


@pytest.fixture(autouse=True)
def isolate_config_env(monkeypatch):
    """隔離 config 相關的環境變數，讓測試結果不取決於執行環境。

    Config 的 env validator 會在環境變數存在時覆寫設定值，因此若不隔離，
    `providers.minimax.api_key == ""` 這類斷言會隨開發機環境時好時壞。
    需要驗證環境變數行為的測試，可在測試內用 monkeypatch.setenv 覆寫。
    """
    for var in ("MINIMAX_API_KEY", "MINIMAX_API_BASE", "TELEGRAM_BOT_TOKEN", "MINIBOT_LOCALE"):
        monkeypatch.delenv(var, raising=False)
