"""File system tools."""

import platform
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

# PowerShell 的敘述分隔與子運算式字元。
#
# `pwsh -Command` 會把整串指令「重新解析」一次，因此除了 `;` / `|`（已列於
# DANGEROUS_PATTERNS）之外，換行、`&`（呼叫運算子）、反引號（跳脫字元）、
# `$(...)` / `(...)`（子運算式）都能在同一行內接上第二道指令。此時白名單
# 只看第一個 token（`echo`），第二道指令便完全繞過授權檢查——`echo hi & whoami`
# 判定為允許，實際上會執行 `whoami`。
#
# Unix 走 argv、不經 shell，這些字元不具執行語意，故僅在 Windows 上拒絕，
# 以免誤傷 `grep '$'` 這類正常查詢。
WINDOWS_DANGEROUS_CHARS = "&`$()"

# 一律以「路徑片段」比對，不寫結尾斜線：`ls ~/.ssh` 這種**目錄本身**的寫法
# 在結尾斜線版本下不受檢，而 `grep -r . ~/.ssh` 同樣能印出私鑰內容。
BLACKLISTED_PATTERNS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/.ssh",
    "/.gnupg",
    "/.aws",
    "/.npm",
    "/.composer",
]


def _normalized(text: str) -> str:
    """正規化文字，讓黑名單在各種寫法下同樣生效。

    只修補「同一個路徑的不同寫法」，不改動語意：

    - **反斜線 → 正斜線**：Windows 的 `Path` 以反斜線呈現，
      `C:\\Users\\me\\.ssh\\id_rsa` 原本可繞過。
    - **大小寫折疊**：macOS 與 Windows 的檔案系統預設不分大小寫，
      `~/.SSH/id_rsa` 指向的就是同一把私鑰。
    - **收斂重複斜線與 `/./`**：`/etc//passwd`、`/etc/./passwd` 與
      `/etc/passwd` 是同一個檔案。
    """
    t = text.replace("\\", "/").casefold()
    while "//" in t:
        t = t.replace("//", "/")
    while "/./" in t:
        t = t.replace("/./", "/")
    return t

def _is_windows_command_safe(command: str) -> bool:
    """指令是否不含 PowerShell 的敘述分隔／子運算式字元。

    各類換行（LF / CR / VT / FF / NEL / U+2028 / U+2029）都是 PowerShell 的
    敘述分隔；製表符只作空白分隔，維持允許。
    """
    for ch in command:
        if ch in WINDOWS_DANGEROUS_CHARS:
            return False
        if ch != "\t" and (ord(ch) < 0x20 or ch in "\x7f\x85\u2028\u2029"):
            return False
    return True


MAX_FILE_SIZE = 5 * 1024 * 1024

# Windows 上一律透過 PowerShell 7 執行：cmd.exe 與內建 PowerShell 5.1 有輸出
# 緩衝問題，導致 subprocess 擷取不到輸出（GEMINI.md §3）。
SHELL_TIMEOUT = 60
WINDOWS_SHELL = "pwsh"


def _is_windows() -> bool:
    """是否在 Windows 上執行。

    獨立成函式以便測試替換——Windows 分支無法在非 Windows 平台上實跑，
    測試以 monkeypatch 覆寫此函式來驗證 argv 組裝與錯誤處理。
    """
    return platform.system() == "Windows"


_workspace: Path | None = None


def set_workspace(workspace: Path) -> None:
    """Set the workspace path for file system tools."""
    global _workspace
    _workspace = workspace


def get_workspace() -> Path | None:
    """Get the current workspace path."""
    return _workspace


def validate_path(path: str) -> Path | None:
    """Validate and resolve path within workspace bounds.

    相對路徑以 workspace 為基準解析（而非行程 CWD），與 ContextBuilder 在
    system prompt 中宣告的「Working directory 就是 workspace」一致。
    絕對路徑則照原樣處理，但仍須通過 workspace 邊界與黑名單檢查。
    """
    workspace = get_workspace()
    if workspace is None:
        return None

    ws = workspace.expanduser().resolve()
    candidate = Path(path).expanduser()
    # 絕對路徑直接使用；相對路徑掛在 workspace 之下。
    p = (candidate if candidate.is_absolute() else ws / candidate).resolve()

    for pattern in BLACKLISTED_PATTERNS:
        if pattern in _normalized(str(p)):
            return None

    # 用 is_relative_to 而非 str.startswith()：後者會把同名前綴的兄弟目錄
    # （例如 workspace=/x/ws 與 /x/ws_secret）誤判為在 workspace 內，造成沙盒逃逸。
    if not p.is_relative_to(ws):
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

    def _build_argv(self, command: str) -> list[str] | None:
        """組出交給 subprocess 的 argv。

        Windows 走 PowerShell 7（`pwsh`）並帶 `-NoProfile -NonInteractive`：
        前者避免使用者 profile 的輸出或副作用混入結果，後者避免指令等待輸入
        而卡到逾時。PowerShell 本身提供 `ls` / `cat` / `pwd` / `echo` /
        `mkdir` 等別名，因此白名單中的 Unix 名稱在 Windows 上同樣可用。
        """
        cmd_parts = command.split()
        if not cmd_parts:
            return None
        if _is_windows():
            return [
                WINDOWS_SHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ]
        return cmd_parts

    async def execute(self, **kwargs) -> str:
        command = kwargs.get("command", "")

        if not self._is_command_allowed(command):
            return "Error: Command not allowed. Only predefined safe commands are permitted."

        argv = self._build_argv(command)
        if argv is None:
            return "Error: Empty command"

        try:
            result = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                # Windows 主控台輸出可能不是 UTF-8；不讓解碼失敗毀掉整個結果
                errors="replace",
                timeout=SHELL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {SHELL_TIMEOUT} seconds"
        except FileNotFoundError:
            return f"Error: Command not found: {argv[0]}"
        except Exception as e:
            return f"Error: {e}"

        # 失敗時以 stderr 為主：命令可能先輸出一部分再失敗，此時要看到的是
        # 錯誤原因而非那半截 stdout。
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "(no output)").strip()
            return f"[Exit code: {result.returncode}]\n{detail}"

        return (result.stdout or result.stderr or "(no output)").strip()

    def _is_command_allowed(self, command: str) -> bool:
        """檢查指令是否符合參數政策。

        這是唯一的授權判斷點：`execute()` 不再重複檢查，避免兩處邏輯漂移。
        """
        if not command or not command.strip():
            return False

        for pattern in DANGEROUS_PATTERNS:
            if pattern in command:
                return False

        # Windows 上指令原文會交給 `pwsh -Command` 重新解析，須另外擋掉
        # PowerShell 專屬的敘述分隔符（見 WINDOWS_DANGEROUS_CHARS）。
        if _is_windows() and not _is_windows_command_safe(command):
            return False

        # shell 工具也必須套用檔案工具的敏感路徑黑名單，
        # 否則 `cat /etc/passwd` 會繞過檔案層的防護。
        # 先統一斜線：Windows 的反斜線路徑否則可繞過（`C:\Users\me\.ssh\...`）。
        normalized = _normalized(command)
        for pattern in BLACKLISTED_PATTERNS:
            if pattern in normalized:
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
