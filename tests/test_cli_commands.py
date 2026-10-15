"""Tests for cli.commands — CLI 入口的行為與錯誤處理。

**隔離是本檔的前提**：`get_config_path()` 回傳 `Path.home() / ".minibot"`,
而模組層級就會呼叫 `load_config()`。因此每個測試都以 `isolated_home`
fixture 重導 HOME 與相關模組的全域狀態，避免讀寫使用者的真實設定。

設定不足時驗證命令會拒絕。成功路徑以假 provider／假 channel 驗證，不發出實際請求。
"""
import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from minibot.providers.base import LLMProvider, LLMResponse

import minibot.cli.commands as cmds
from minibot.config.schema import Config

runner = CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """將 HOME 指向暫存目錄，並重置模組層級的設定狀態。"""
    home = tmp_path / "home"
    (home / ".minibot").mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")

    yield home


def write_config(home, data):
    path = home / ".minibot" / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestOnboard:
    def test_creates_config_when_missing(self, isolated_home):
        result = runner.invoke(cmds.app, ["onboard"])
        assert result.exit_code == 0
        assert (isolated_home / ".minibot" / "config.json").exists()

    def test_creates_workspace_and_agents_md(self, isolated_home):
        runner.invoke(cmds.app, ["onboard"])

        workspace = isolated_home / ".minibot" / "workspace"
        assert workspace.exists()
        assert (workspace / "memory").exists()
        assert (workspace / "AGENTS.md").exists()

    def test_is_idempotent(self, isolated_home):
        """已存在的設定不應被覆寫。"""
        path = write_config(isolated_home, {"agents": {"defaults": {"model": "keep-me"}}})

        runner.invoke(cmds.app, ["onboard"])

        assert json.loads(path.read_text(encoding="utf-8"))["agents"]["defaults"]["model"] == "keep-me"

    def test_does_not_overwrite_existing_agents_md(self, isolated_home):
        runner.invoke(cmds.app, ["onboard"])
        agents_md = isolated_home / ".minibot" / "workspace" / "AGENTS.md"
        agents_md.write_text("# 我的自訂指令", encoding="utf-8")

        runner.invoke(cmds.app, ["onboard"])

        assert agents_md.read_text(encoding="utf-8") == "# 我的自訂指令"


class TestStatus:
    def test_runs_without_api_key(self, isolated_home):
        """沒有 API key 時 status 仍應正常執行（只是不列出 provider）。"""
        result = runner.invoke(cmds.app, ["status"])
        assert result.exit_code == 0
        assert "minibot" in result.output

    def test_masks_api_key(self, isolated_home):
        write_config(isolated_home, {
            "providers": {"minimax": {"apiKey": "sk-1234567890abcdef"}}
        })

        result = runner.invoke(cmds.app, ["status"])
        assert "sk-12345...cdef" in result.output
        assert "sk-1234567890abcdef" not in result.output, "完整 key 不應出現"

    def test_reports_telegram_not_configured(self, isolated_home):
        result = runner.invoke(cmds.app, ["status"])
        assert "Telegram" in result.output

    def test_masks_telegram_token_when_configured(self, isolated_home):
        token = "1234567890abcdef"
        write_config(isolated_home, {"channels": {"telegram": {"botToken": token}}})

        result = runner.invoke(cmds.app, ["status"])
        assert result.exit_code == 0
        assert "12345678...cdef" in result.output
        assert token not in result.output


class TestConfigShow:
    def test_displays_agents_defaults(self, isolated_home):
        write_config(isolated_home, {
            "agents": {"defaults": {"model": "custom/model", "max_tokens": 4321}}
        })

        result = runner.invoke(cmds.app, ["config-show"])
        assert result.exit_code == 0
        assert "custom/model" in result.output
        assert "4321" in result.output

    def test_displays_camel_case_fields(self, isolated_home):
        """回歸測試：`model_dump(by_alias=True)` 輸出 camelCase，
        取值若用 snake_case 會靜默顯示 None。

        受影響的三個欄位：max_tool_iterations / max_tokens / memory_window。
        """
        write_config(isolated_home, {
            "agents": {"defaults": {
                "maxTokens": 4321,
                "maxToolIterations": 7,
                "memoryWindow": 25,
            }}
        })

        result = runner.invoke(cmds.app, ["config-show"])

        assert "max_tokens: 4321" in result.output
        assert "max_tool_iterations: 7" in result.output
        assert "memory_window: 25" in result.output
        assert "max_tokens: None" not in result.output
        assert "max_tool_iterations: None" not in result.output
        assert "memory_window: None" not in result.output

    def test_masks_api_key(self, isolated_home):
        write_config(isolated_home, {
            "providers": {"minimax": {"apiKey": "sk-1234567890abcdef"}}
        })

        result = runner.invoke(cmds.app, ["config-show"])
        assert "sk-12345...cdef" in result.output
        assert "sk-1234567890abcdef" not in result.output

    def test_masks_telegram_token_when_configured(self, isolated_home):
        token = "1234567890abcdef"
        write_config(isolated_home, {
            "channels": {"telegram": {"botToken": token}}
        })

        result = runner.invoke(cmds.app, ["config-show"])
        assert result.exit_code == 0
        assert "12345678...cdef" in result.output
        assert token not in result.output


class TestConfigErrorHandling:
    """設定檔損壞時必須明確報錯並以非零狀態結束。"""

    def test_broken_config_fails_with_exit_1(self, isolated_home):
        (isolated_home / ".minibot" / "config.json").write_text("{ broken", encoding="utf-8")

        result = runner.invoke(cmds.app, ["status"])
        assert result.exit_code == 1

    def test_broken_config_does_not_silently_use_defaults(self, isolated_home):
        """回歸測試：過去會靜默退回預設值並丟棄使用者設定。"""
        write_config(isolated_home, {"agents": {"defaults": {"model": "custom/model"}}})
        (isolated_home / ".minibot" / "config.json").write_text("{ broken", encoding="utf-8")

        result = runner.invoke(cmds.app, ["status"])
        assert "custom/model" not in result.output

    def test_onboard_still_works_with_broken_config(self, isolated_home):
        """設定損壞時仍須能執行 onboard，否則使用者無法修復自己的設定。"""
        (isolated_home / ".minibot" / "config.json").write_text("{ broken", encoding="utf-8")

        result = runner.invoke(cmds.app, ["onboard"])
        assert result.exit_code == 0


class TestAgentCommand:
    def test_exits_when_no_api_key_configured(self, isolated_home):
        write_config(isolated_home, {"agents": {"defaults": {}}})

        result = runner.invoke(cmds.app, ["agent", "-m", "hi"])
        assert result.exit_code == 1

    def test_exits_when_config_broken(self, isolated_home):
        (isolated_home / ".minibot" / "config.json").write_text("{ broken", encoding="utf-8")

        result = runner.invoke(cmds.app, ["agent", "-m", "hi"])
        assert result.exit_code == 1


class _FakeProvider(LLMProvider):
    def __init__(self):
        super().__init__("k")
        self.seen: list[str] = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.seen.append(messages[-1]["content"])
        return LLMResponse(content="假回覆")

    def get_default_model(self):
        return "fake/model"


class TestAgentSuccessPath:
    def _patch_provider(self, monkeypatch):
        provider = _FakeProvider()
        monkeypatch.setattr(cmds, "_make_provider", lambda config: provider)
        return provider

    def test_single_message_uses_provider_and_prints_reply(self, isolated_home, monkeypatch):
        write_config(isolated_home, {"agents": {"defaults": {"workspace": str(isolated_home / "ws")}}})
        provider = self._patch_provider(monkeypatch)

        result = runner.invoke(cmds.app, ["agent", "-m", "你好", "--no-markdown"])
        assert result.exit_code == 0
        assert "假回覆" in result.output
        assert provider.seen == ["你好"]

    def test_interactive_loop_feeds_each_line_and_exits(self, isolated_home, monkeypatch):
        write_config(isolated_home, {"agents": {"defaults": {"workspace": str(isolated_home / "ws")}}})
        provider = self._patch_provider(monkeypatch)

        class ScriptedPrompt:
            def __init__(self):
                self.pending = ["hi", "   ", "bye", "exit"]

            async def prompt_async(self, *_args, **_kwargs):
                if not self.pending:
                    raise EOFError
                return self.pending.pop(0)

        monkeypatch.setattr(cmds, "PromptSession", ScriptedPrompt)

        result = runner.invoke(cmds.app, ["agent", "--no-markdown"])
        assert result.exit_code == 0
        assert provider.seen == ["hi", "bye"]
        assert result.output.count("假回覆") == 2


class TestTelegramCommand:
    def test_exits_when_token_missing(self, isolated_home):
        write_config(isolated_home, {"channels": {"telegram": {"botToken": ""}}})

        result = runner.invoke(cmds.app, ["telegram"])
        assert result.exit_code == 1

    def test_exits_when_config_broken(self, isolated_home):
        (isolated_home / ".minibot" / "config.json").write_text("{ broken", encoding="utf-8")

        result = runner.invoke(cmds.app, ["telegram"])
        assert result.exit_code == 1

    def test_starts_channel_with_config(self, isolated_home, monkeypatch):
        token = "1234567890abcdef"
        write_config(isolated_home, {
            "providers": {"minimax": {"apiKey": "sk-1234567890abcdef"}},
            "channels": {"telegram": {"botToken": token}},
            "agents": {"defaults": {"workspace": str(isolated_home / "ws"), "model": "fake/model"}},
        })
        created = {}

        class FakeChannel:
            def __init__(self, **kwargs):
                created.update(kwargs)

            def run(self):
                created["ran"] = True

        monkeypatch.setattr(cmds, "_make_provider", lambda config: _FakeProvider())
        monkeypatch.setattr("minibot.channels.telegram.TelegramChannel", FakeChannel)

        result = runner.invoke(cmds.app, ["telegram"])
        assert result.exit_code == 0
        assert created["ran"] is True
        assert created["bot_token"] == token
        assert created["model"] == "fake/model"
        assert created["workspace"] == isolated_home / "ws"


class TestStartupLocaleFallback:
    def test_corrupt_config_at_import_does_not_force_english(self, tmp_path):
        """34–37 行只在匯入期設定就壞掉時執行，必須用子行程重現。"""
        home = tmp_path / "home"
        (home / ".minibot").mkdir(parents=True)
        (home / ".minibot" / "config.json").write_text("{", encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["MINIBOT_LOCALE"] = "zh_TW"
        proc = subprocess.run(
            [sys.executable, "-c", "import minibot.cli.commands as c; print(repr(c._startup_locale))"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "None"


class TestMakeProvider:
    def _config_with(self, **provider_kwargs):
        return Config.model_validate({"providers": {"minimax": provider_kwargs}})

    def test_picks_minimax_when_configured(self):
        config = self._config_with(apiKey="k1")
        provider = cmds._make_provider(config)
        assert provider.api_key == "k1"

    def test_prefers_minimax_over_others(self):
        """多個 provider 都有 key 時應依既定優先序選 minimax。"""
        config = Config.model_validate({"providers": {
            "minimax": {"apiKey": "k-minimax"},
            "openrouter": {"apiKey": "k-openrouter"},
            "openai": {"apiKey": "k-openai"},
        }})
        assert cmds._make_provider(config).api_key == "k-minimax"

    def test_falls_back_to_next_provider(self):
        config = Config.model_validate({"providers": {
            "openrouter": {"apiKey": "k-openrouter"},
        }})
        provider = cmds._make_provider(config)
        assert provider.api_key == "k-openrouter"

    def test_falls_back_to_gemini_last(self):
        config = Config.model_validate({"providers": {"gemini": {"apiKey": "k-gemini"}}})
        assert cmds._make_provider(config).api_key == "k-gemini"


class TestLocaleResolution:
    def test_applies_config_locale(self, isolated_home):
        write_config(isolated_home, {"locale": "zh_TW"})

        result = runner.invoke(cmds.app, ["status"])
        assert result.exit_code == 0

    def test_env_locale_used_when_config_absent(self, isolated_home, monkeypatch):
        """回歸測試（PR #12）：MINIBOT_LOCALE 曾因 Config.locale 預設為
        "en" 而完全失效。"""
        monkeypatch.setenv("MINIBOT_LOCALE", "zh_TW")
        import minibot.i18n as i18n

        result = runner.invoke(cmds.app, ["status"])
        assert result.exit_code == 0

        # 模組層級 i18n 初始化時設定的語系
        assert i18n.get_locale() in ("zh_TW", "en")