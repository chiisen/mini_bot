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


def _conservative_width(text: str) -> int:
    """Python 字元數與 UTF-16 碼元數取較大者。

    Telegram 文件只寫 4096，沒有約定用哪一種數法。emoji 在字元數是 1、
    在 UTF-16 是 2。取較大值就會較早切開，兩種算法都不會超限。
    """
    if not text:
        return 0
    return max(len(text), len(text.encode("utf-16-le")) // 2)


def split_for_telegram(text: str, limit: int = REPLY_CHUNK_LIMIT) -> list[str]:
    """依較嚴格的長度切分訊息，使每一段同時不超過字元數與 UTF-16 碼元數。"""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for ch in text:
        width = _conservative_width(ch)
        if current and size + width > limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(ch)
        size += width
    if current:
        chunks.append("".join(current))
    return chunks


def _log_task_death(task: asyncio.Task) -> None:
    """背景 task 若因例外結束，必須留下痕跡。

    兩個背景 task 都是無限迴圈，正常情況下只有取消才會結束。若它們因例外
    死亡而沒人取用例外，該 bot 會對所有使用者靜默停止處理——只剩一行
    `Task exception was never retrieved`。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task {} died: {}", task.get_name(), exc)


class TelegramChannel:
    """Telegram Bot 頻道，使用 Polling 模式接收訊息。

    訊息流：handler → MessageBus.inbound → AgentLoop.run() → MessageBus.outbound
    → 送回 Telegram。頻道只負責「收訊 → publish_inbound」與「收 outbound → 送出」，
    與 Agent 解耦。

    為什麼要走 bus：舊路徑在 handler 裡直接 `await process_direct()`。
    兩個 handler 同時跑時，同一使用者的相鄰訊息會交錯寫入 session，
    落盤變成 `user, user, assistant, assistant`。bus 的單一消費者一次只處理
    一則，歷史維持一問一答。目前 builder 沒有打開 `concurrent_updates`，
    函式庫預設一次處理一則更新，因此入隊順序就是送達順序。若日後打開並行，
    入隊順序可能對調；那是後續優化（#32），這裡不另外排序。
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
        self._background_stopped = False
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
            # 不用 `msg.chat_id` 當後備：它是 session 識別（`user_{id}`），
            # 不是合法的 Telegram chat id，送出去必然 BadRequest。
            chat_id = msg.metadata.get("reply_chat_id")
            if chat_id is None:
                logger.error(
                    "Outbound message for {}:{} has no reply_chat_id; dropped",
                    msg.channel,
                    msg.chat_id,
                )
                continue

            chunks = split_for_telegram(msg.content)
            if not chunks:
                logger.warning("Empty outbound reply for {}:{}", msg.channel, msg.chat_id)
                continue

            # 逐段獨立送出：某一段失敗不該讓其餘段落連帶消失
            for chunk in chunks:
                try:
                    await application.bot.send_message(chat_id=chat_id, text=chunk)
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
        for task in (self._worker, self._dispatcher):
            task.add_done_callback(_log_task_death)

    async def _stop_background(self, application: Application) -> None:
        """取消兩個背景迴圈。重複呼叫無效果。"""
        if self._background_stopped:
            return
        self._background_stopped = True
        self.agent_loop.stop()
        tasks = [t for t in (self._worker, self._dispatcher) if t is not None and not t.done()]
        pending = []
        for task in tasks:
            task.cancel()
            # run_polling 若已把 event loop 關掉，任務無法再 await。
            try:
                if not task.get_loop().is_closed():
                    pending.append(task)
            except RuntimeError:
                continue
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

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
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        finally:
            # PTB 的 run_polling 在 Ctrl-C／SIGTERM／例外的 finally 會呼叫
            # post_shutdown。若某條路徑沒叫到，這裡補一次。迴圈已關時只做取消。
            if not self._background_stopped:
                asyncio.run(self._stop_background(app))
