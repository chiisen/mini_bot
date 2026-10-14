"""Session management for conversation history."""

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from minibot.utils.helpers import ensure_dir, safe_filename

SESSION_TIMEOUT = timedelta(hours=24)


@dataclass
class Session:
    """
    對話 Session。

    訊息以 JSONL 格式儲存，支援持久化。

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    owner_id: str = ""  # User ID for session isolation
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # 已整合至檔案的訊息數

    def is_expired(self) -> bool:
        """檢查 session 是否過期。"""
        return datetime.now() - self.updated_at > SESSION_TIMEOUT

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """新增訊息至 session。"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """取得最近訊息（LLM 格式），保留 tool metadata。"""
        out: list[dict[str, Any]] = []
        for m in self.messages[-max_messages:]:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        """清除所有訊息並重置 session。"""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    管理對話 sessions。

    Sessions 以 JSONL 格式儲存於 sessions 目錄。
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".minibot" / "sessions"
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """取得 session 的檔案路徑。"""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.minibot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str, owner_id: str = "") -> Session:
        """
        取得既有 session 或建立新的。

        Args:
            key: Session key（通常為 channel:chat_id）。
            owner_id: 使用者 ID，用於會話隔離。

        Returns:
            Session 物件。
        """
        if key in self._cache:
            session = self._cache[key]
            if owner_id and session.owner_id and session.owner_id != owner_id:
                logger.warning("Session {} access denied for owner {}", key, owner_id)
                return Session(key=key, owner_id=owner_id)
            if session.is_expired():
                session.clear()
            return session

        session = self._load(key, owner_id)
        if session is None:
            session = Session(key=key, owner_id=owner_id)

        self._cache[key] = session
        return session

    def _load(self, key: str, owner_id: str = "") -> Session | None:
        """從磁碟載入 session。"""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                import shutil
                shutil.move(str(legacy_path), str(path))
                logger.info("Migrated session {} from legacy path", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0
            loaded_owner_id = ""

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                        loaded_owner_id = data.get("owner_id", "")
                    else:
                        messages.append(data)

            return Session(
                key=key,
                owner_id=owner_id or loaded_owner_id,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """儲存 session 至磁碟。"""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "owner_id": session.owner_id,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """從記憶體快取移除 session。"""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        列出所有 sessions。

        Returns:
            Session 資訊 dict 的清單。
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        # 用 `or ""` 而非預設參數：要處理的是「值為 None」而非「鍵不存在」。
        # 舊版或手動編輯過的 session 檔可能缺 updated_at，單一異常不應讓整批失敗。
        return sorted(
            sessions,
            key=lambda x: x.get("updated_at") or "",
            reverse=True,
        )
