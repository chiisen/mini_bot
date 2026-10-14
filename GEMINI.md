# mini_bot 專案開發規則 (AI Agent Guides)

## 1. 核心理念與原則
- **簡潔至上**：恪守 KISS (Keep It Simple, Stupid) 原則，維持 `minibot` 核心程式碼簡潔。
- **千行約定**：專案設計目標為「約 1000 行左右」跑通完整 Agent Loop 與雙通道（CLI + Telegram）的極簡框架，避免過度工程化。

## 2. Windows 開發注意事項
- **終端機**：Windows 環境必須使用 **PowerShell 7** (`pwsh`)，不要使用 CMD 或舊版 PowerShell。
- **Shell 指令**：ShellTool 在 Windows 上會以 `pwsh -NoProfile -NonInteractive` 執行，因此指令需為 PowerShell 能理解的語法。白名單中的名稱（`ls`／`cat`／`pwd`／`echo`／`mkdir`）在 PowerShell 中都是別名，可直接使用；`head`／`tail`／`grep` 沒有對應別名，在 Windows 上不可用。原生 cmdlet（如 `Get-ChildItem`）目前不在白名單內。
- **路徑分隔符**：使用反斜線 `\` 或正斜線 `/` 均可相容。

## 3. ShellTool 跨平台問題記錄

### 3.1 問題描述
在 Windows 11 上使用 Python `subprocess` 呼叫 cmd 或內建 PowerShell 時，常遇到 stdout/stderr 無法擷取的問題（命令執行但無輸出）。

**原因**：Windows 11 中 cmd.exe 和內建 PowerShell (v5.1) 的輸出緩衝機制（全緩衝模式），導致 `subprocess.PIPE` 無法即時讀取輸出。

### 3.2 解決方案測試過程
1. **嘗試設定 COMSPEC 指向 內建 PowerShell (v5.1)**：使用 `os.environ["COMSPEC"]` 指向內建 PowerShell (v5.1)，仍有問題
2. **嘗試直接用 `powershell -Command`**：加上 `bufsize=0` 無緩衝，仍無法解決
3. **嘗試 ctypes 取得 Console Code Page**：編碼問題，仍無輸出

### 3.3 問題根因
- **有問題**：cmd.exe 與 Windows 內建 PowerShell (v5.1)
- **正常**：**PowerShell 7** (`pwsh`)

因此解法不是替換 subprocess 函式庫，而是**改用 PowerShell 7 作為執行殼層**。

### 3.4 最終實作
Windows 上一律透過 PowerShell 7 執行：

```python
# minibot/agent/tools/filesystem.py
def _build_argv(self, command: str) -> list[str] | None:
    cmd_parts = command.split()
    if not cmd_parts:
        return None
    if _is_windows():
        return ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command]
    return cmd_parts
```

`-NoProfile` 避免使用者 profile 的輸出或副作用混入結果；`-NonInteractive` 避免指令等待輸入而卡到逾時。其餘平台直接以 argv 執行。兩者共用同一段 `subprocess.run` 呼叫與錯誤處理。

> **歷史修正**：本節曾記載以 **plumbum** 作為最終解決方案。實際上 `plumbum.local`
> 是 `LocalMachine` 實例、不可呼叫，`local(cmd, args, shell=False)` 對任何指令
> 都固定拋 `TypeError: 'LocalMachine' object is not callable`，該分支從未成功
> 執行過（#9）。plumbum 依賴已移除。

### 3.5 依賴
無額外依賴，僅使用標準庫 `subprocess`。**執行環境需自備 PowerShell 7 (`pwsh`)**。

### 3.6 驗證方式
CI（`.github/workflows/ci.yml`）同時在 `ubuntu-latest` 與 `windows-latest` 上執行測試。
`TestShellWindowsEndToEnd` 只在 Windows 上跑，會實際透過 `pwsh` 執行白名單指令並
比對 stdout——這是 Windows 路徑唯一的真實驗證來源。

## 4. 任務結束自檢與記錄規範 (Definition of Done)
在完成任何功能開發、Bug 修正或重構任務後，Agent **必須主動**執行以下的「程式碼行數統計」與記錄動作，幫助追蹤專案體積變化：

### 4.1 執行統計指令
請使用 PowerShell 執行以下指令，取得 `minibot` 套件下的最新 `.py` 檔案總行數：

```powershell
$total = (Get-ChildItem -Recurse -File -Filter *.py minibot | Get-Content | Measure-Object -Line).Lines; Write-Host "目前程式碼總行數: $total"
```

### 4.2 收尾與回報
1. **回報差異**：在任務完成的回覆中，主動向使用者報告程式碼行數變化（例如：「本次更新後，總行數由 938 行增至 950 行」）。
2. **自動化更新**：主動根據變更內容更新專案根目錄的 `CHANGELOG.md`。
3. 若單次修改導致行數暴增（>100行），請主動說明原因，並確認是否符合「簡潔至上」的核心原則。
