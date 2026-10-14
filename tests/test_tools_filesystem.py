"""Tests for agent.tools.filesystem — 沙盒與 Shell 白名單的安全邊界。

這裡固定的是「必須維持」的安全性質：
- 路徑不得離開 workspace
- 敏感路徑（~/.ssh、/etc/passwd 等）必須被拒絕
- shell 的串接與重導向字元必須被擋下
- 非白名單指令、以及具副作用指令的未列舉子命令不得通過
"""
import asyncio
from pathlib import Path

import pytest

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
