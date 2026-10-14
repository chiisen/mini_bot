# CHANGELOG

本專案遵循 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) 格式。

## [Unreleased]

### 修正

- **Provider 建構時不再寫入全域環境變數**：`LiteLLMProvider.__init__` 過去會執行 `os.environ[env_key] = api_key` 與 `os.environ["MINIMAX_API_BASE"] = api_base`，造成兩個問題：

  1. **金鑰洩漏**——環境變數會被子行程繼承。實測建構一個 OpenRouter provider 後，任意子行程都能用 `os.environ["OPENROUTER_API_KEY"]` 讀到完整金鑰。
  2. **跨 provider 污染**——`api_base` 的變數名是硬編碼的 `MINIMAX_API_BASE`，與實際 provider 無關。實測建構 OpenRouter provider 後，OpenRouter 的端點被寫進 `MINIMAX_API_BASE`；而 `config/schema.py` 的 `load_from_env()` 正好以 `MINIMAX_API_BASE` 作為 MiniMax `apiBase` 的後備值，導致 MiniMax 請求被導向其他 provider 的端點。

  憑證改為一律經由 `chat()` 的 `api_key` / `api_base` 參數傳給 LiteLLM（原本就已如此傳遞，環境變數寫入純屬多餘）。`env_key` 參數保留以相容既有呼叫端，實際不再產生任何環境變數。(Fixes #3)

- **`.opencode/` 本機檔案改由根目錄 `.gitignore` 宣告**：`.opencode/.gitignore` 的內容包含 `.gitignore` 自身，導致它自己被忽略、永遠無法進版控（`git ls-files .opencode/` 僅追蹤 `rules/` 下的兩個檔案）。因此修正只在本機生效，其他克隆者仍會看到 `package-lock.json` 出現在 `git status`。改為在**受版控的根目錄 `.gitignore`** 統一宣告 `.opencode/` 的本機產物。(Fixes #18)

- **README 專案結構連結失效**：連結指向 `MINIBOT_MINIMAL_VERSION.md`，但該檔實際位於 `docs/`，根目錄無此檔。補上 `docs/` 前綴。(Fixes #19)

- **`validate_path()` 以行程 CWD 解析相對路徑**：相對路徑原本以行程 CWD 解析，邊界卻用 workspace 檢查。由於 Agent 啟動時 CWD 通常是專案目錄而非 workspace，模型照著 system prompt 宣告的「Working directory 就是 workspace」使用相對路徑時，一律被拒絕。改為以 workspace 為基準解析相對路徑。(Fixes #15)

- **沙盒邊界可被同名前綴的兄弟目錄繞過**：邊界檢查使用 `str(p).startswith(str(ws))`，會把 `/x/ws_secret` 誤判為 `/x/ws` 的一部分。改用 `Path.is_relative_to()`。(Why: 這是實際的沙盒逃逸——`workspace=/x/ws` 時可讀取 `/x/ws_secret/evil.txt`)

- **`_load()` 未還原 `updated_at`，24 小時 Session 逾時跨行程失效**：載入時只還原 `created_at` 而漏了 `updated_at`，導致 Session 一律以「載入當下」為更新時間。重啟後所有舊 session 都被視為剛更新過，`SESSION_TIMEOUT` 只在單一行程的記憶體快取存活期間有效。已還原該欄位，並確認舊格式（無 `updated_at`）與損壞值皆能安全載入。(Fixes #16)

> **行為變更**：修正後，超過 24 小時的舊對話會在重啟後被判定為過期並清空。這是 `SESSION_TIMEOUT` 宣告語意的實際生效。

- **`minibot config-show` 有三個欄位永遠顯示 `None`**：`config-show` 以 `model_dump(by_alias=True)` 輸出（camelCase），卻以 snake_case 取值，導致 `max_tool_iterations`、`max_tokens`、`memory_window` 恆為 `None`——正是使用者最需要檢查的執行參數。改為以 alias 鍵名（`maxToolIterations`／`maxTokens`／`memoryWindow`）取用。(Why: 靜默顯示 None 比不顯示更糟，使用者會誤以為設定未生效)

- **注入過濾誤傷以 system/admin/root 開頭的正常輸入**：`INJECTION_PATTERNS` 的 `^\s*(system|admin|root)` 只檢查字串開頭，不看後續內容，導致 `root cause analysis`、`admin panel`、`systemd service`、`rootkit detection` 等技術問答中極常見的詞語被剝掉關鍵詞，Agent 收到殘缺句子且使用者不知情。改為要求冒號分隔（`^\s*(system|admin|root)\s*:`），保留角色冒充的結構性特徵。(Why: 角色冒充的特徵是「角色: 內容」，而非單純以字根開頭；實測修正後攻擊樣本 0 漏網、正常輸入 0 誤傷)

> **注意**：`admin:` 與 `root:` 的攔截**完全依賴此條** —— 第 6 條 `system\s*:\s*` 僅涵蓋 system。已加註解說明並補上回歸測試鎖定此相依性。

- **`list_sessions()` 在 metadata 缺 `updated_at` 時整批崩潰**：`sorted()` 的鍵值取用 `.get("updated_at", "")`，但此處要處理的是「鍵存在、值為 `None`」而非「鍵不存在」，因此預設值無效，`None` 與 `str` 比較會拋 `TypeError`。單一筆異常 session 檔就會讓整個列表失敗。改為 `.get("updated_at") or ""`，並讓缺時間戳的 session 排在最後。(Why: 修正 #17；`list_sessions()` 目前雖無呼叫點，但 #5 要實作的 Session 管理指令必然會用到)

- **ShellTool 無法執行帶參數的指令**：`execute()` 的子命令檢查把「第一個參數」當成「子命令」，而 `ALLOWED_COMMANDS` 對單字指令（`ls`、`cat`…）只把指令名放進自己的清單，導致 `ls -la`、`cat <file>`、`echo hi`、`mkdir <dir>` 一律被拒——Unix 上幾乎只能執行無參數指令（`git` 因清單恰好包含子命令名而僥倖可用）。改為明確定義參數政策：
  - 唯讀指令（`ls`／`cat`／`head`／`tail`／`grep`／`pwd`／`echo`／`mkdir`／`touch`）允許任意參數
  - 具副作用指令只開放列舉的子命令：`git` 僅 `status`／`log`／`diff`／`branch`；`pip`、`curl`、`npm`、`node`、`python` 僅查詢類（`--version`／`list`）
  - `find`（`-delete`／`-exec` 可破壞檔案）與 `cd` 移出白名單
  - 授權判斷集中於 `_is_command_allowed()`，`execute()` 不再重複檢查，避免兩處邏輯漂移
  - 同時把檔案工具的敏感路徑黑名單套用到 shell 參數，補上 `cat /etc/passwd` 可繞過檔案層防護的缺口
  (Why: 修正後仍維持 fail-closed——未列舉的子命令一律拒絕，實測 `git reset --hard`、`git push --force`、`find . -delete`、`pip install <任意套件>`、`cat /etc/passwd`、`curl <url>` 皆無法執行；後三者分別對應任意程式碼執行、敏感檔案洩漏與網路外傳)
- **測試**：`tests/test_tools_filesystem.py` 增補參數政策與端到端執行測試（測試數 86 → 120）；`filesystem.py` 覆蓋率 70% → 88%，整體 28% → 30%。

- **設定檔載入失敗不再靜默退回預設值**：`config/loader.py` 的 `load_config()` 過去在讀取／解析／驗證失敗時只印一行訊息，然後回傳 `Config()`，導致使用者自訂的 model、workspace、max_tokens 全部無聲消失。改為拋出新的 `ConfigError`（**檔案不存在仍回傳預設值**，行為不變）；同時擋掉 JSON 根值非 object 的情況（過去會在 `_decrypt_sensitive_fields` 爆 `AttributeError`）。(Why: 設定無聲遺失比啟動失敗更難察覺)
- **CLI 明確回報設定錯誤**：`cli/commands.py` 新增 `_config_or_exit()`，設定檔損壞時顯示含路徑的錯誤訊息並以 exit code 1 結束。啟動時的 i18n 初始化不因此中斷，且 `onboard` 等指令仍可執行——使用者不會被鎖在無法修復設定的狀態。
- **i18n 訊息語意修正**：`error.config_load_failed` 由「警告」改為「錯誤」並帶上設定檔路徑（en / zh_TW / zh_CN 三語系）。
- **測試覆蓋**：新增 `tests/test_loader.py`，涵蓋檔案不存在／正常載入／JSON 損壞／schema 不合法／根值非 object／錯誤訊息含路徑。（測試數 19 → 25）
- **`MINIBOT_LOCALE` 環境變數先前完全失效**：`Config.locale` 預設為 `"en"`，因其本身即為合法語系，`detect_locale()` 會直接採用而不去讀環境變數，導致 README 記載的 `export MINIBOT_LOCALE=zh_TW` 沒有任何效果。改為預設 `None` 代表「未指定」，使語系優先順序回歸 README 記載的 `config.json > 環境變數 > 預設`；設定檔明確指定時仍優先於環境變數。(Why: 文件記載的設定路徑實際不可用，與 config validator 的 TypeError 屬同一類問題)
- **測試**：新增 `tests/test_i18n.py`（10 個測試），涵蓋語系優先順序與 `locale` 預設值；`tests/conftest.py` 的環境隔離加入 `MINIBOT_LOCALE`。

- **Config 環境變數驗證器拋出 TypeError**：`config/schema.py` 的 `load_from_env` 先將 `ProviderConfig` / `TelegramConfig` 實例放入 dict，再對其做 item assignment，導致 `MINIMAX_API_KEY` 或 `TELEGRAM_BOT_TOKEN` 存在時 `Config()` 直接失敗（`TypeError: 'ProviderConfig' object does not support item assignment`）。改為純 dict 操作，並以 alias 鍵名（`apiKey` / `apiBase` / `botToken`）寫入——pydantic 在 alias 與欄位名同時存在時以 alias 為準，若寫入 snake_case 會讓 config 檔案的值反過來蓋掉環境變數。(Why: 修復 README 建議的環境變數設定路徑；該路徑失效時 `load_config()` 還會靜默退回預設值，連帶丟棄使用者的整份設定)
- **測試環境隔離**：`tests/conftest.py` 新增 autouse fixture，清除 config 相關環境變數，避免斷言結果取決於開發機環境。(Why: 這正是上述 bug 能長期潛伏的原因——未設定金鑰的環境完全看不到問題)
- **測試覆蓋**：`tests/test_config.py` 新增 `TestEnvOverrides`，涵蓋環境變數存在/不存在、以及環境變數覆蓋 config 檔值的雙路徑。（測試數 12 → 19）
- **測試**：`tests/test_litellm_provider.py` 的 `TestGlobalSideEffect` 原本固化缺陷行為（「環境變數寫入的後果——已記錄但尚未修正」），改為 `TestNoEnvPollution`，斷言翻轉為「建構 provider 不寫入任何環境變數」；另新增 `test_credentials_still_reach_litellm`，確保移除環境變數寫入後憑證仍正確傳給 LiteLLM。（測試數 23 → 23，覆蓋率維持 100%）

### 修改

- **移除 `TODO.md`**：該檔原本被當作待辦的終點，但依 `AI_GITHUB_PROJECT_GUIDE.md` §1，待辦應建立為 GitHub Issues 以利追蹤。內容已全部遷移後刪除：
  - 相對路徑解析缺陷 → [#15](https://github.com/chiisen/mini_bot/issues/15)
  - Session 逾時跨行程失效 → [#16](https://github.com/chiisen/mini_bot/issues/16)
  - `list_sessions()` 崩潰 → [#17](https://github.com/chiisen/mini_bot/issues/17)
  - 覆蓋率缺口與 CI 的驗證天花板 → [#1](https://github.com/chiisen/mini_bot/issues/1) 評論
  - `.opencode/package-lock.json` 未忽略 → [#18](https://github.com/chiisen/mini_bot/issues/18)
  - README 失效連結 → [#19](https://github.com/chiisen/mini_bot/issues/19)

- 將 `config.json.example` 更名為 `config.example.json`，並同步更新 README。
- **文件與安裝優化**：更新 README.md 快速開始指令，修正 macOS/Unix 環境下的 `cp` 指令、路徑斜線問題，並建議使用 `python3 -m pip install -e .` 確保安裝正確。 (Why: 解決 macOS 使用者在初始化過程中的指令報錯)
- **README 視覺優化**：為 README.md 增加情緒符號 (Emojis)，提升閱讀體驗與親和力。(Why: 讓文件更生動)
- **README Windows 支援**：新增 Windows 環境準備說明（PowerShell 7）、Windows 安裝指令（PowerShell 指令），並更新功能總覽表格（移除已實現的 Shell 工具）。(Why: 完善跨平台文件)

### 新增

- **測試：CLI 指令**：新增 `tests/test_cli_commands.py`（23 個測試），以 `typer.testing.CliRunner` 驗證 `onboard`（建立設定／workspace、冪等、不覆寫既有檔案）、`status` 與 `config-show`（含 API Key 遮罩）、設定損壞時明確報錯且 exit 1、`onboard` 在設定損壞時仍可執行、`agent`／`telegram` 在設定不足時正確拒絕，以及 `_make_provider()` 的 provider 優先序與 fallback。`cli/commands.py` 覆蓋率 0% → 70%，整體 70% → 84%。（測試數 231 → 254）(Why: CLI 是使用者唯一的操作介面，且是設定載入錯誤的呈現層；測試以 `isolated_home` fixture 隔離 HOME，避免讀寫真實設定)

- **測試：LiteLLM Provider**：新增 `tests/test_litellm_provider.py`（22 個測試），以 monkeypatch 攔截 `litellm.acompletion`（**不發出任何網路請求**），涵蓋模型前綴套用（不重複加前綴、None 回退實例預設）、參數組裝（`api_base`／`tools`／`tool_choice` 條件性省略）、tool call 正規化（dict 與不合法 JSON 字串皆經 `json_repair`）、`finish_reason` 與 `usage` 映射、例外不外洩且回傳可讀訊息。`providers/litellm_provider.py` 覆蓋率 0% → 100%，整體 65% → 70%。（測試數 209 → 231）(Why: 這是唯一對外呼叫 LLM 的邊界，出錯會直接影響所有對話；且錯誤處理與參數組裝是靜默失效的高風險區)

- **測試：Agent 迭代迴圈**：新增 `tests/test_agent_loop.py`（21 個測試），以 mock provider 驗證 LLM ↔ Tool 迭代流程：無 tool call 直接回覆、tool 結果的 role／`tool_call_id`／`name` 結構、assistant `tool_calls` 的 JSON 字串序列化、多個 tool call 的執行順序、`max_iterations` 硬性上限、工具輸出截斷至 10000 字元、`on_progress` 回呼、session 持久化與 `memory_window`，以及 Bus 消費模式（`run()`）的 outbound 發佈、session 分流與單則失敗不中斷迴圈。`agent/loop.py` 與 `bus/` 全數 0% → 100%，整體 49% → 65%。（測試數 188 → 209）(Why: `agent/loop.py` 是 Agent 的 orchestrator；Bus 消費模式雖無 channel 使用，仍屬公開 API，日後接線時才發現問題代價較高)

- **測試：session 持久化與隔離**：新增 `tests/test_session_manager.py`（23 個測試），涵蓋訊息往返、tool metadata（`tool_calls`／`tool_call_id`／`name`）保留、owner 隔離（不符者取得空 session 且不污染快取）、快取過期清除、legacy 路徑遷移，以及損壞與空行檔案的健壯性。`session/manager.py` 覆蓋率 0% → 100%，整體 30% → 43%。（測試數 120 → 143）(Why: 此模組負責對話歷史的持久化與使用者隔離，出錯會直接遺失對話或造成跨使用者洩漏)

- **CI（GitHub Actions）**：新增 `.github/workflows/ci.yml`，在 `push` 到 `main` 與 `pull_request` 時安裝 `.[test]` 並執行 `pytest`（附覆蓋率報告），Python 3.11。(Why: 專案先前沒有任何自動化關卡，而這幾天發現的缺陷恰好都藏在測試未覆蓋的路徑上；開發策略改為直接在 `main` 開發後，CI 成為唯一能自動攔截退化風險的機制。**注意其天花板**：CI 沒有 secrets，因此「設定齊備」的路徑仍驗證不到)

- **測試：沙盒與 Shell 白名單**：新增 `tests/test_tools_filesystem.py`（51 個測試），涵蓋 `validate_path()` 的 workspace 邊界與敏感路徑黑名單、Shell 指令白名單與危險字元（串接、重導向）、以及 `read_file` / `write_file` / `list_dir` 的存取控制。`agent/tools/filesystem.py` 覆蓋率 0% → 70%，整體 17% → 28%。（測試數 35 → 86）(Why: 此模組是 Agent 的安全邊界卻完全沒有測試，且不需要 API key 或網路即可驗證)

- **待辦索引 `TODO.md`**：集中記錄專案目前的待辦優先序與已知缺口，並指向對應的 GitHub Issue。內容包含排序理由（例如為何應優先補「設定齊備路徑」的測試）、CI 的驗證天花板，以及尚未開成 issue 的事項（`.opencode/package-lock.json` 未忽略、README 失效連結）。(Why: 待辦散落在 issue 與對話中，缺少一份可快速掌握現況與順序的索引)

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
