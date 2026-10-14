# Windows 環境開發規則

## 1. 終端機要求

- **必須使用 PowerShell 7** (`pwsh`)，請勿使用 CMD 或舊版 PowerShell (v5.1)
- ShellTool 在 Windows 上會以 `pwsh -NoProfile -NonInteractive` 執行

## 2. Shell 指令語法

ShellTool 的白名單以名稱比對，而 PowerShell 本身提供下列別名，因此可直接沿用：

| 白名單名稱 | PowerShell 對應 |
| --- | --- |
| `ls` | `Get-ChildItem` |
| `cat` | `Get-Content` |
| `pwd` | `Get-Location` |
| `echo` | `Write-Output` |
| `mkdir` | `New-Item -ItemType Directory` |

`head`／`tail`／`grep` **沒有** PowerShell 別名，在 Windows 上不可用（會回報
`Command not found`）。原生 cmdlet（如 `Get-ChildItem`）目前不在白名單內。

```powershell
# 可用（別名）
ls -Recurse -File -Filter *.py minibot
cat config.json

# 不可用：head/tail/grep 無別名，且原生 cmdlet 不在白名單
Get-ChildItem -Recurse -File -Filter *.py minibot
```

## 3. 路徑分隔符

- 使用反斜線 `\` 或正斜線 `/` 均可相容
- 使用 `~` 代表使用者家目錄

## 4. ShellTool 實作說明

在 Windows 11 上使用 Python `subprocess` 呼叫 cmd 或內建 PowerShell 時，常遇到
stdout/stderr 無法擷取的問題（命令執行但無輸出）。

**原因**：Windows 11 中 cmd.exe 和內建 PowerShell (v5.1) 的輸出緩衝機制
（全緩衝模式），導致 `subprocess.PIPE` 無法即時讀取輸出。

**最終解決方案**：改用 **PowerShell 7** 作為執行殼層。

```python
# minibot/agent/tools/filesystem.py
if _is_windows():
    return ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command]
return cmd_parts
```

**依賴**：無額外依賴（僅標準庫 `subprocess`），但需自備 PowerShell 7。

> **歷史修正**：本節曾記載以 **plumbum** 解決。實際上 `plumbum.local` 不可呼叫，
> 該分支對任何指令都固定拋 `TypeError`，從未成功執行（#9）。plumbum 依賴已移除。

## 5. 驗證方式

**本專案不使用遠端 CI**，測試一律在本機執行：
`python -m pytest tests/ --cov=minibot --cov-report=term-missing`。
不要新增 CI workflow（維護者的明確決定）。

Windows 路徑的真實驗證靠 `TestShellWindowsEndToEnd`（實際跑 `pwsh`，比對 stdout）。
它只在 Windows 上跑，因此在非 Windows 平台一律 skip——要驗證 Windows 路徑，必須在
一台裝有 PowerShell 7 的 Windows 機器上直接執行測試；否則只能依賴以 monkeypatch
模擬的單元測試。此缺口見 [#23](https://github.com/chiisen/mini_bot/issues/23)。
