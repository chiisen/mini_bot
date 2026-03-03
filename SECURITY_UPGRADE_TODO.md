# 安全升級待辦事項 (Security Upgrade To-Do)

## 專案安全分析

本專案基於 OpenClaw 架構，發現以下安全問題需要修復：

| 優先級 | 問題類型 | 嚴重程度 | 位置 |
|--------|----------|----------|------|
| P0 | Shell 任意執行 | 🔴 嚴重 | `minibot/agent/tools/filesystem.py:21-47` |
| P0 | 路徑遍歷 | 🔴 嚴重 | `minibot/agent/tools/filesystem.py:61-108` |
| P1 | API Key 明文存儲 | 🟠 高 | `minibot/config/loader.py`, `config.example.json` |
| P1 | 依賴庫漏洞 | 🟠 高 | `pyproject.toml` |
| P2 | 對話注入 | 🟡 中 | `minibot/agent/loop.py:104-122` |
| P2 | 會話隔離 | 🟡 中 | `minibot/session/manager.py` |
| P3 | 錯誤訊息洩露 | 🟢 低 | 多處 |

---

## 待辦清單

### P0 - 緊急修復

#### T1. Shell Tool 任意執行漏洞 (CRITICAL)

**問題**：使用者可透過 Agent 執行任意 shell 命令，導致遠端程式碼執行 (RCE)

**位置**：`minibot/agent/tools/filesystem.py:21-47`

**修復方向**：
- [ ] 實作允許列表 (whitelist) 机制，只允許執行預定義的安全命令
- [ ] 移除 `shell=True` 参数，改用命令列表
- [ ] 添加執行逾時和資源限制
- [ ] 考慮完全移除 ShellTool 或設為可選功能

**驗證方法**：
```python
# 修復後應該拒絕危險命令
tool = ShellTool()
result = await tool.execute("rm -rf /")  # 應回傳錯誤
result = await tool.execute("cat /etc/passwd")  # 應回傳錯誤
```

---

#### T2. 路徑遍歷漏洞 (CRITICAL)

**問題**：檔案工具可存取任意路徑，攻擊者可讀取系統敏感檔案

**位置**：`minibot/agent/tools/filesystem.py:61-108`

**修復方向**：
- [ ] 實作 workspace 目錄限制，所有檔案操作必須在 workspace 內
- [ ] 添加路徑驗證，阻止 `../` 路徑穿越
- [ ] 實作黑名單機制，阻止存取系統敏感檔案（如 `/etc/passwd`, `~/.ssh/` 等）
- [ ] 限制檔案大小，防止記憶體耗盡

**驗證方法**：
```python
# 修復後應該拒絕 workspace 外存取
tool = ReadFileTool()
result = await tool.execute("/etc/passwd")  # 應回傳錯誤
result = await tool.execute("~/.ssh/id_rsa")  # 應回傳錯誤
```

---

### P1 - 高優先級

#### T3. API Key 安全存儲

**問題**：API keys 明文存儲於 `config.json`，缺乏加密

**位置**：
- `minibot/config/loader.py`
- `config.example.json`

**修復方向**：
- [ ] 使用環境變數作為主要 API Key 來源（優先於 config）
- [ ] 實作 config 加密機制（如使用 `cryptography` 庫）
- [ ] 在 config 加載時使用 pydantic validator 驗證
- [ ] 從環境變數讀取敏感資訊時提供提示

**驗證方法**：
```bash
# 優先使用環境變數
export MINIMAX_API_KEY="your-key-here"
export TELEGRAM_BOT_TOKEN="your-token-here"
minibot agent
```

---

#### T4. 依賴庫安全性審查

**問題**：需檢查使用依賴庫的已知漏洞

**位置**：`pyproject.toml`

**修復方向**：
- [ ] 執行 `pip-audit` 或 `safety` 檢查已知漏洞
- [ ] 更新有漏洞的依賴庫版本
- [ ] 記錄依賴庫安全審查結果
- [ ] 設定依賴庫版本上限 (>=x.y.z, <最新穩定版)

**驗證命令**：
```bash
pip-audit
# 或
safety check
```

---

### P2 - 中優先級

#### T5. 對話注入防護

**問題**：用戶輸入未經清理，可能導致 prompt injection

**位置**：`minibot/agent/loop.py:104-122`

**修復方向**：
- [ ] 實作輸入過濾，移除或轉義特殊字符
- [ ] 考慮使用分隔符隔離用戶輸入
- [ ] 記錄並監控異常注入模式

---

#### T6. 會話隔離與存取控制

**問題**：缺乏會話隔離，無法限制用戶只能存取自己的會話

**位置**：`minibot/session/manager.py`

**修復方向**：
- [ ] 實作用戶 ID 驗證
- [ ] 實作會話存取權限控制
- [ ] 添加會話逾時機制

---

### P3 - 低優先級

#### T7. 錯誤訊息脫敏

**問題**：錯誤訊息可能洩露內部資訊

**修復方向**：
- [ ] 檢視所有錯誤訊息，確保不洩露敏感資訊
- [ ] 實作統一的錯誤處理機制

---

## 實作順序建議

1. **第一階段**（立即）：修復 T1, T2 - 嚴重漏洞
2. **第二階段**：修復 T3, T4 - 高風險問題
3. **第三階段**：修復 T5, T6 - 中風險問題
4. **第四階段**：修復 T3 - 低風險優化

---

## 驗證清單

完成每個任務後，請勾選：

- [ ] T1: Shell Tool 安全性修復
- [ ] T2: 路徑遍歷漏洞修復
- [ ] T3: API Key 安全存儲
- [ ] T4: 依賴庫安全性審查
- [ ] T5: 對話注入防護
- [ ] T6: 會話隔離與存取控制
- [ ] T7: 錯誤訊息脫敏

---

## 相關檔案

- `minibot/agent/tools/filesystem.py` - 檔案工具實作
- `minibot/config/loader.py` - 配置載入
- `minibot/config/schema.py` - 配置結構定義
- `minibot/agent/loop.py` - Agent 迴圈邏輯
- `minibot/session/manager.py` - 會話管理
- `pyproject.toml` - 專案依賴
