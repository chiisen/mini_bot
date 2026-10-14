"""Tests for agent.memory — MEMORY.md / HISTORY.md 讀寫。

`write_long_term()` 與 `append_history()` 目前尚無呼叫點（見 #6），
但仍屬公開 API；這裡固定其基本行為，避免日後接線時才發現壞掉。
"""
import pytest

from minibot.agent.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestReadLongTerm:
    def test_returns_empty_when_file_absent(self, store):
        assert store.read_long_term() == ""

    def test_reads_existing_content(self, store):
        store.write_long_term("- 使用者偏好繁體中文")
        assert store.read_long_term() == "- 使用者偏好繁體中文"

    def test_preserves_unicode(self, store):
        store.write_long_term("emoji 🤖 與中文")
        assert "🤖" in store.read_long_term()


class TestWriteLongTerm:
    def test_creates_memory_dir(self, tmp_path):
        """workspace 底下可能還沒有 memory/，寫入不得失敗。"""
        workspace = tmp_path / "fresh"
        workspace.mkdir()
        assert MemoryStore(workspace).memory_dir.is_dir()

    def test_overwrites_previous_content(self, store):
        """整檔覆寫是既有語意；呼叫端需自行控制長度（見 #6）。"""
        store.write_long_term("舊內容")
        store.write_long_term("新內容")
        assert store.read_long_term() == "新內容"


class TestAppendHistory:
    def test_appends_with_blank_line_separator(self, store):
        store.append_history("第一次")
        store.append_history("第二次")
        assert store.history_file.read_text(encoding="utf-8") == "第一次\n\n第二次\n\n"

    def test_strips_trailing_whitespace_from_entry(self, store):
        store.append_history("有尾隨空白   \n")
        assert store.history_file.read_text(encoding="utf-8") == "有尾隨空白\n\n"

    def test_content_is_append_only(self, store):
        """HISTORY.md 是日誌，寫入不得清除既有內容。"""
        store.append_history("最早")
        store.append_history("之後")
        content = store.history_file.read_text(encoding="utf-8")
        assert content.index("最早") < content.index("之後")


class TestGetMemoryContext:
    def test_empty_when_no_long_term_memory(self, store):
        assert store.get_memory_context() == ""

    def test_wraps_content_in_section_heading(self, store):
        store.write_long_term("- 事實")
        assert store.get_memory_context() == "## Long-term Memory\n- 事實"

    def test_history_is_not_injected(self, store):
        """HISTORY.md 只進日誌，不進 prompt——否則會無限膨脹。"""
        store.append_history("不該出現")
        assert "不該出現" not in store.get_memory_context()
