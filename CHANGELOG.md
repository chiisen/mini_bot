# CHANGELOG

本專案遵循 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) 格式。

## [Unreleased]

### 修正

- **Config 環境變數驗證器拋出 TypeError**：`config/schema.py` 的 `load_from_env` 先將 `ProviderConfig` / `TelegramConfig` 實例放入 dict，再對其做 item assignment，導致 `MINIMAX_API_KEY` 或 `TELEGRAM_BOT_TOKEN` 存在時 `Config()` 直接失敗（`TypeError: 'ProviderConfig' object does not support item assignment`）。改為純 dict 操作，並以 alias 鍵名（`apiKey` / `apiBase` / `botToken`）寫入——pydantic 在 alias 與欄位名同時存在時以 alias 為準，若寫入 snake_case 會讓 config 檔案的值反過來蓋掉環境變數。(Why: 修復 README 建議的環境變數設定路徑；該路徑失效時 `load_config()` 還會靜默退回預設值，連帶丟棄使用者的整份設定)
- **測試環境隔離**：`tests/conftest.py` 新增 autouse fixture，清除 config 相關環境變數，避免斷言結果取決於開發機環境。(Why: 這正是上述 bug 能長期潛伏的原因——未設定金鑰的環境完全看不到問題)
- **測試覆蓋**：`tests/test_config.py` 新增 `TestEnvOverrides`，涵蓋環境變數存在/不存在、以及環境變數覆蓋 config 檔值的雙路徑。（測試數 12 → 19）

### 修改

- 將 `config.json.example` 更名為 `config.example.json`，並同步更新 README。
- **文件與安裝優化**：更新 README.md 快速開始指令，修正 macOS/Unix 環境下的 `cp` 指令、路徑斜線問題，並建議使用 `python3 -m pip install -e .` 確保安裝正確。 (Why: 解決 macOS 使用者在初始化過程中的指令報錯)
- **README 視覺優化**：為 README.md 增加情緒符號 (Emojis)，提升閱讀體驗與親和力。(Why: 讓文件更生動)
- **README Windows 支援**：新增 Windows 環境準備說明（PowerShell 7）、Windows 安裝指令（PowerShell 指令），並更新功能總覽表格（移除已實現的 Shell 工具）。(Why: 完善跨平台文件)

### 新增

- **開發者工具文件**：新增 `docs/grepai_guide.md` 與 `docs/aider_guide.md`，提供 grepai (語意搜尋工具) 與 Aider (AI 結對程式設計助手) 的完整 Windows 安裝與操作指南。(Why: 幫助開發者快速上手並極大化 AI 輔助寫碼成功率與效率)

- **OS 特定規範**：建立 `Windows/GEMINI.md` 與 `macOS/GEMINI.md`，遵循 GEMINI.md 中的規則導航規範。(Why: 將作業系統特定的開發規則集中管理)
- **Shell 工具**：`shell` 指令，讓 Agent 能執行系統指令。(Why: 擴展 Agent 能力範圍，使其能執行系統操作)
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
