"""File system tools."""

import subprocess
from pathlib import Path
from minibot.agent.tools.base import Tool


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

    async def execute(self, command: str) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=60,
            )
            try:
                stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
                stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                output = stdout or stderr or "(no output)"
            except:
                stdout = result.stdout.decode("latin-1", errors="replace") if result.stdout else ""
                stderr = result.stderr.decode("latin-1", errors="replace") if result.stderr else ""
                output = stdout or stderr or "(no output)"
            if result.returncode != 0:
                return f"[Exit code: {result.returncode}]\n{output}"
            return output
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 60 seconds"
        except Exception as e:
            return f"Error: {e}"


class ReadFileTool(Tool):
    """讀取檔案內容。"""

    name = "read_file"
    description = "Read the contents of a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path"}},
        "required": ["path"]
    }

    async def execute(self, path: str) -> str:
        p = Path(path).expanduser()
        if not p.exists():
            return f"Error: File not found: {path}"
        return p.read_text(encoding="utf-8")[:50000]


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

    async def execute(self, path: str, content: str) -> str:
        p = Path(path).expanduser()
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

    async def execute(self, path: str) -> str:
        p = Path(path).expanduser()
        if not p.is_dir():
            return f"Error: Not a directory: {path}"
        items = []
        for item in sorted(p.iterdir()):
            prefix = "[DIR] " if item.is_dir() else "[FILE]"
            items.append(f"{prefix} {item.name}")
        return "\n".join(items) or "(empty directory)"
