"""Tests for agent.tools.registry — 工具註冊與執行。

`execute()` 的例外路徑是重點：工具實作若有 bug，錯誤必須變成可讀字串回給
LLM，而不是讓整個 Agent 迴圈中斷。
"""
import asyncio

import pytest

from minibot.agent.tools.base import Tool
from minibot.agent.tools.registry import ToolRegistry


class EchoTool(Tool):
    name = "echo"
    description = "回音"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, **kwargs) -> str:
        return f"echo: {kwargs.get('text', '')}"


class BoomTool(Tool):
    name = "boom"
    description = "永遠失敗"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        raise RuntimeError("internal detail that must not leak")


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


class TestRegisterAndGet:
    def test_get_returns_registered_tool(self, registry):
        assert isinstance(registry.get("echo"), EchoTool)

    def test_get_returns_none_for_unknown(self, registry):
        assert registry.get("nope") is None

    def test_register_overwrites_same_name(self, registry):
        registry.register(EchoTool())
        assert len(registry.get_definitions()) == 1


class TestGetDefinitions:
    def test_returns_schema_per_tool(self, registry):
        repo = ToolRegistry()
        repo.register(EchoTool())
        repo.register(BoomTool())
        assert len(repo.get_definitions()) == 2

    def test_empty_registry_yields_empty_list(self):
        assert ToolRegistry().get_definitions() == []


class TestExecute:
    def test_runs_tool_with_params(self, registry):
        assert asyncio.run(registry.execute("echo", {"text": "hi"})) == "echo: hi"

    def test_unknown_tool_reports_name(self, registry):
        result = asyncio.run(registry.execute("ghost", {}))
        assert "ghost" in result

    def test_tool_exception_becomes_readable_message(self):
        """工具內部例外不得中斷 Agent 迴圈，且不應外洩內部細節。"""
        reg = ToolRegistry()
        reg.register(BoomTool())
        result = asyncio.run(reg.execute("boom", {}))
        assert isinstance(result, str)
        assert result
        assert "internal detail" not in result
