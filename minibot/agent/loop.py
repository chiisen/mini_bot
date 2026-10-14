"""Agent loop (minimal)."""

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

import json_repair
from loguru import logger

from minibot.agent.context import ContextBuilder
from minibot.agent.tools.registry import ToolRegistry
from minibot.agent.tools.filesystem import ReadFileTool, WriteFileTool, ListDirTool, ShellTool, set_workspace
from minibot.bus.events import InboundMessage, OutboundMessage
from minibot.bus.queue import MessageBus
from minibot.providers.base import LLMProvider
from minibot.session.manager import Session, SessionManager


class AgentLoop:
    """最小 Agent 迴圈：接收訊息 → 建立 context → 呼叫 LLM → 執行工具 → 回覆。"""

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        memory_window: int = 50,
        session_manager: SessionManager | None = None,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        set_workspace(workspace)
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.context = ContextBuilder(workspace)
        self.tools = ToolRegistry()
        self.session_mgr = session_manager or SessionManager(workspace)
        self._running = False

        # 註冊基本工具
        self.tools.register(ReadFileTool())
        self.tools.register(WriteFileTool())
        self.tools.register(ListDirTool())
        self.tools.register(ShellTool())

    async def _run_agent_loop(
        self,
        messages: list[dict],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, list[str]]:
        """核心迭代迴圈：LLM ↔ Tool 執行。"""
        tools_used = []

        for i in range(self.max_iterations):
            # 呼叫 LLM
            resp = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions() or None,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )

            # 無 tool calls → 回傳最終內容
            if not resp.has_tool_calls:
                return resp.content or "", tools_used

            # 有 tool calls → 加入 assistant 訊息並依序執行
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in resp.tool_calls
            ]
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                logger.info("Tool: {}({})", tc.name, tc.arguments)
                result = await self.tools.execute(tc.name, tc.arguments)
                tools_used.append(tc.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    # 工具輸出是不可信輸入，加邊界標記（見 ContextBuilder.wrap_tool_output）
                    "content": self.context.wrap_tool_output(tc.name, str(result)[:10000]),
                })

                if on_progress:
                    await on_progress(f"🔧 {tc.name}")

        return "Reached max iterations.", tools_used

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """直接處理訊息（CLI 使用）。"""
        session = self.session_mgr.get_or_create(session_key)
        history = session.get_history(self.memory_window)

        messages = self.context.build_messages(history, content)
        session.add_message("user", content)

        final_content, tools = await self._run_agent_loop(messages, on_progress)

        session.add_message("assistant", final_content)
        self.session_mgr.save(session)

        return final_content

    async def run(self) -> None:
        """Bus 消費迴圈（Gateway 模式用）。"""
        self._running = True
        while self._running:
            msg = await self.bus.consume_inbound()
            try:
                result = await self.process_direct(
                    msg.content,
                    session_key=msg.session_key,
                )
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=result,
                ))
            except Exception as e:
                logger.error("Error processing message: {}", e)

    def stop(self):
        """停止 Bus 消費迴圈。"""
        self._running = False
