# TODO — 待辦與已知缺口

> 本檔是待辦事項的**優先序索引**。細節以 GitHub Issue 為準，此處只記錄排序理由，以及尚未開成 issue 的項目。
>
> 最後更新：2026-10-14

## 目前狀態

| 指標 | 值 |
|---|---|
| 測試 | 120 passed（`python -m pytest tests/ -q`） |
| 覆蓋率 | 30%（`--cov=minibot`） |
| 套件行數 | 1,677（`GEMINI.md` §1「千行約定」目標約 1,000） |
| CI | ✅ `.github/workflows/ci.yml`（push / PR 時執行 pytest） |
| 開啟的 issue | 7 |

## 待辦（依建議順序）

### 1. 補其餘核心模組的測試 — [#1](https://github.com/chiisen/mini_bot/issues/1)

`config/`、`i18n/`、`loader` 與 `agent/tools/filesystem.py` 已有測試（整體 30%），其餘仍為 **0%**：

| 模組 | 行數 | 覆蓋率 |
|---|---|---|
| `cli/commands.py` | 159 | 0% |
| `session/manager.py` | 118 | 0% |
| `agent/loop.py` | 69 | 0% |
| `providers/litellm_provider.py` | 47 | 0% |
| `channels/telegram.py` | 39 | 0% |

> **優先原則**：優先覆蓋「**預設環境不會執行到的分支**」，而非單純衝高行數。
> PR [#10](https://github.com/chiisen/mini_bot/pull/10) 之前，`Config` 的 env validator 在 `MINIMAX_API_KEY` 存在時會拋 `TypeError`，卻躲過了數個 commit —— 因為未設定金鑰的開發機**永遠跑不到那條路徑**。

### 2. `validate_path()` 以行程 CWD 解析相對路徑 — 尚未開 issue

`validate_path()` 使用 `Path(path).resolve()`，相對路徑會以**行程 CWD** 解析，但沙盒邊界是 workspace。而 `ContextBuilder` 的 system prompt 告訴模型「Working directory 就是 workspace」，模型照著用相對路徑的結果是一律被拒：

```
system prompt:  Working directory: <workspace>
實際 CWD:       <專案目錄>
read_file("AGENTS.md") -> Error: Access denied. Path must be within workspace
```

**影響**：所有檔案工具都必須使用絕對路徑才動得了，與 prompt 宣告不符。
**需要決定**：相對路徑應以 workspace 還是 CWD 為基準？這是語意決策而非單純修正。

> CI 已建立（見上方狀態表），但其天花板不變：沒有 secrets，驗證不到「設定齊備」的路徑。**CI 是回歸防護，不是這類 bug 的偵測器。**

### 3. Windows ShellTool 無法運作 — [#9](https://github.com/chiisen/mini_bot/issues/9)

`local(cmd, args, shell=False)` 是無效的 plumbum API 呼叫，在整個支援版本範圍（`>=1.8.0,<2.0.0`）皆拋 `TypeError`，被外層 `except` 吞掉後回傳錯誤字串 —— Windows 上 ShellTool 完全不可用。

> **驗證侷限**：已在 macOS 實測該 API 無效（`LocalMachine` 不可呼叫與 OS 無關），但**未在實際 Windows 端到端驗證**。

### 4. 安全缺口 — [#4](https://github.com/chiisen/mini_bot/issues/4)、[#3](https://github.com/chiisen/mini_bot/issues/3)

- **#4**：`sanitize_input()` 只作用於 user 訊息，**工具結果未經過濾** —— 透過 `read_file` 讀入的檔案內容可繞過檢查
- **#3**：`litellm_provider` 將 API Key 寫入 `os.environ`，造成全域狀態污染

### 5. 功能完善 — [#5](https://github.com/chiisen/mini_bot/issues/5)、[#6](https://github.com/chiisen/mini_bot/issues/6)

- **#5**：Session 過期清理與管理指令
- **#6**：`MEMORY.md` 自動整合 —— `write_long_term()` / `append_history()` 目前是**死碼**（零呼叫點），API 已就緒、只缺觸發時機

### 最後考慮 / 建議關閉 — [#2](https://github.com/chiisen/mini_bot/issues/2)

Telegram channel 未走 `MessageBus`。接線會增加程式碼卻沒有使用者可見價值，且專案已超出自身的行數目標（1,677 / ~1,000），建議**收斂而非擴建**。

## 其他已知事項

### `.opencode/package-lock.json` 反覆出現為未追蹤檔案

`.opencode/.gitignore` 只忽略 `node_modules`，未忽略 `package-lock.json`，因此 opencode 背景程序產生該檔後，會持續在 `git status` 中干擾。

修法：在 `.opencode/.gitignore` 補一行 `package-lock.json`。

> **不要提交該檔案** —— 它屬於工具產物，不是專案內容。

### README 的文件索引有失效連結

README 的「專案結構」段落指向 `MINIBOT_MINIMAL_VERSION.md`，但該檔不存在（實際位於 `docs/MINIBOT_MINIMAL_VERSION.md`）。屬獨立的小修正，尚未處理。
