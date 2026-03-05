# mini_bot 專案開發規則 (AI Agent Guides)

## 1. 核心理念與原則
- **簡潔至上**：恪守 KISS (Keep It Simple, Stupid) 原則，維持 `minibot` 核心程式碼簡潔。
- **千行約定**：專案設計目標為「約 1000 行左右」跑通完整 Agent Loop 與雙通道（CLI + Telegram）的極簡框架，避免過度工程化。

## 2. 圖表規範
> 參照全局規則 [5.5 📊 Mermaid 程式圖規則](#)。
> 新增核心功能時，優先在 README.md 加入 Mermaid 流程圖，圖表節點使用**繁體中文**。

## 3. 任務結束自檢 (Definition of Done)
> 參照全局規則 [8.3 任務完結定義](#)。

### 3.1 程式碼行數統計（專案特有）
除全局 DoD 檢查外，須執行行數統計追蹤專案體積變化：

```powershell
$total = (Get-ChildItem -Recurse -File -Filter *.py minibot | Get-Content | Measure-Object -Line).Lines; Write-Host "目前程式碼總行數: $total"
```

### 3.2 回報差異
任務完成後主動報告行數變化（例如：「本次更新後，總行數由 938 行增至 950 行」）。

若單次修改導致行數暴增（>100行），請主動說明原因，並確認是否符合「簡潔至上」的核心原則。
