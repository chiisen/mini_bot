"""Context builder (minimal)."""

import platform
import re
from datetime import datetime
from pathlib import Path
from minibot.agent.memory import MemoryStore

INJECTION_PATTERNS = [
    r"^\s*ignore\s+(previous|above|prior)",
    # 要求冒號分隔，否則 "root cause analysis"、"admin panel"、"systemd service"
    # 等正常輸入會被誤傷（本條是唯一攔截 `admin:` / `root:` 的規則，不可移除）。
    r"^\s*(system|admin|root)\s*:",
    r"(?i)you\s+(are\s+)?(now|just|simply)\s+a\s+(code|script)",
    r"(?i)forget\s+(everything|all|your)\s+(instructions|rules)",
    r"(?i)new\s+instructions:",
    r"(?i)system\s*:\s*",
    r"(?i)<\|system\|>",
    r"(?i)<\|user\|>",
    r"(?i)<\|assistant\|>",
    r"```system",
    r"#\s*system\s*$",
]


class ContextBuilder:
    """建立 LLM 所需的 system prompt 及對話訊息串列。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)

    def sanitize_input(self, content: str) -> str:
        """清理用戶輸入，防止 prompt injection。"""
        sanitized = content
        for pattern in INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "[FILTERED]", sanitized, flags=re.IGNORECASE)

        dangerous_sequences = [
            "【system】",
            "【SYSTEM】",
            "[system]",
            "[[system]]",
            "\x00",
        ]
        for seq in dangerous_sequences:
            sanitized = sanitized.replace(seq, "[FILTERED]")

        return sanitized[:10000]

    def build_system_prompt(self) -> str:
        """建立 system prompt，包含基本資訊、workspace 指引與長期記憶。"""
        parts = [
            "You are mini_bot 🤖, a personal AI assistant.",
            f"Platform: {platform.system()} {platform.release()}",
            f"Working directory: {self.workspace}",
            f"Current time: {datetime.now().isoformat()}",
        ]
        # 載入 workspace 引導檔
        for name in ("AGENTS.md", "SOUL.md"):
            f = self.workspace / name
            if f.exists():
                parts.append(f"\n## {name}\n{f.read_text(encoding='utf-8')}")
        # 載入長期記憶
        mem = self.memory.get_memory_context()
        if mem:
            parts.append(f"\n{mem}")
        return "\n\n".join(parts)

    def build_messages(self, history: list[dict], current_message: str) -> list[dict]:
        """組合完整的訊息串列（system + 歷史 + 目前訊息）。"""
        messages = [{"role": "system", "content": self.build_system_prompt()}]
        messages.extend(history)
        messages.append({"role": "user", "content": self.sanitize_input(current_message)})
        return messages
