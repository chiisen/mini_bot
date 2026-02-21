# CHANGELOG

本專案遵循 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) 格式。

## [Unreleased]

### 新增

- **全域規範與相容性**：建立 `GEMINI.md` 與 `.opencode/rules/AGENTS.md`。(Why: 確保遵循全域 AI Agent 指南，並保持與 Opencode 環境的相容性，同時設定千行代碼的收尾檢查機制)
- **Telegram 頻道**：`minibot telegram` 指令啟動 Telegram Bot（Polling 模式）。(Why: 將對話介面延展至通訊軟體，讓助理隨時可用)
- 每個 Telegram 使用者獨立 Session（key = `tg:user_{user_id}`）
- 支援 `/start` 歡迎訊息、長訊息自動分段回傳
- Config schema 新增 `channels.telegram.botToken` 欄位
- `minibot status` 顯示 Telegram 設定狀態
- 依賴新增 `python-telegram-bot>=22.0,<23.0`

## [0.1.0] - 2026-02-21

### 新增

- 初始建立 `minibot` 套件，基於 MINIBOT_MINIMAL_VERSION.md 規格
- **CLI 指令**：`minibot agent`（互動聊天）、`minibot onboard`（初始化）、`minibot status`（狀態查看）
- **Bus 層**：非同步訊息匯流排（`InboundMessage`、`OutboundMessage`、`MessageBus`）
- **Config 層**：Pydantic 設定 schema，預設模型為 `minimax/MiniMax-M2.5`
- **Session 層**：JSONL 格式 session 持久化（含 metadata + 對話記錄）
- **Provider 層**：LiteLLM-based Provider，MiniMax M2.5 優先（支援 openrouter、anthropic、openai、deepseek、gemini）
- **Agent 層**：最小 AgentLoop（LLM ↔ Tool 迭代迴圈、process\_direct、`MemoryStore`、`ContextBuilder`）
- **Tools 層**：`read_file`、`write_file`、`list_dir` 三個基本工具
- **記憶系統**：雙層記憶（`MEMORY.md` 長期事實 + `HISTORY.md` 歷史日誌）
- Workspace 初始檔案：`AGENTS.md`、`MEMORY.md`
- 規格文件：`MINIBOT_MINIMAL_VERSION.md`

### 說明

- 資料目錄：`~/.minibot`（config.json、workspace、sessions）
- 套件名稱：`minibot`，CLI 指令：`minibot`
- 參考原始極簡架構，完整移植並建立為 mini_bot 專案
