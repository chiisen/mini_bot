"""Tests for providers.litellm_provider — 模型命名、參數組裝與錯誤處理。

`litellm.acompletion` 全程被 monkeypatch，**不發出任何網路請求**。

環境變數的寫入行為（#3）刻意**未寫成斷言**——它是待修的缺陷，
先固化成斷言會讓日後修正更困難。
"""
import asyncio

import pytest

from minibot.providers.litellm_provider import LiteLLMProvider


@pytest.fixture
def make_provider():
    """建立 provider 時不污染真實環境變數。"""
    import os

    created = []

    def factory(**kwargs):
        kwargs.setdefault("api_key", "test-key")
        kwargs.setdefault("model", "default-model")
        kwargs.setdefault("env_key", "MINIBOT_TEST_PROVIDER_KEY")
        provider = LiteLLMProvider(**kwargs)
        created.append(kwargs["env_key"])
        return provider

    yield factory

    for key in created:
        os.environ.pop(key, None)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, message, finish_reason="stop", usage=None):
        self.choices = [_FakeChoice(message, finish_reason)]
        self.usage = usage


class _FakeToolCallFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeToolCallFunction(name, arguments)


@pytest.fixture
def capture_completion(monkeypatch):
    """攔截 litellm.acompletion，記錄 kwargs 並回傳指定回應。"""
    import litellm

    state = {"calls": [], "response": _FakeResponse(_FakeMessage(content="回覆"))}

    async def fake_acompletion(**kwargs):
        state["calls"].append(kwargs)
        return state["response"]

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    return state


class TestPrefixedModel:
    @pytest.mark.parametrize("prefix,model,expected", [
        ("minimax", "M2.5", "minimax/M2.5"),
        ("minimax", "minimax/M2.5", "minimax/M2.5"),   # 不重複加 prefix
        ("", "gpt-4", "gpt-4"),
        ("openrouter", "anthropic/claude", "openrouter/anthropic/claude"),
    ])
    def test_prefix_applied_once(self, make_provider, prefix, model, expected):
        provider = make_provider(litellm_prefix=prefix)
        assert provider._prefixed_model(model) == expected

    def test_falls_back_to_instance_model(self, make_provider):
        """model=None 應使用實例的 model，而非空字串。"""
        provider = make_provider(litellm_prefix="minimax", model="fallback-model")
        assert provider._prefixed_model(None) == "minimax/fallback-model"

    def test_get_default_model(self, make_provider):
        assert make_provider(model="m").get_default_model() == "m"


class TestChatArguments:
    def test_passes_required_arguments(self, make_provider, capture_completion):
        provider = make_provider()
        asyncio.run(provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=123, temperature=0.3,
        ))

        kwargs = capture_completion["calls"][0]
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["max_tokens"] == 123
        assert kwargs["temperature"] == 0.3
        assert kwargs["api_key"] == "test-key"

    def test_omits_api_base_when_not_set(self, make_provider, capture_completion):
        asyncio.run(make_provider().chat(messages=[]))
        assert "api_base" not in capture_completion["calls"][0]

    def test_includes_api_base_when_set(self, make_provider, capture_completion):
        asyncio.run(make_provider(api_base="https://x.invalid").chat(messages=[]))
        assert capture_completion["calls"][0]["api_base"] == "https://x.invalid"

    def test_omits_tool_choice_when_no_tools(self, make_provider, capture_completion):
        asyncio.run(make_provider().chat(messages=[]))
        assert "tools" not in capture_completion["calls"][0]
        assert "tool_choice" not in capture_completion["calls"][0]

    def test_sets_tool_choice_auto_when_tools_present(self, make_provider, capture_completion):
        tools = [{"type": "function", "function": {"name": "x"}}]
        asyncio.run(make_provider().chat(messages=[], tools=tools))
        assert capture_completion["calls"][0]["tool_choice"] == "auto"

    def test_uses_prefixed_model_in_call(self, make_provider, capture_completion):
        asyncio.run(make_provider(litellm_prefix="minimax").chat(messages=[]))
        assert capture_completion["calls"][0]["model"] == "minimax/default-model"


class TestResponseNormalization:
    def test_maps_content(self, make_provider, capture_completion):
        capture_completion["response"] = _FakeResponse(_FakeMessage(content="你好"))
        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.content == "你好"
        assert result.has_tool_calls is False

    def test_maps_tool_calls_with_dict_arguments(self, make_provider, capture_completion):
        tc = _FakeToolCall("c1", "read_file", {"path": "a.txt"})
        capture_completion["response"] = _FakeResponse(
            _FakeMessage(content=None, tool_calls=[tc])
        )

        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.has_tool_calls is True
        assert result.tool_calls[0].id == "c1"
        assert result.tool_calls[0].name == "read_file"
        assert result.tool_calls[0].arguments == {"path": "a.txt"}

    def test_repairs_malformed_json_arguments(self, make_provider, capture_completion):
        """LLM 常回傳不合法 JSON 字串，provider 必須容錯。"""
        tc = _FakeToolCall("c1", "read_file", "{'path': 'a.txt'}")
        capture_completion["response"] = _FakeResponse(
            _FakeMessage(content=None, tool_calls=[tc])
        )

        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.tool_calls[0].arguments == {"path": "a.txt"}

    def test_maps_finish_reason_and_usage(self, make_provider, capture_completion):
        capture_completion["response"] = _FakeResponse(
            _FakeMessage(content="x"), finish_reason="length", usage={"total_tokens": 42}
        )
        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.finish_reason == "length"
        assert result.usage == {"total_tokens": 42}

    def test_handles_missing_usage(self, make_provider, capture_completion):
        capture_completion["response"] = _FakeResponse(_FakeMessage(content="x"), usage=None)
        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.usage == {}


class TestErrorHandling:
    def test_exception_does_not_propagate(self, make_provider, monkeypatch):
        """Provider 錯誤不應中斷對話，應回傳錯誤訊息字串。"""
        import litellm

        async def boom(**kwargs):
            raise RuntimeError("上游 503")

        monkeypatch.setattr(litellm, "acompletion", boom)
        result = asyncio.run(make_provider().chat(messages=[]))
        assert isinstance(result.content, str)
        assert result.content

    def test_error_result_has_no_tool_calls(self, make_provider, monkeypatch):
        import litellm

        async def boom(**kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(litellm, "acompletion", boom)
        result = asyncio.run(make_provider().chat(messages=[]))
        assert result.has_tool_calls is False


class TestGlobalSideEffect:
    """環境變數寫入的後果（#3）——已記錄但尚未修正。"""

    def test_second_instance_overwrites_env_key(self, make_provider):
        """第二個實例會覆寫環境變數，但實例本身仍保有各自的 api_key。"""
        import os

        first = make_provider(api_key="first-secret")
        make_provider(api_key="second-secret")

        assert os.environ["MINIBOT_TEST_PROVIDER_KEY"] == "second-secret"
        # 實例狀態未被污染——chat() 使用 self.api_key
        assert first.api_key == "first-secret"

    def test_api_base_env_key_is_hardcoded(self, make_provider):
        """`MINIMAX_API_BASE` 與 env_key 無關，為硬編碼。"""
        import os

        os.environ.pop("MINIMAX_API_BASE", None)
        make_provider(api_key="k", api_base="https://x.invalid")
        assert os.environ["MINIMAX_API_BASE"] == "https://x.invalid"
        os.environ.pop("MINIMAX_API_BASE", None)

    def test_key_is_inherited_by_child_process(self, make_provider):
        """實質風險：API key 會擴散到子行程環境。"""
        import subprocess

        make_provider(api_key="SECRET-KEY-VALUE")
        result = subprocess.run(
            ["python", "-c", "import os; print(os.environ.get('MINIBOT_TEST_PROVIDER_KEY', ''))"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "SECRET-KEY-VALUE"