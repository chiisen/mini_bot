"""File system tools."""

import subprocess
from pathlib import Path
from minibot.agent.tools.base import Tool

# 參數政策：
#   ARGS_ANY —— 唯讀指令，允許任意參數（仍受危險字元與敏感路徑檢查）
#   set      —— 具副作用指令，只允許清單中出現的子命令
#
# 注意：具副作用指令一律要求明確列出子命令。`git` 只開放讀取類操作，
# `pip` / `curl` 等只開放查詢版本或列表——因為 `pip install <任意套件>`
# 等同任意程式碼執行。
ARGS_ANY = "*"

ALLOWED_COMMANDS: dict[str, str | set[str]] = {
    # 唯讀：不改變系統狀態
    "ls": ARGS_ANY,
    "cat": ARGS_ANY,
    "head": ARGS_ANY,
    "tail": ARGS_ANY,
    "grep": ARGS_ANY,
    "pwd": ARGS_ANY,
    "echo": ARGS_ANY,
    # 建立檔案／目錄：具副作用，但限於執行者權限內
    "mkdir": ARGS_ANY,
    "touch": ARGS_ANY,
    # 具副作用：只允許明確列出的子命令
    "git": {"status", "log", "diff", "branch"},
    "pip": {"--version", "list"},
    "curl": {"--version"},
    "npm": {"--version"},
    "node": {"--version"},
    "python": {"--version"},
}

# 串接、重導向與其他可注入任意指令的樣式
DANGEROUS_PATTERNS = [
    "rm -rf", "rm /", "dd if=", "mkfs", ":(){:|:&};:",
    "curl | sh", "wget | sh", "&&", "||", ";", "|",
    ">", ">>", "<",
]

BLACKLISTED_PATTERNS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/.ssh/",
    "/.gnupg/",
    "/.aws/",
    "/.npm/",
    "/.composer/",
]

MAX_FILE_SIZE = 5 * 1024 * 1024

_workspace: Path | None = None


def set_workspace(workspace: Path) -> None:
    """Set the workspace path for file system tools."""
    global _workspace
    _workspace = workspace


def get_workspace() -> Path | None:
    """Get the current workspace path."""
    return _workspace


def validate_path(path: str) -> Path | None:
    """Validate and resolve path within workspace bounds."""
    workspace = get_workspace()
    if workspace is None:
        return None

    p = Path(path).expanduser().resolve()
    ws = workspace.resolve()

    for pattern in BLACKLISTED_PATTERNS:
        if pattern in str(p):
            return None

    if not str(p).startswith(str(ws)):
        return None

    return p


class ShellTool(Tool):
    """執行 Shell 指令。"""

    name = "shell"
    description = "Execute a shell command and return its output."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"}
        },
        "required": ["command"]
    }

    async def execute(self, **kwargs) -> str:
        command = kwargs.get("command", "")
        import platform
        is_windows = platform.system() == "Windows"

        if not self._is_command_allowed(command):
            return "Error: Command not allowed. Only predefined safe commands are permitted."

        try:
            if is_windows:
                from plumbum import local
                cmd_parts = command.split()
                result = local(cmd_parts[0], cmd_parts[1:], shell=False)
                output = result[1] if len(result) > 1 else str(result[0])
                return output
            else:
                cmd_parts = command.split()
                if not cmd_parts:
                    return "Error: Empty command"

                result = subprocess.run(
                    cmd_parts,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                )
                output = result.stdout or result.stderr or "(no output)"
                if result.returncode != 0:
                    return f"[Exit code: {result.returncode}]\n{output}"
                return output
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 60 seconds"
        except Exception as e:
            return f"Error: {e}"

    def _is_command_allowed(self, command: str) -> bool:
        """檢查指令是否符合參數政策。

        這是唯一的授權判斷點：`execute()` 不再重複檢查，避免兩處邏輯漂移。
        """
        if not command or not command.strip():
            return False

        for pattern in DANGEROUS_PATTERNS:
            if pattern in command:
                return False

        # shell 工具也必須套用檔案工具的敏感路徑黑名單，
        # 否則 `cat /etc/passwd` 會繞過檔案層的防護。
        for pattern in BLACKLISTED_PATTERNS:
            if pattern in command:
                return False

        cmd_parts = command.strip().split()
        if not cmd_parts:
            return False

        policy = ALLOWED_COMMANDS.get(cmd_parts[0])
        if policy is None:
            return False
        if policy == ARGS_ANY:
            return True

        # 具副作用指令：必須明確使用允許的子命令
        return len(cmd_parts) > 1 and cmd_parts[1] in policy


class ReadFileTool(Tool):
    """讀取檔案內容。"""

    name = "read_file"
    description = "Read the contents of a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path"}},
        "required": ["path"]
    }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path", "")
        p = validate_path(path)
        if p is None:
            return f"Error: Access denied. Path must be within workspace: {path}"
        if not p.exists():
            return f"Error: File not found: {path}"
        if p.stat().st_size > MAX_FILE_SIZE:
            return f"Error: File too large (max {MAX_FILE_SIZE} bytes)"
        content = p.read_text(encoding="utf-8")
        if len(content) > MAX_FILE_SIZE:
            content = content[:MAX_FILE_SIZE] + "\n... (truncated)"
        return content


class WriteFileTool(Tool):
    """寫入內容至檔案。"""

    name = "write_file"
    description = "Write content to a file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["path", "content"]
    }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        p = validate_path(path)
        if p is None:
            return f"Error: Access denied. Path must be within workspace: {path}"

        if len(content) > MAX_FILE_SIZE:
            return f"Error: Content too large (max {MAX_FILE_SIZE} bytes)"

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path}"


class ListDirTool(Tool):
    """列出目錄內容。"""

    name = "list_dir"
    description = "List contents of a directory."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path"}},
        "required": ["path"]
    }

    async def execute(self, **kwargs) -> str:
        path = kwargs.get("path", "")
        p = validate_path(path)
        if p is None:
            return f"Error: Access denied. Path must be within workspace: {path}"
        if not p.is_dir():
            return f"Error: Not a directory: {path}"
        items = []
        for item in sorted(p.iterdir()):
            prefix = "[DIR] " if item.is_dir() else "[FILE]"
            items.append(f"{prefix} {item.name}")
        return "\n".join(items) or "(empty directory)"
