"""Tests for session.manager — 對話歷史的持久化、隔離與逾時。

這裡固定的是「必須維持」的性質：訊息往返不遺失、tool metadata 不被剝除、
使用者之間互不窺視、legacy 檔案會被遷移而非棄置。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from minibot.session.manager import SESSION_TIMEOUT, Session, SessionManager


@pytest.fixture
def manager(tmp_path):
    return SessionManager(tmp_path)


class TestSessionMessageHandling:
    def test_add_message_records_role_content_and_timestamp(self):
        session = Session(key="cli:direct")
        session.add_message("user", "hello")
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "hello"
        assert "timestamp" in session.messages[0]

    def test_get_history_preserves_tool_metadata(self):
        """tool 訊息必須保留 tool_call_id／name，否則 LLM 無法配對呼叫與結果。"""
        session = Session(key="k")
        session.add_message("assistant", "", tool_calls=[{"id": "abc"}])
        session.add_message("tool", "result", tool_call_id="abc", name="read_file")

        history = session.get_history()
        assert history[0]["tool_calls"] == [{"id": "abc"}]
        assert history[1]["tool_call_id"] == "abc"
        assert history[1]["name"] == "read_file"

    def test_get_history_returns_only_last_n(self):
        session = Session(key="k")
        for i in range(10):
            session.add_message("user", str(i))
        assert [m["content"] for m in session.get_history(3)] == ["7", "8", "9"]

    def test_get_history_strips_internal_timestamp(self):
        """時間戳是本地記錄，不應送進 LLM 訊息。"""
        session = Session(key="k")
        session.add_message("user", "x")
        assert "timestamp" not in session.get_history()[0]

    def test_clear_resets_messages_and_consolidation_counter(self):
        session = Session(key="k")
        session.add_message("user", "x")
        session.last_consolidated = 5
        session.clear()
        assert session.messages == []
        assert session.last_consolidated == 0


class TestExpiryFlag:
    def test_expired_when_updated_at_exceeds_timeout(self):
        session = Session(key="k")
        session.updated_at = datetime.now() - SESSION_TIMEOUT - timedelta(seconds=1)
        assert session.is_expired() is True

    def test_not_expired_within_timeout(self):
        assert Session(key="k").is_expired() is False


class TestLoadRestoresUpdatedAt:
    """回歸測試（#16）：`_load()` 未還原 updated_at，使 SESSION_TIMEOUT
    跨行程完全失效——重啟後所有舊 session 都被視為剛更新過。"""

    def _write(self, directory, key, updated_at, include_stamp=True):
        meta = {
            "_type": "metadata",
            "key": key,
            "owner_id": "",
            "created_at": updated_at.isoformat(),
        }
        if include_stamp:
            meta["updated_at"] = updated_at.isoformat()
        path = directory / f"{key.replace(':', '_')}.jsonl"
        path.write_text(
            json.dumps(meta) + "\n" + json.dumps({"role": "user", "content": "x"}) + "\n",
            encoding="utf-8",
        )
        return path

    def test_restores_updated_at_from_disk(self, tmp_path):
        old = datetime.now() - timedelta(days=3)
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        self._write(sessions, "tg:user_1", old)

        session = SessionManager(tmp_path).get_or_create("tg:user_1")
        assert abs((session.updated_at - old).total_seconds()) < 1

    def test_old_session_is_expiring_after_restart(self, tmp_path):
        """跨越行程的逾時判斷：重啟後舊 session 應被判為過期。"""
        old = datetime.now() - timedelta(days=3)
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        self._write(sessions, "tg:user_1", old)

        session = SessionManager(tmp_path).get_or_create("tg:user_1")
        assert session.is_expired() is True

    def test_recent_session_not_expiring_after_restart(self, tmp_path):
        recent = datetime.now() - timedelta(minutes=5)
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        self._write(sessions, "tg:user_1", recent)

        session = SessionManager(tmp_path).get_or_create("tg:user_1")
        assert session.is_expired() is False

    def test_legacy_file_without_updated_at_still_loads(self, tmp_path):
        """舊格式 session 檔（無 updated_at）必須仍可載入，不得中斷。"""
        now = datetime.now()
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        self._write(sessions, "cli:direct", now, include_stamp=False)

        session = SessionManager(tmp_path).get_or_create("cli:direct")
        assert [m["content"] for m in session.messages] == ["x"]
        assert session.is_expired() is False


class TestPersistence:
    def test_round_trip_preserves_messages(self, manager, tmp_path):
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "hello")
        manager.save(session)

        reloaded = SessionManager(tmp_path).get_or_create("cli:direct")
        assert [m["content"] for m in reloaded.messages] == ["hello"]

    def test_filename_replaces_colon(self, manager, tmp_path):
        manager.save(manager.get_or_create("tg:user_42"))
        assert (tmp_path / "sessions" / "tg_user_42.jsonl").exists()

    def test_metadata_is_written_as_first_line(self, manager, tmp_path):
        manager.save(manager.get_or_create("cli:direct"))
        content = (tmp_path / "sessions" / "cli_direct.jsonl").read_text(encoding="utf-8")
        assert json.loads(content.splitlines()[0])["_type"] == "metadata"

    def test_unknown_key_creates_empty_session(self, manager):
        assert manager.get_or_create("nope:none").messages == []


class TestOwnerIsolation:
    def test_owner_mismatch_returns_empty_session(self, manager):
        """不同 owner 不得取得同一把 key 的既有對話。"""
        alice = manager.get_or_create("cli:direct", owner_id="alice")
        alice.add_message("user", "secret")
        manager.save(alice)

        mallory = manager.get_or_create("cli:direct", owner_id="mallory")
        assert mallory.messages == []
        assert mallory.owner_id == "mallory"

    def test_owner_mismatch_does_not_evict_cached_session(self, manager):
        """被拒的存取不應污染快取，原 owner 之後仍拿得到自己的對話。"""
        alice = manager.get_or_create("cli:direct", owner_id="alice")
        alice.add_message("user", "secret")

        manager.get_or_create("cli:direct", owner_id="mallory")
        again = manager.get_or_create("cli:direct", owner_id="alice")
        assert len(again.messages) == 1

    def test_matching_owner_reuses_cached_session(self, manager):
        first = manager.get_or_create("cli:direct", owner_id="alice")
        first.add_message("user", "hi")
        assert len(manager.get_or_create("cli:direct", owner_id="alice").messages) == 1


class TestExpiredCacheEviction:
    def test_expired_cached_session_is_cleared_on_access(self, manager):
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "old")
        session.updated_at = datetime.now() - SESSION_TIMEOUT - timedelta(seconds=1)

        assert manager.get_or_create("cli:direct").messages == []


class TestLegacyMigration:
    def test_moves_legacy_file_into_workspace(self, manager, tmp_path):
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        legacy_file = legacy / "tg_user_9.jsonl"
        legacy_file.write_text(
            json.dumps({
                "_type": "metadata",
                "key": "tg:user_9",
                "owner_id": "",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }) + "\n" + json.dumps({"role": "user", "content": "old"}) + "\n",
            encoding="utf-8",
        )
        manager.legacy_sessions_dir = legacy

        session = manager.get_or_create("tg:user_9")
        assert [m["content"] for m in session.messages] == ["old"]
        assert not legacy_file.exists(), "legacy 檔案應被移走而非留下"
        assert (tmp_path / "sessions" / "tg_user_9.jsonl").exists()

    def test_no_legacy_file_leaves_new_session_empty(self, manager, tmp_path):
        manager.legacy_sessions_dir = tmp_path / "nonexistent"
        assert manager.get_or_create("tg:user_1").messages == []


class TestInvalidate:
    def test_invalidate_drops_cached_session(self, manager, tmp_path):
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "x")
        manager.save(session)

        manager.invalidate("cli:direct")
        reloaded = manager.get_or_create("cli:direct")
        assert [m["content"] for m in reloaded.messages] == ["x"]


class TestCorruptSessionFile:
    def test_corrupt_file_falls_back_to_empty_session(self, manager, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "cli_direct.jsonl").write_text("{ not json", encoding="utf-8")

        assert manager.get_or_create("cli:direct").messages == []

    def test_blank_lines_are_ignored(self, manager, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "cli_direct.jsonl").write_text(
            json.dumps({
                "_type": "metadata",
                "key": "cli:direct",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }) + "\n"
            + "\n"
            + json.dumps({"role": "user", "content": "hi"}) + "\n",
            encoding="utf-8",
        )

        session = manager.get_or_create("cli:direct")
        assert [m["content"] for m in session.messages] == ["hi"]


class TestListSessions:
    def test_empty_workspace_returns_empty_list(self, manager):
        assert manager.list_sessions() == []

    def test_corrupt_file_is_skipped_not_fatal(self, manager, tmp_path):
        """單一損壞檔案不應讓整個列表失效。"""
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "bad.jsonl").write_text("{ not json", encoding="utf-8")
        assert manager.list_sessions() == []

    def test_missing_updated_at_does_not_crash(self, manager, tmp_path):
        """回歸測試（#17）：metadata 缺 updated_at 時原本整批拋 TypeError。

        `.get(key, "")` 的預設值無效，因為鍵存在、值為 None。
        """
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "cli_direct.jsonl").write_text(
            json.dumps({
                "_type": "metadata",
                "key": "cli:direct",
                "created_at": datetime.now().isoformat(),
            }) + "\n",
            encoding="utf-8",
        )

        items = manager.list_sessions()
        assert len(items) == 1
        assert items[0]["updated_at"] is None

    def test_sessions_with_and_without_updated_at_coexist(self, manager, tmp_path):
        """缺欄位與正常欄位應能共存，且有時間戳的排在前面。"""
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "no_stamp.jsonl").write_text(
            json.dumps({"_type": "metadata", "key": "cli:direct",
                        "created_at": datetime.now().isoformat()}) + "\n",
            encoding="utf-8",
        )
        (sessions / "tg_user_9.jsonl").write_text(
            json.dumps({"_type": "metadata", "key": "tg:user_9",
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat()}) + "\n",
            encoding="utf-8",
        )

        keys = [i["key"] for i in manager.list_sessions()]
        assert keys == ["tg:user_9", "cli:direct"]

    def test_lists_saved_sessions_sorted_newest_first(self, manager, tmp_path):
        manager.save(manager.get_or_create("cli:direct"))
        manager.save(manager.get_or_create("tg:user_1"))

        items = SessionManager(tmp_path).list_sessions()
        assert {i["key"] for i in items} == {"cli:direct", "tg:user_1"}

        updated = [i["updated_at"] for i in items]
        assert updated == sorted(updated, reverse=True)


class TestPruneAndDelete:
    def test_prune_removes_only_old_files_and_keeps_unstamped(self, manager):
        old = manager.get_or_create("cli:old")
        old.updated_at = datetime.now() - timedelta(days=8)
        old.add_message("user", "keep-me-not")
        # add_message 會把 updated_at 刷成現在，測的是 prune 讀磁碟時間戳。
        old.updated_at = datetime.now() - timedelta(days=8)
        manager.save(old)

        recent = manager.get_or_create("cli:new")
        recent.add_message("user", "stay")
        manager.save(recent)

        unstamped = manager.sessions_dir / "cli_plain.jsonl"
        unstamped.write_text(
            json.dumps({"_type": "metadata", "key": "cli:plain", "created_at": datetime.now().isoformat()}) + "\n",
            encoding="utf-8",
        )

        removed = manager.prune(timedelta(days=7))
        assert removed == ["cli:old"]
        assert manager.get("cli:old") is None
        assert manager.get("cli:new").messages[0]["content"] == "stay"
        assert unstamped.exists()

    def test_delete_missing_session_returns_false(self, manager):
        assert manager.delete("cli:missing") is False

    def test_get_does_not_clear_expired_session(self, manager):
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "old")
        session.updated_at = datetime.now() - timedelta(days=3)
        manager.save(session)
        manager.invalidate("cli:direct")

        loaded = SessionManager(manager.workspace).get("cli:direct")
        assert loaded.messages[0]["content"] == "old"
