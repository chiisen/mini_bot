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
from minibot.i18n import t
from minibot.providers.base import LLMProvider
from minibot.session.manager import Session, SessionManager

# 未整合訊息達到此數才呼叫 LLM 摘要。30 約為 15 輪來回。
CONSOLIDATE_EVERY = 30
# MEMORY.md 整檔覆寫，必須有上限，否則每次整合都會把檔案撐大。
MEMORY_CHAR_LIMIT = 4000


def _parse_consolidation(text: str) -> tuple[str, str] | None:
    if "MEMORY:" not in text or "HISTORY:" not in text:
        return None
    memory = text.split("MEMORY:", 1)[1].split("HISTORY:", 1)[0].strip()
    history = text.split("HISTORY:", 1)[1].strip()
    if not memory or not history:
        return None
    return memory, history


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
        self.consolidate_every = CONSOLIDATE_EVERY
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
                content = resp.content or ""
                # 純空白也視為空回覆：送一串空白對使用者等於沒有回覆。
                if not content.strip():
                    # 回傳空字串會讓頻道「什麼都不送」——`split_for_telegram("")`
                    # 是空清單，使用者只看到自己送出的訊息、之後全無回應，然後
                    # 重送同一則（#22）。改回一句看得見的訊息，與 provider 端
                    # 例外時回 `error.llm_request_failed` 的處置一致。
                    logger.warning(
                        "LLM returned empty content with no tool calls; replying with fallback"
                    )
                    return t("error.empty_reply"), tools_used
                return content, tools_used

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

        return t("error.max_iterations_reached"), tools_used

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """直接處理訊息（CLI 使用）。"""
        session = self.session_mgr.get_or_create(session_key)
        history = session.get_history(self.memory_window)

        # 送進 LLM 的當輪內容由 build_messages 過濾；落盤使用同一個結果，
        # 否則被 [FILTERED] 的注入字串會以原始形式存入歷史，從下一輪起就
        # 繞過 sanitize_input()（過濾只在「當輪」有效）。build_messages 一律
        # 把當輪訊息接在最後，故直接取末項即可，不必重複過濾一次。
        messages = self.context.build_messages(history, content)
        safe_content = messages[-1]["content"]

        final_content, tools = await self._run_agent_loop(messages, on_progress)

        # 只在成功時落盤完整的一輪。失敗時若已寫入 user 訊息，歷史會變成
        # `user, user`（記憶體中已寫入、未落盤），送進 LLM 是不合法的結構。
        session.add_message("user", safe_content)
        session.add_message("assistant", final_content)
        self.session_mgr.save(session)
        await self._maybe_consolidate(session)

        return final_content

    async def _maybe_consolidate(self, session: Session) -> None:
        """把累積的對話摘要寫進 MEMORY.md / HISTORY.md。

        失敗只記日誌。訊息列表維持 append-only，不在這裡刪改。
        """
        end = len(session.messages)
        if end - session.last_consolidated < self.consolidate_every:
            return
        chunk = list(session.messages[session.last_consolidated:end])
        existing = self.context.memory.read_long_term()
        prompt = (
            "CONSOLIDATE the conversation into durable memory.\n"
            "Reply in the user's language with exactly two sections and nothing else:\n"
            "MEMORY:\n<updated long-term facts>\n"
            "HISTORY:\n<one log entry for the new messages>\n\n"
            f"Existing MEMORY:\n{existing}\n\n"
            "New messages:\n"
            + "\n".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:500]}"
                for m in chunk
            )
        )
        try:
            resp = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                model=self.model,
                max_tokens=1024,
                temperature=0.2,
            )
            parsed = _parse_consolidation(resp.content or "")
            if parsed is None:
                logger.warning("Memory consolidation returned an unusable summary")
                return
            memory_text, history_entry = parsed
            if len(memory_text) > MEMORY_CHAR_LIMIT:
                memory_text = memory_text[-MEMORY_CHAR_LIMIT:]
            self.context.memory.write_long_term(memory_text)
            self.context.memory.append_history(history_entry)
        except Exception as e:
            logger.warning("Memory consolidation failed: {}", e)
            return

        # 只前進計數。messages 本體不改，避免破壞已送出的 LLM cache 前提。
        session.last_consolidated = end
        self.session_mgr.save(session)

    async def run(self) -> None:
        """Bus 消費迴圈（Gateway 模式用）。

        單一消費者，因此所有入站訊息會被序列化處理。這正是 Telegram 需要的：
        python-telegram-bot 預設併發處理更新，直接呼叫 `process_direct()` 時
        同一使用者的相鄰訊息會交錯寫入 session，產生
        `user, user, assistant, assistant` 這種永久損壞的歷史。
        """
        self._running = True
        while self._running:
            msg = await self.bus.consume_inbound()
            try:
                result = await self.process_direct(
                    msg.content,
                    session_key=msg.session_key,
                )
            except Exception as e:
                logger.error("Error processing message: {}", e)
                # 只寫日誌等於讓使用者靜默無回應，必須把失敗送回頻道
                result = t("error.llm_request_failed")

            # metadata 一併回傳：頻道用它記路由資訊（例如實際回覆對象）
            try:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=result,
                    metadata=msg.metadata,
                ))
            except Exception as e:
                # 單一則發佈失敗不該殺掉整個消費迴圈——否則該 bot 對所有
                # 使用者靜默停止處理（見 channels/telegram.py 的 done callback）。
                logger.error("Failed to publish outbound for {}: {}", msg.session_key, e)

    def stop(self):
        """停止 Bus 消費迴圈。"""
        self._running = False
