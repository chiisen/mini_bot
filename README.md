# mini_bot

最小可執行的 Personal AI Agent，基於 LiteLLM 支援多種 LLM Provider，預設使用 **MiniMax M2.5**。

## 特色

- 🤖 CLI 互動聊天（`minibot agent`）
- 📱 Telegram Bot（`minibot telegram`）
- 💾 Session 持久化（JSONL 格式）
- 🧠 長期記憶（MEMORY.md）
- 🔧 基本工具：讀/寫/列目錄
- ⚡ 非同步 Agent Loop

## 快速開始

```powershell
# 安裝
pip install -e .

# 複製設定範本到家目錄（Windows PowerShell）
copy config.json.example ~\.minibot\config.json

# 初始化 workspace
minibot onboard

# 編輯 API Key（用下方範例填入你的 MiniMax API Key）
notepad ~\.minibot\config.json

# 複製 workspace 範本（第一次使用）
copy workspace\AGENTS.md.example workspace\AGENTS.md
copy workspace\memory\MEMORY.md.example workspace\memory\MEMORY.md

# 開始聊天
minibot agent
minibot agent -m "Hello!"

# 查看狀態
minibot status
```

## 設定說明（`~/.minibot/config.json`）

只需修改 `apiKey` 一個欄位，其餘保留預設即可：

```json
{
  "providers": {
    "minimax": {
      "apiKey": "← 替換成你的 MiniMax API Key",
      "apiBase": "https://api.minimax.io/v1"  ← 保留不變
    }
  },
  "channels": {
    "telegram": {
      "botToken": "← 填入 Telegram Bot Token（可選）"
    }
  },
  "agents": {
    "defaults": {
      "model": "minimax/MiniMax-M2.5"         ← 保留不變
    }
  }
}
```

> - MiniMax API Key 可至 [minimax.io](https://www.minimax.io/) 申請取得。
> - Telegram Bot Token 可透過 [@BotFather](https://t.me/BotFather) 申請。

## Telegram Bot

設定好 `botToken` 後，執行：

```powershell
minibot telegram
```

在 Telegram 傳訊息給你的 Bot 即可開始聊天，每個使用者會有獨立的 Session 記憶。

## 功能總覽

### ✅ 已實現

| 功能 | 說明 |
|---|---|
| **CLI 互動聊天** | `minibot agent` 互動模式 / `minibot agent -m` 單次模式 |
| **Telegram Bot** | `minibot telegram` 啟動 Polling，每位使用者獨立 Session |
| **LLM 呼叫** | 透過 LiteLLM 支援 6 個 Provider，預設 MiniMax M2.5 |
| **Agent Loop** | LLM ↔ Tool 迭代迴圈（最多 20 輪） |
| **檔案工具** | `read_file`、`write_file`、`list_dir` |
| **Session 持久化** | JSONL 格式，自動載入歷史對話 |
| **記憶系統** | `MEMORY.md` 長期記憶 + `HISTORY.md` 歷史日誌 |
| **設定系統** | Pydantic schema，`~/.minibot/config.json`（repo 外，安全） |
| **狀態監控** | `minibot status` 顯示設定、Provider、Telegram 狀態 |

### 🔮 未來擴展

| 功能 | 說明 |
|---|---|
| Shell 工具 | `exec` — 讓 Agent 能執行系統指令 |
| Web 工具 | `web_search`、`web_fetch` — 讓 Agent 能上網搜尋 |
| 記憶自動整合 | 自動將對話摘要寫入 MEMORY.md |
| Cron 排程 | 定時任務 |
| MCP 支援 | 外部工具伺服器 |
| 更多頻道 | Discord、Slack 等 |

## 支援的 Provider（優先順序）

1. **MiniMax**（預設）
2. OpenRouter
3. Anthropic
4. OpenAI
5. DeepSeek
6. Gemini

## 專案結構

詳見 [MINIBOT_MINIMAL_VERSION.md](MINIBOT_MINIMAL_VERSION.md)

## 程式碼量統計 (極簡約定)

本專案旨在維持極簡的 Agent 核心框架。你隨時可以用以下 PowerShell 指令追蹤目前的 `minibot` 套件 `.py` 檔案總行數：

```powershell
# 統計總行數
(Get-ChildItem -Recurse -File -Filter *.py minibot | Get-Content | Measure-Object -Line).Lines

# 列出各檔案行數排行榜
Get-ChildItem -Recurse -File -Filter *.py minibot | Select-Object FullName | ForEach-Object { $lines = (Get-Content $_.FullName | Measure-Object -Line).Lines; [PSCustomObject]@{ File=$_.FullName.Replace((Get-Location).Path + "\", ""); Lines=$lines } } | Sort-Object Lines -Descending | Format-Table -AutoSize
```

> **截至 2026-02-21：核心程式碼約為 938 行**
