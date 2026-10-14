"""Telegram channel — Polling mode, per-user session."""

import asyncio
from pathlib import Path

from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

from minibot.agent.loop import AgentLoop
from minibot.bus.events import InboundMessage
from minibot.bus.queue import MessageBus
from minibot.i18n import init as i18n_init
from minibot.providers.base import LLMProvider

# Telegram 單則訊息上限
REPLY_CHUNK_LIMIT = 4096

# InboundMessage.session_key 為 `channel:chat_id`，故 chat_id 採 `user_{id}`
# 以維持既有的 session key 格式（與既有 session 檔相容）。
CHANNEL = "tg"


class TelegramChannel:
    """Telegram Bot 頻道，使用 Polling 模式接收訊息。

    訊息流：handler → MessageBus.inbound → AgentLoop.run() → MessageBus.outbound
    → 送回 Telegram。頻道只負責「收訊 → publish_inbound」與「收 outbound → 送出」，
    與 Agent 解耦。

    為什麼要走 bus：python-telegram-bot 預設併發處理更新
    （`concurrent_updates=256`），過去 handler 直接 `await process_direct()` 時，
    同一使用者的相鄰訊息會併發進入同一個 session，落盤歷史變成
    `user, user, assistant, assistant` —— 順序永久損壞，且下次載入時
    LLM 會收到不合法的對話結構。bus 的單一消費者天然序列化處理。
    """

    def __init__(
        self,
        bot_token: str,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        memory_window: int = 50,
    ):
        self.bot_token = bot_token
        self.bus = MessageBus()
        self.agent_loop = AgentLoop(
            bus=self.bus,
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=max_iterations,
            temperature=temperature,
            max_tokens=max_tokens,
            memory_window=memory_window,
        )
        self._worker: asyncio.Task | None = None
        self._dispatcher: asyncio.Task | None = None
        i18n_init()

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /start 指令。"""
        await update.message.reply_text(
            "🤖 Hi! I'm mini_bot, your personal AI assistant.\n"
            "Send me any message to start chatting!"
        )

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """把訊息發布到 bus；不在此處呼叫 Agent。"""
        if not update.message or not update.message.text:
            return

        user_id = update.effective_user.id
        logger.info(
            "Telegram [{}] {}: {}",
            self._session_key(user_id),
            update.effective_user.first_name,
            update.message.text,
        )

        await self.bus.publish_inbound(InboundMessage(
            channel=CHANNEL,
            sender_id=str(user_id),
            chat_id=self._chat_key(user_id),
            content=update.message.text,
            # 實際回覆對象與 session 識別分開：session key 必須是
            # `tg:user_{id}`，但 Telegram 送訊息需要真正的 chat id。
            metadata={"reply_chat_id": update.effective_chat.id},
        ))

    @staticmethod
    def _chat_key(user_id: int) -> str:
        return f"user_{user_id}"

    @classmethod
    def _session_key(cls, user_id: int) -> str:
        """每個 Telegram 使用者獨立 Session（`tg:user_{user_id}`）。"""
        return f"{CHANNEL}:{cls._chat_key(user_id)}"

    async def _consume_inbound(self) -> None:
        """單一消費者：序列化處理所有使用者訊息。"""
        await self.agent_loop.run()

    async def _deliver_outbound(self, application: Application) -> None:
        """把 outbound 佇列的回覆送回 Telegram。"""
        while True:
            msg = await self.bus.consume_outbound()
            chat_id = msg.metadata.get("reply_chat_id", msg.chat_id)
            try:
                for i in range(0, len(msg.content), REPLY_CHUNK_LIMIT):
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=msg.content[i:i + REPLY_CHUNK_LIMIT],
                    )
            except Exception as e:
                logger.error("Failed to send reply to {}: {}", chat_id, e)

    async def _start_background(self, application: Application) -> None:
        """啟動 bus 消費與回覆派送 task。

        刻意不用 `application.create_task()`：PTB 的 `stop()` 會**等待**這些 task
        結束，而兩者都是無限迴圈，會讓關閉流程死鎖。改為不受管理的 task，
        並在 `_stop_background` 顯式取消。
        """
        self._worker = asyncio.create_task(self._consume_inbound(), name="minibot-agent-loop")
        self._dispatcher = asyncio.create_task(
            self._deliver_outbound(application), name="minibot-telegram-dispatcher"
        )

    async def _stop_background(self, application: Application) -> None:
        self.agent_loop.stop()
        tasks = [t for t in (self._worker, self._dispatcher) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def run(self) -> None:
        """啟動 Telegram Bot（Polling 模式）。"""
        # 調大 timeout（預設 5 秒太短，部分網路環境會逾時）
        request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
        app = (
            Application.builder()
            .token(self.bot_token)
            .request(request)
            .post_init(self._start_background)
            .post_shutdown(self._stop_background)
            .build()
        )

        # 註冊 handlers
        app.add_handler(CommandHandler("start", self._handle_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        logger.info("Telegram Bot started (Polling mode)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
