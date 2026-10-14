"""Tests for agent.loop — LLM ↔ Tool 迭代迴圈。

這裡固定的是「必須維持」的行為：
- 無 tool call 時直接回傳內容
- tool call 會被執行，結果以正確的 role / tool_call_id / name 追加回 messages
- `max_iterations` 是硬性上限
- 工具輸出注入 messages 前會被截斷

工具輸出會以 `<tool_output>` 邊界標記包住（#4），因此斷言內容時需先解開邊界；
`unwrap_tool_output()` 即為此用途。
"""
import asyncio
import json

import pytest

from minibot.agent.loop import AgentLoop
from minibot.bus.queue import MessageBus
from minibot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


def unwrap_tool_output(content: str) -> str:
    """取出 `<tool_output ...>` 邊界內的原始內容。"""
    assert content.startswith("<tool_output tool="), content[:40]
    assert content.endswith("</tool_output>"), content[-40:]
    return content.split(">\n", 1)[1][: -len("\n</tool_output>")]


class ScriptedProvider(LLMProvider):
    """依腳本依序回應，並記錄每次收到的 messages。"""

    def __init__(self, script):
        super().__init__("k")
        self.script = list(script)
        self.calls = []
        self.tools_arg = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls.append(list(messages))
        self.tools_arg.append(tools)
        if self.script:
            return self.script.pop(0)
        return LLMResponse(content="(script exhausted)")

    def get_default_model(self):
        return "test/model"


def make_loop(provider, workspace, **kwargs):
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace, **kwargs)


def tool_call(name, arguments=None, call_id="c1"):
    return LLMResponse(content=None, tool_calls=[ToolCallRequest(
        id=call_id, name=name, arguments=arguments or {})])


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    return tmp_path


class TestNoToolCalls:
    def test_returns_content_directly(self, workspace):
        provider = ScriptedProvider([LLMResponse(content="你好")])
        assert asyncio.run(make_loop(provider, workspace).process_direct("嗨")) == "你好"
        assert len(provider.calls) == 1

    def test_empty_content_becomes_empty_string(self, workspace):
        provider = ScriptedProvider([LLMResponse(content=None)])
        assert asyncio.run(make_loop(provider, workspace).process_direct("嗨")) == ""

    def test_persists_session_to_disk(self, workspace):
        provider = ScriptedProvider([LLMResponse(content="回覆")])
        asyncio.run(make_loop(provider, workspace).process_direct("問題"))

        session_file = workspace / "sessions" / "cli_direct.jsonl"
        assert session_file.exists()
        content = session_file.read_text(encoding="utf-8")
        assert "問題" in content
        assert "回覆" in content

    def test_passes_tool_definitions_to_provider(self, workspace):
        provider = ScriptedProvider([LLMResponse(content="ok")])
        asyncio.run(make_loop(provider, workspace).process_direct("x"))
        assert provider.tools_arg[0], "工具定義應傳給 LLM"


class TestToolCallFlow:
    def test_executes_tool_and_returns_final_content(self, workspace):
        provider = ScriptedProvider([
            tool_call("read_file", {"path": str(workspace / "a.txt")}),
            LLMResponse(content="檔案內容是 hi"),
        ])
        result = asyncio.run(make_loop(provider, workspace).process_direct("讀檔"))
        assert result == "檔案內容是 hi"
        assert len(provider.calls) == 2

    def test_tool_result_message_shape(self, workspace):
        """tool 訊息必須帶 tool_call_id 與 name，否則 LLM 無法配對呼叫與結果。"""
        provider = ScriptedProvider([
            tool_call("read_file", {"path": str(workspace / "a.txt")}, call_id="abc"),
            LLMResponse(content="done"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("讀檔"))

        tool_msgs = [m for m in provider.calls[1] if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "abc"
        assert tool_msgs[0]["name"] == "read_file"
        assert unwrap_tool_output(tool_msgs[0]["content"]) == "hi"

    def test_assistant_tool_calls_serialized_as_json_string(self, workspace):
        """LLM API 要求 tool_calls 的 function.arguments 是 JSON 字串。"""
        provider = ScriptedProvider([
            tool_call("read_file", {"path": str(workspace / "a.txt")}),
            LLMResponse(content="done"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("讀檔"))

        assistant = [
            m for m in provider.calls[1]
            if m["role"] == "assistant" and m.get("tool_calls")
        ][0]
        fn = assistant["tool_calls"][0]["function"]
        assert fn["name"] == "read_file"
        assert json.loads(fn["arguments"]) == {"path": str(workspace / "a.txt")}

    def test_unknown_tool_is_reported_to_llm(self, workspace):
        """不存在的工具不應拋例外，錯誤字串要回給 LLM 讓它自行判斷。"""
        provider = ScriptedProvider([
            tool_call("no_such_tool"),
            LLMResponse(content="我無法使用該工具"),
        ])
        result = asyncio.run(make_loop(provider, workspace).process_direct("x"))
        assert result == "我無法使用該工具"

    def test_tool_error_is_passed_through(self, workspace):
        provider = ScriptedProvider([
            tool_call("read_file", {"path": "/etc/passwd"}),
            LLMResponse(content="無法讀取"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("x"))

        tool_msg = [m for m in provider.calls[1] if m["role"] == "tool"][0]
        assert "Access denied" in tool_msg["content"]


class TestMultipleToolCalls:
    def test_executes_each_call_in_order(self, workspace):
        provider = ScriptedProvider([
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="c1", name="pwd", arguments={}),
                ToolCallRequest(id="c2", name="pwd", arguments={}),
            ]),
            LLMResponse(content="完成"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("x"))

        tool_msgs = [m for m in provider.calls[1] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]


class TestMaxIterations:
    def test_stops_at_max_iterations(self, workspace):
        """無限迴圈的 LLM 必須被上限攔截。"""

        class Looping(LLMProvider):
            def __init__(self):
                super().__init__("k")
                self.calls = 0

            async def chat(self, messages, tools=None, **kw):
                self.calls += 1
                return tool_call("pwd", call_id=f"c{self.calls}")

            def get_default_model(self):
                return "test/model"

        provider = Looping()
        result = asyncio.run(
            make_loop(provider, workspace, max_iterations=3).process_direct("loop")
        )
        assert result == "Reached max iterations."
        assert provider.calls == 3


class TestToolOutputTruncation:
    def test_truncates_long_tool_output(self, workspace):
        """過大的工具輸出不應整份塞進 messages。"""
        big = workspace / "big.txt"
        big.write_text("X" * 50000, encoding="utf-8")

        provider = ScriptedProvider([
            tool_call("read_file", {"path": str(big)}),
            LLMResponse(content="ok"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("讀大檔"))

        tool_msg = [m for m in provider.calls[1] if m["role"] == "tool"][0]
        assert len(unwrap_tool_output(tool_msg["content"])) == 10000

    def test_short_output_not_truncated(self, workspace):
        provider = ScriptedProvider([
            tool_call("read_file", {"path": str(workspace / "a.txt")}),
            LLMResponse(content="ok"),
        ])
        asyncio.run(make_loop(provider, workspace).process_direct("讀檔"))

        tool_msg = [m for m in provider.calls[1] if m["role"] == "tool"][0]
        assert unwrap_tool_output(tool_msg["content"]) == "hi"


class TestOnProgress:
    def test_callback_receives_tool_name(self, workspace):
        seen = []

        async def progress(msg):
            seen.append(msg)

        provider = ScriptedProvider([tool_call("pwd"), LLMResponse(content="done")])
        asyncio.run(
            make_loop(provider, workspace).process_direct("x", on_progress=progress)
        )
        assert seen == ["🔧 pwd"]

    def test_callback_not_required(self, workspace):
        provider = ScriptedProvider([tool_call("pwd"), LLMResponse(content="done")])
        assert asyncio.run(
            make_loop(provider, workspace).process_direct("x")
        ) == "done"


class TestBusRunLoop:
    """`run()` 是 Bus 消費模式（Gateway 用）。目前沒有任何 channel 使用它，
    但屬於公開 API，因此測試其行為以免日後接線時才發現問題。"""

    def _run_until_outbound(self, loop, inbound, timeout=2.0):
        """投遞一則訊息，等待對應的 outbound 回覆。"""

        async def scenario():
            task = asyncio.ensure_future(loop.run())
            await loop.bus.publish_inbound(inbound)
            try:
                return await asyncio.wait_for(loop.bus.consume_outbound(), timeout)
            finally:
                loop.stop()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return asyncio.run(scenario())

    def test_publishes_response_to_outbound(self, workspace):
        from minibot.bus.events import InboundMessage

        provider = ScriptedProvider([LLMResponse(content="回覆內容")])
        loop = make_loop(provider, workspace)

        outbound = self._run_until_outbound(
            loop,
            InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="問題"),
        )

        assert outbound.content == "回覆內容"
        assert outbound.channel == "test"
        assert outbound.chat_id == "c1"

    def test_uses_session_key_from_inbound(self, workspace):
        """每位使用者的對話必須依 inbound 的 channel:chat_id 分開。

        註：asyncio.Queue 會綁定建立時的 event loop，因此兩則訊息必須在
        同一個 asyncio.run() 內送出。
        """
        from minibot.bus.events import InboundMessage

        provider = ScriptedProvider([LLMResponse(content="A"), LLMResponse(content="B")])
        loop = make_loop(provider, workspace)

        async def scenario():
            task = asyncio.ensure_future(loop.run())
            await loop.bus.publish_inbound(
                InboundMessage(channel="t", sender_id="u1", chat_id="u1", content="q1")
            )
            first = await asyncio.wait_for(loop.bus.consume_outbound(), 2.0)
            await loop.bus.publish_inbound(
                InboundMessage(channel="t", sender_id="u2", chat_id="u2", content="q2")
            )
            second = await asyncio.wait_for(loop.bus.consume_outbound(), 2.0)
            loop.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return first, second

        first, second = asyncio.run(scenario())
        assert (first.content, second.content) == ("A", "B")
        assert (workspace / "sessions" / "t_u1.jsonl").exists()
        assert (workspace / "sessions" / "t_u2.jsonl").exists()

    def test_error_does_not_kill_loop(self, workspace):
        """單則訊息處理失敗不應讓整個消費迴圈停止。"""
        from minibot.bus.events import InboundMessage

        class Boom(LLMProvider):
            def __init__(self):
                super().__init__("k")
                self.calls = 0

            async def chat(self, messages, tools=None, **kw):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("provider down")
                return LLMResponse(content="恢復後回覆")

            def get_default_model(self):
                return "test/model"

        loop = make_loop(Boom(), workspace)

        async def scenario():
            task = asyncio.ensure_future(loop.run())
            await loop.bus.publish_inbound(
                InboundMessage(channel="t", sender_id="u", chat_id="u", content="q1")
            )
            await loop.bus.publish_inbound(
                InboundMessage(channel="t", sender_id="u", chat_id="u", content="q2")
            )
            try:
                return await asyncio.wait_for(loop.bus.consume_outbound(), 2.0)
            finally:
                loop.stop()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        outbound = asyncio.run(scenario())
        assert outbound.content == "恢復後回覆"

    def test_stop_sets_running_false(self, workspace):
        loop = make_loop(ScriptedProvider([]), workspace)
        loop._running = True
        loop.stop()
        assert loop._running is False


class TestSessionPersistence:
    def test_second_turn_includes_history(self, workspace):
        provider = ScriptedProvider([
            LLMResponse(content="第一輪"),
            LLMResponse(content="第二輪"),
        ])
        loop = make_loop(provider, workspace)

        asyncio.run(loop.process_direct("第一個問題"))
        asyncio.run(loop.process_direct("第二個問題"))

        second_turn = provider.calls[1]
        assert second_turn[0]["role"] == "system"
        contents = [m["content"] for m in second_turn if m["role"] == "user"]
        assert "第一個問題" in contents

    def test_memory_window_limits_history(self, workspace):
        provider = ScriptedProvider([
            LLMResponse(content="回覆1"),
            LLMResponse(content="回覆2"),
            LLMResponse(content="回覆3"),
        ])
        loop = make_loop(provider, workspace, memory_window=2)

        asyncio.run(loop.process_direct("問題1"))
        asyncio.run(loop.process_direct("問題2"))
        asyncio.run(loop.process_direct("問題3"))

        last = provider.calls[2]
        user_msgs = [m["content"] for m in last if m["role"] == "user"]
        assert "問題1" not in user_msgs