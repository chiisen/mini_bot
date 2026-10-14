"""Tests for channels.telegram — bus 接線與回覆派送。

固定的是「必須維持」的性質（#2 的驗收標準）：
- Session key 仍為 `tg:user_{user_id}`（多使用者隔離行為不變）
- 訊息處理經由 MessageBus，`AgentLoop.run()` 實際被執行
- 回覆分段送出，且送給正確的 Telegram chat

這裡不連線 Telegram：`Application` 以 mock 取代，只驗證訊息流與路由。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from minibot.bus.events import InboundMessage, OutboundMessage
from minibot.channels.telegram import (
    CHANNEL,
    REPLY_CHUNK_LIMIT,
    TelegramChannel,
    _log_task_death,
    split_for_telegram,
)
from minibot.providers.base import LLMProvider, LLMResponse


@pytest.fixture
def loguru_messages():
    """收集 loguru 的訊息（loguru 預設不進標準 logging，caplog 收不到）。"""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    yield messages
    logger.remove(sink_id)


class EchoProvider(LLMProvider):
    """回音 provider，避免任何網路請求。"""

    def __init__(self):
        super().__init__("k")
        self.seen = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        user = [m for m in messages if m["role"] == "user"][-1]["content"]
        self.seen.append(user)
        return LLMResponse(content=f"echo: {user}")

    def get_default_model(self):
        return "test/model"


@pytest.fixture
def channel(tmp_path):
    return TelegramChannel(bot_token="token", provider=EchoProvider(), workspace=tmp_path)


def make_update(user_id=123, text="hi", chat_id=None):
    """組出 _handle_message 需要的最小 Update 介面。"""
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "Tester"
    update.effective_chat.id = chat_id if chat_id is not None else user_id
    return update


class TestSessionIsolation:
    def test_session_key_matches_legacy_format(self, channel):
        """#2 驗收：session 隔離行為維持不變。"""
        assert channel._session_key(123) == "tg:user_123"

    def test_session_key_derives_from_inbound_message(self):
        """InboundMessage.session_key 必須等於原本的 `tg:user_{id}`，否則舊 session 會失聯。"""
        msg = InboundMessage(
            channel=CHANNEL, sender_id="123", chat_id="user_123", content="hi"
        )
        assert msg.session_key == "tg:user_123"

    def test_distinct_users_get_distinct_sessions(self, channel):
        assert channel._session_key(1) != channel._session_key(2)


class TestHandleMessage:
    def test_publishes_inbound_instead_of_calling_agent(self, channel):
        update = make_update(user_id=123, text="哈囉")
        asyncio.run(channel._handle_message(update, None))

        msg = channel.bus.inbound.get_nowait()
        assert msg.channel == CHANNEL
        assert msg.content == "哈囉"
        assert msg.session_key == "tg:user_123"

    def test_records_reply_chat_id(self, channel):
        """session 識別與回覆對象不同，回覆對象必須帶上。"""
        update = make_update(user_id=123, chat_id=555)
        asyncio.run(channel._handle_message(update, None))

        msg = channel.bus.inbound.get_nowait()
        assert msg.chat_id == "user_123"
        assert msg.metadata["reply_chat_id"] == 555

    def test_ignores_non_text_messages(self, channel):
        update = make_update()
        update.message.text = None
        asyncio.run(channel._handle_message(update, None))
        assert channel.bus.inbound.empty()

    def test_ignores_missing_message(self, channel):
        update = make_update()
        update.message = None
        asyncio.run(channel._handle_message(update, None))
        assert channel.bus.inbound.empty()

    def test_does_not_reply_directly(self, channel):
        """#2：處理改由背景消費者負責，handler 不應直接回覆。"""
        update = make_update()
        asyncio.run(channel._handle_message(update, None))
        update.message.reply_text.assert_not_awaited()


class TestHandleStart:
    def test_replies_welcome(self, channel):
        update = make_update()
        asyncio.run(channel._handle_start(update, None))
        update.message.reply_text.assert_awaited_once()


class TestDeliverOutbound:
    def _run_dispatch(self, channel, outgoing):
        """把 outgoing 全部餵進 outbound 佇列並跑一輪派送。"""
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        async def scenario():
            for msg in outgoing:
                await channel.bus.publish_outbound(msg)
            task = asyncio.ensure_future(channel._deliver_outbound(app))
            # 等到佇列清空
            while not channel.bus.outbound.empty():
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return app

        return asyncio.run(scenario())

    def test_sends_reply_to_recorded_chat_id(self, channel):
        app = self._run_dispatch(channel, [OutboundMessage(
            channel=CHANNEL, chat_id="user_123", content="回覆",
            metadata={"reply_chat_id": 555},
        )])
        app.bot.send_message.assert_awaited_with(chat_id=555, text="回覆")

    def test_drops_message_without_reply_chat_id(self, channel):
        """缺 reply_chat_id 時不得以 session 識別當 chat id 送出。

        回歸測試：原本後備成 `msg.chat_id`（即 `user_{id}`），這不是合法的
        Telegram chat id，送出去必然 BadRequest；寧可留下明確錯誤日誌。
        """
        app = self._run_dispatch(channel, [OutboundMessage(
            channel=CHANNEL, chat_id="user_123", content="回覆",
        )])
        app.bot.send_message.assert_not_awaited()

    def test_chunks_long_reply(self, channel):
        """Telegram 單則上限 4096 字元，超過必須分段。"""
        long_text = "x" * (REPLY_CHUNK_LIMIT + 10)
        app = self._run_dispatch(channel, [OutboundMessage(
            channel=CHANNEL, chat_id="user_123", content=long_text,
            metadata={"reply_chat_id": 555},
        )])

        assert app.bot.send_message.await_count == 2
        sent = [c.kwargs["text"] for c in app.bot.send_message.await_args_list]
        assert len(sent[0]) == REPLY_CHUNK_LIMIT
        assert len(sent[1]) == 10
        assert "".join(sent) == long_text

    def test_send_failure_does_not_kill_dispatcher(self, channel):
        """單則送出失敗不應讓派送迴圈停止。"""
        app = MagicMock()
        app.bot.send_message = AsyncMock(
            side_effect=[RuntimeError("boom"), None]
        )

        async def scenario():
            task = asyncio.ensure_future(channel._deliver_outbound(app))
            await channel.bus.publish_outbound(OutboundMessage(
                channel=CHANNEL, chat_id="user_1", content="第一則",
                metadata={"reply_chat_id": 1},
            ))
            await channel.bus.publish_outbound(OutboundMessage(
                channel=CHANNEL, chat_id="user_1", content="第二則",
                metadata={"reply_chat_id": 1},
            ))
            while app.bot.send_message.await_count < 2:
                await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        assert app.bot.send_message.await_count == 2

    def test_failed_chunk_does_not_drop_the_rest(self, channel):
        """某一段失敗時，其餘段落仍必須送出。

        回歸測試：分段迴圈原本被包在單一 `try` 內，任一段失敗就中斷整個
        for 迴圈，後續段落無聲消失——使用者只看到被截斷的回覆。
        """
        app = MagicMock()
        app.bot.send_message = AsyncMock(
            side_effect=[RuntimeError("第二段失敗"), None, None]
        )
        text = "x" * (REPLY_CHUNK_LIMIT * 2 + 5)  # 三段

        async def scenario():
            task = asyncio.ensure_future(channel._deliver_outbound(app))
            await channel.bus.publish_outbound(OutboundMessage(
                channel=CHANNEL, chat_id="user_1", content=text,
                metadata={"reply_chat_id": 1},
            ))
            while app.bot.send_message.await_count < 3:
                await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(asyncio.wait_for(scenario(), 3.0))
        assert app.bot.send_message.await_count == 3


class TestSplitForTelegram:
    """Telegram 的長度上限以 UTF-16 碼元計算，不是 Python 字元數。"""

    def test_short_text_is_one_chunk(self):
        assert split_for_telegram("哈囉") == ["哈囉"]

    def test_empty_text_yields_no_chunks(self):
        assert split_for_telegram("") == []

    def test_non_bmp_chars_count_as_two_units(self):
        """回歸測試：4096 個 emoji 是 8192 碼元，以 `len()` 切分會整段送出而遭拒。"""
        emoji = "\U0001F534"  # 🔴，非 BMP
        chunks = split_for_telegram(emoji * REPLY_CHUNK_LIMIT)

        assert len(chunks) == 2
        assert all(len(c) <= REPLY_CHUNK_LIMIT // 2 for c in chunks)
        assert "".join(chunks) == emoji * REPLY_CHUNK_LIMIT

    def test_chunks_never_exceed_the_limit(self):
        for text in ["x" * 8193, "\U0001F534" * 3000, "a\U0001F534" * 3000]:
            chunks = split_for_telegram(text)
            assert "".join(chunks) == text
            assert all(
                len(c.encode("utf-16-le")) // 2 <= REPLY_CHUNK_LIMIT for c in chunks
            )


class TestBackgroundLifecycle:
    """背景 task 的啟動與關閉。

    這裡固定的是「關閉時必須能結束」：兩個迴圈都是無限迴圈，且刻意不使用
    `application.create_task()`（PTB 的 `stop()` 會等待受管理的 task 結束，
    會造成死鎖）。若 `_stop_background` 沒有真的取消它們，測試會逾時。
    """

    def test_start_creates_both_tasks(self, channel):
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        async def scenario():
            await channel._start_background(app)
            try:
                assert channel._worker is not None
                assert channel._dispatcher is not None
                assert not channel._worker.done()
                assert not channel._dispatcher.done()
            finally:
                await channel._stop_background(app)

        asyncio.run(asyncio.wait_for(scenario(), 3.0))

    def test_stop_cancels_tasks_and_returns(self, channel):
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        async def scenario():
            await channel._start_background(app)
            tasks = (channel._worker, channel._dispatcher)
            await asyncio.wait_for(channel._stop_background(app), 3.0)
            return tasks

        tasks = asyncio.run(scenario())
        assert all(t.done() for t in tasks)
        assert channel.agent_loop._running is False

    def test_stop_is_safe_without_start(self, channel):
        asyncio.run(channel._stop_background(MagicMock()))

    def test_worker_actually_processes_inbound(self, channel):
        """背景消費者必須真的把 inbound 送進 Agent（避免 run() 成為 dead code）。"""
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        async def scenario():
            await channel._start_background(app)
            try:
                await channel._handle_message(make_update(text="哈囉"), None)
                for _ in range(50):
                    await asyncio.sleep(0)
                    if app.bot.send_message.await_count:
                        break
            finally:
                await asyncio.wait_for(channel._stop_background(app), 3.0)

        asyncio.run(asyncio.wait_for(scenario(), 5.0))
        app.bot.send_message.assert_awaited_with(chat_id=123, text="echo: 哈囉")

    def test_dying_background_task_is_logged(self, channel, loguru_messages):
        """背景 task 意外死亡必須留下痕跡，而非靜默停止服務。

        這兩個 task 都是無限迴圈，正常只會因取消而結束。若因例外死亡而沒人
        取用例外，該 bot 會對所有使用者停止處理，只剩一行
        `Task exception was never retrieved`。
        """
        async def scenario():
            async def boom():
                raise RuntimeError("queue broken")

            task = asyncio.create_task(boom(), name="minibot-agent-loop")
            task.add_done_callback(_log_task_death)
            await asyncio.sleep(0)
            for _ in range(10):
                await asyncio.sleep(0)
                if task.done():
                    break

        asyncio.run(asyncio.wait_for(scenario(), 3.0))
        assert any("minibot-agent-loop died" in m for m in loguru_messages)

    def test_cancelled_background_task_is_not_noisy(self, loguru_messages):
        """正常關閉（取消）不該被當成意外死亡。"""
        async def scenario():
            task = asyncio.create_task(asyncio.sleep(60), name="minibot-agent-loop")
            task.add_done_callback(_log_task_death)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(asyncio.wait_for(scenario(), 3.0))
        assert not [m for m in loguru_messages if "died" in m]


class TestEndToEndThroughBus:
    def test_message_reaches_agent_and_returns_via_bus(self, channel):
        """#2 核心驗收：訊息經 MessageBus 抵達 Agent，回覆也經 MessageBus 回來。"""
        asyncio.run(channel._handle_message(make_update(text="你好"), None))

        async def scenario():
            task = asyncio.ensure_future(channel._consume_inbound())
            try:
                return await asyncio.wait_for(channel.bus.consume_outbound(), 2.0)
            finally:
                channel.agent_loop.stop()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        outbound = asyncio.run(scenario())
        assert outbound.content == "echo: 你好"
        assert outbound.chat_id == "user_123"
        assert outbound.metadata["reply_chat_id"] == 123

    def test_concurrent_messages_are_serialized(self, channel):
        """#2 的動機：同一使用者的相鄰訊息過去會併發而交錯損壞歷史。

        經 bus 後由單一消費者處理，落盤順序必須是 user/assistant 成對。
        """
        import json

        async def scenario():
            for text in ("第一題", "第二題"):
                await channel._handle_message(make_update(text=text), None)

            task = asyncio.ensure_future(channel._consume_inbound())
            try:
                replies = [
                    await asyncio.wait_for(channel.bus.consume_outbound(), 2.0)
                    for _ in range(2)
                ]
            finally:
                channel.agent_loop.stop()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return replies

        replies = asyncio.run(scenario())
        assert [r.content for r in replies] == ["echo: 第一題", "echo: 第二題"]

        session_file = channel.agent_loop.workspace / "sessions" / "tg_user_123.jsonl"
        records = [
            json.loads(line)
            for line in session_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        roles = [r["role"] for r in records if r.get("role")]
        assert roles == ["user", "assistant", "user", "assistant"]
