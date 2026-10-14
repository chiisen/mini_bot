"""Tests for agent.tools.filesystem — 沙盒與 Shell 白名單的安全邊界。

這裡固定的是「必須維持」的安全性質：
- 路徑不得離開 workspace
- 敏感路徑（~/.ssh、/etc/passwd 等）必須被拒絕
- shell 的串接與重導向字元必須被擋下
- 非白名單指令不得通過

注意：本檔只涵蓋**目前正確**的行為。已知有兩處缺陷刻意不寫成斷言，
詳見 TODO.md 或相關 issue，避免把錯誤行為固化成「規格」。
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

    @pytest.mark.parametrize("command", ["ls", "pwd", "cat", "echo", "mkdir", "touch", "git"])
    def test_allows_whitelisted_commands(self, command):
        assert ShellTool()._is_command_allowed(command) is True

    @pytest.mark.parametrize("command", ["bash", "sh", "wget", "nc", "sudo", "chmod"])
    def test_rejects_non_whitelisted_commands(self, command):
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
