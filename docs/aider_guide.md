# 🤖 Aider 安裝與操作指南 (Windows)

[Aider](https://aider.chat/) 是一個強大的終端機 AI 結對程式碼開發助手 (Pair Programming in your Terminal)。它可以直接在你的專案目錄下運行，並擁有**直接編輯檔案**與**自動提交 Git Commit** 的超強能力，能極大提升 AI 輔助開發的成功率。

本文檔將介紹如何在 Windows (PowerShell) 環境下安裝與使用 Aider，並搭配你的專案一起工作。

---

## 1. 📥 安裝 Aider

在你的 terminal 內透過 pip 進行全域或專案層級的安裝：

```powershell
pip install aider-chat
```

安裝完成後，你可以透過以下指令確認版本：

```powershell
aider --version
```

---

## 2. 🔑 設定 API Key (以 OpenAI 或 Anthropic 為例)

Aider 需要一個強大的大語言模型 (例如 GPT-4o 或 Claude 3.5 Sonnet) 來確保它能正確修改程式碼架構。

在使用前，必須設定環境變數提供你的 API Key。  
在 **PowerShell 7** 中：

```powershell
# 如果使用 OpenAI 模型（預設）
$env:OPENAI_API_KEY="sk-你的_OpenAI_API_Key"

# 如果使用 Anthropic 模型（目前生成程式碼最強）
$env:ANTHROPIC_API_KEY="sk-ant-你的_Anthropic_API_Key"
```

> **💡 提示：永久設定環境變數**  
> 如果你不想每次開終端機都要重新設定，可以將上述指令加入你的 PowerShell Profile 中：
> 執行 `code $profile`，然後將環境變數寫入該檔案。

---

### 🦙 使用 Ollama (完全免費，無須 API Key)

如果你想使用本地部署的模型來替代付費 API（例如 `llama3`、`qwen2.5-coder` 等），你可以透過 Ollama 來運行 Aider：

1. **確保服務啟動**：確認 Ollama 正在背景運行，且我們已下載對應的模型（例如：`ollama pull llama3`）。
2. **啟動 Aider 時加上指定模型參數**：
   只要透過 `--model ollama/<模型名稱>` 的格式啟動，完全不需要設定環境變數或 API Key！

```powershell
# 語法：aider --model ollama/<模型名稱>
aider --model ollama/llama3

aider --model ollama/got-oss:20b0cloud
```

> **💡 提示：上下文長度 (Context Window)**  
> 預設 Ollama 的 Context 可能較小（2k），對於 Aider 在編輯多個檔案時可能會不夠用。啟動前建議暫時拉高 Ollama 允許的上下文大小：
>
> ```powershell
> $env:OLLAMA_NUM_CTX=8192
> ```

---

## 3. 🚀 開始結對程式設計 (Pair Programming)

進入你的專案目錄 (例如 `mini_bot`)，直接啟動 Aider：

```powershell
# 啟動 Aider 並指定你要修改的檔案
aider minibot/agent/loop.py
```

或者不帶參數直接啟動：

```powershell
aider
```

啟動後，你進入了 Aider 的互動式終端機。它會掃描整個專案（Repo Map）來建立對程式碼架構的認知。

---

## 4. 💬 常用指令與操作邏輯

在 Aider 介面中，直接打字就是對 AI 說話。但如果加上預設的斜線指令 (`/`)，可以執行各種強大的開發與系統控制。

### 檔案管理

- `/add <檔案>`：將檔案拉進這輪對話的 Context，讓 AI 參考或修改（例如 `/add README.md`）。
- `/drop <檔案>`：把檔案從這輪 Context 中移除，節省 Token。
- `/ls`：查看目前被加進 Context 的所有檔案列表。

### 🗺️ Codebase 索引與專案地圖 (Repo Map)

很多人會問：「要怎麼讓 Aider 掃描或索引我的 Codebase？」
答案是：**全自動的，不需要手動建立 Index！**

1. **背景分析**：只要你的目錄下有 `.git` (是一個 Git 儲存庫)，Aider 啟動時就會在背景自動使用 Tree-sitter 分析整個專案的結構、函數與依賴關係，並產生一份抽象的「專案地圖 (Repo Map)」。
2. **精準且節省 Token**：有了 Repo Map，Aider 就能看懂整個專案的架構，不需要你將所有檔案都 `/add` 進去，即可進行跨檔案的推論。
3. **`/map` 指令**：如果你想主動查看 Aider 所看到的 codebase 索引摘要（Repo Map 的內容），直接輸入 `/map` 即可顯示。

### 🎯 怎麼測試 Aider 有沒有發揮作用？

當 Aider 啟動並生成完 Repo Map 後，你可以用這幾個簡單的步驟來測試連接的模型是否夠聰明、以及工具是否正常運作：

1. **問一個全域性的問題（測試 Repo Map）**：
   直接在對話框輸入：「_這個專案的主要功能是什麼？核心依賴有哪些？_」或「_這支程式的進入點 (Entry point) 在哪個檔案？_」
   如果它回答得頭頭是道，恭喜！代表它成功看懂了你的專案架構。
2. **做一個無害的修改（測試編輯與 Git 功能）**：
   輸入：「_幫我在 README.md 的最下面加上一行『This project is awesome!』並且存檔。_」
   觀察 Aider 是否會：
   - 自動將 README.md 拉入 Context。
   - 產生修改並存檔。
   - 在背景幫你自動執行 `git commit`。
3. **測試時光倒流（測試安全網）**：
   接著輸入：**`/undo`**。
   你會發現剛剛對 README.md 的修改，連同 Git Commit 瞬間被撤銷，專案回到完美無缺的狀態！

---

### 程式碼生成與 Git 保護

當你說：「幫我把這裡的迴圈改成非同步」，Aider 會：

1. **自動幫你 Commit 目前的修改**（如果有的話），確保一個乾淨的還原點。
2. 呼叫大模型去重寫程式碼片段（Multi-file Edits）。
3. 生成完畢後，**再幫你自動 Commit 一次**（附上 AI 產生的 `feat: ... ` 訊息）。

如果它改壞了，或是你不滿意：

- **`/undo`**：一秒還原上一次 AI 做的任何修改，完美反悔。
- `/diff`：查看剛剛 AI 修改了哪些地方的差異。

### 測試、自修復與系統指令

- **`/run <指令>`**：直接在 Aider 裡執行指令（例如 `/run pytest`）。如果出錯，Aider 會自動讀取錯誤訊息並提出修正方案。
- `/clear`：清空歷史對話，開始一個全新的思路。
- `/exit` 或 `/quit`：離開 Aider 終端機。

---

## 5. 🤝 AI 代理人如何與 Aider 協作？

當你正在使用 AI 代理人（例如 Antigravity）進行開發時，你可以讓 AI 代理人與 Aider 配合，達成更強大的自動化開發流程：

### 模式 A：指令橋接 (Instruction Bridging)

你可以在對話中直接要求 AI 代理人：「_幫我寫一個 Aider 指令來完成這個重構。_」
AI 代理人會提供精確的指令供你複製到 Aider 終端機執行，例如：

> `/add minibot/utils.py`
> `將所有同步 I/O 改為使用 `plumbum` 的非同步操作，並確保錯誤處理符合 GEMINI.md 規範。`

### 模式 B：架構師與實作者 (Architect & Implementer)

這是目前最強大的協作方式：

1. **AI 代理人當「架構師」**：負責高層次的邏輯規劃、產出 `implementation_plan.md`。
2. **Aider 當「實作者」**：你將 AI 代理人的計畫直接貼給 Aider，並加上 `--architect` 參數啟動 Aider。
3. **優點**：AI 代理人負責思考複雜的跨檔案關聯，Aider 負責執行精準的程式碼層級替換 (Search/Replace) 與自動 Commit。

### 模式 C：自動化代理 (Auto-Execution)

如果環境允許，AI 代理人可以直接在後端透過 `run_command` 調用 Aider：

```powershell
aider --message "修正所有 lint 錯誤" --yes
```

這可以讓 AI 代理人在發現測試失敗時，自動呼叫 Aider 進行「一鍵自修復」。

---

## 6. 💡 Aider 為什麼對寫程式超級有幫助？

1. **Repo Map 全域護城河**：它不會只看你加進去的單一檔案，它會透過抽象語法樹 (AST) 掃描專案，不會發生「改了 A 卻讓沒看到的 B 死掉」的悲劇。
2. **搜尋取代模式 (Search/Replace)**：LLM 不需要重寫全檔，而是精準取代目標程式碼區塊，減少格式跑版，速度飛快。
3. **無壓力的 Git 支援 (Undo)**：因為每次修改前都有 Commit 保護，你可以放心地盡情讓 AI 嘗試重構或加入新功能！
