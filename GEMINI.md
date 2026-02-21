# mini_bot 專案開發規則 (AI Agent Guides)

## 1. 核心理念與原則
- **簡潔至上**：恪守 KISS (Keep It Simple, Stupid) 原則，維持 `minibot` 核心程式碼簡潔。
- **千行約定**：專案設計目標為「約 1000 行左右」跑通完整 Agent Loop 與雙通道（CLI + Telegram）的極簡框架，避免過度工程化。

## 2. 任務結束自檢與記錄規範 (Definition of Done)
在完成任何功能開發、Bug 修正或重構任務後，Agent **必須主動**執行以下的「程式碼行數統計」與記錄動作，幫助追蹤專案體積變化：

### 2.1 執行統計指令
請使用 PowerShell 執行以下指令，取得 `minibot` 套件下的最新 `.py` 檔案總行數：

```powershell
$total = (Get-ChildItem -Recurse -File -Filter *.py minibot | Get-Content | Measure-Object -Line).Lines; Write-Host "目前程式碼總行數: $total"
```

### 2.2 收尾與回報
1. **回報差異**：在任務完成的回覆中，主動向使用者報告程式碼行數變化（例如：「本次更新後，總行數由 938 行增至 950 行」）。
2. **自動化更新**：主動根據變更內容更新專案根目錄的 `CHANGELOG.md`。
3. 若單次修改導致行數暴增（>100行），請主動說明原因，並確認是否符合「簡潔至上」的核心原則。
