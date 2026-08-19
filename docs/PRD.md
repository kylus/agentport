# PRD：Topic Expert Agent

## 1. 產品定位

為每一個值得長期累積知識的 topic 建立一個專屬 agent。

agent 吸收該 topic 相關的來源——SCM repo（GitHub / GitLab 的 issue、MR/PR、wiki）、Google Docs、本地檔案、Claude Code session 紀錄,以及已啟用聊天頻道（Discord / Slack）的討論——整理成可追溯來源、可版控、可持續更新的「目前理解」,並在被 tag 時回答問題、整理會議、整理討論、更新理解。

每個 agent 只對應一個 topic。Topic 的粒度至少是一個產品、一個專案、一個客戶、一條長期工作線,不容許太小的 topic(例如單一技術元件、單一 bug)。

## 2. Agent 的存在形式

- 一個 topic 對應一個 agent
- 同一個 agent 可被 tag 在任何已啟用頻道（Discord / Slack）的 channel 或 DM——頻道是對稱的 opt-in adapter,每個 topic 至少啟用一個
- 在哪裡被 tag,就在哪裡回應
- tag 哪個 agent,就決定了 topic,使用者不需要再額外指定主題
- 同一個 channel 可被多個 topic 的 agent 出現,各自只看自己被 tag 的訊息

Topic 的命名空間以 SCM repo（GitHub 或 GitLab）為主要對應單位。沒有 repo 的長期工作線(例如客戶關係、合規專案、採購案)也可以建立 topic,屬於 non-repo topic。

## 3. 使用限制

### 3.1 只在被 tag 時啟動

agent 不主動監聽訊息,不主動回覆未被 tag 的對話。

### 3.2 任務範圍限定

agent 只處理與該 topic 知識整理、問答、記憶、理解更新相關的任務。

被要求做超出範圍的事情時,agent 拒絕並引導使用者回到 topic expert 的範圍。

### 3.3 區分硬拒絕與軟拒絕

- 硬拒絕:任務與 topic expert 完全無關(寫歌、訂餐、查股票、產生與 topic 無關的程式碼),agent 直接擋下
- 軟拒絕:任務在範圍內但 agent 沒有相關記憶,agent 應誠實說明沒有記憶,並引導使用者指出相關來源

## 4. 使用者角色與權限

### 4.1 Owner

每個 topic 有一位 owner,負責該 topic 的記憶品質。

Owner 對該 topic 擁有正式記憶的寫入權限,以及 approve 他人提案的權限。

### 4.2 Contributor

所有可看到該 agent 被 tag 的使用者皆為 contributor。

Contributor 可以:
- 查詢
- 整理頻道 thread
- 要求整理外部來源
- 提交記憶更新提案

Contributor 的寫入不會直接成為正式記憶,需 owner approve。

### 4.3 衝突解決原則

正式記憶的衝突解決原則為 owner-wins:owner 寫入的版本就是正式版,contributor 的寫入一律進入待 approve 狀態。

## 5. 記憶結構

### 5.1 每個 topic 的記憶分為七個 section

| Section | 內容 |
|---|---|
| Background | topic 的脈絡、為什麼存在、相關背景、領域術語 glossary |
| Current Understanding | 目前的共識與結論 |
| Decisions | 已拍板的決策 |
| Open Questions | 還沒解決的問題、已識別的風險 |
| Commitments | 對外的承諾(對客戶、對主管、跨部門) |
| People | 利害關係人、RACI、專長、聯絡偏好 |
| Corrections | 被使用者糾正過的錯誤紀錄（PRD §6.9）。下次回答前 grep 防再犯。 |

People 早期被合進 Background 的 stakeholders 段，但實務上「人」的查詢頻率高（誰負責 X、Y 的專長是什麼、要跟誰確認），獨立 section 比 grep 更直接。

### 5.2 每個 section 獨立版控

每次 section 被更新,系統記錄:
- 哪個 section 被改
- 改之前的版本與改之後的版本
- 來源(GitHub/GitLab item / Google Doc / 頻道 message / 本地檔案)
- 更新者
- 更新時間

回溯時可以針對單一 section 查詢歷史,不會被無關更新干擾。

### 5.3 輕量 cross-reference

Section 內提到的人、客戶、產品、其他 topic 都使用「一致的 grep-able 標記」,讓 owner 跨 topic 找關聯時用標準 shell tool 就能掃完。具體寫法:

| 指涉對象 | 標記寫法 | 範例 |
|---|---|---|
| 其他 topic | `[[<topic-name>]]` | `[[netops]]`、`[[my-product]]` |
| 人(chat handle) | `@<handle>` | `@alice`、`@bob` |
| 客戶 | `客戶: <name>` 或 `customer: <name>`(統一一種,在 background.md glossary 註明) | `客戶: Acme Corp` |
| 產品 | 產品 codename 直接寫 | `MyProduct v3` |
| GitHub / GitLab / Drive item | 永遠用 canonical URL | `https://github.com/owner/repo/issues/12` |

「自動建立」的具體實現是「規範化標記 + grep」,不維護任何後端 index 檔。Owner 要查「哪些 topic 提到 alice」就跑:

```bash
grep -rn '、@bob' ~/workspace/topic-*/memory/
```

跨 topic enumeration 屬 operator 的 host-level 操作,「不」放進 agentport 框架 (見 feedback-agentport-layering)。

不做形式化的知識圖譜抽取、不做 LLM-based entity linking、不做向量索引。

### 5.4 記憶必須引用來源

每段正式記憶必須能追溯到至少一個來源。沒有來源的內容不應進入正式記憶。

### 5.5 不應自動進入正式記憶的內容

- 閒聊
- 未確認的猜測
- 暫時想法
- 單一個人的未確認意見
- 與 topic 無關的內容

這類內容如果有價值,應由 contributor 提交為更新提案,owner 審核。

## 6. 功能需求

### 6.1 問答

- 被 tag 時回答 topic 相關問題
- 延續同一 thread 的上下文
- 回覆目前理解時包含 current understanding、相關背景、已知決策、open questions、來源、最後更新時間
- 軟拒絕時誠實說明沒有記憶,並請使用者指出相關來源

### 6.2 頻道討論整理

- 整理指定 thread（任一已啟用頻道）,輸出摘要、決策、待辦、owner、open questions、風險、理解變更、是否建議寫入長期記憶、來源
- Owner 可要求整理某段期間的 topic 相關討論

### 6.3 外部資料吸收

- 吸收 GitHub / GitLab(issue / MR / wiki / comment)、Google Docs、本地檔案
- 整理結果包含背景、狀態、決策、相關討論、open questions、對既有理解的影響、來源
- 文件內引用其他文件或任務時,在允許範圍內遞迴追蹤,並依下表標示來源狀態:

| 狀態 | 落點 | 含義 |
|---|---|---|
| 已處理 | `ingest_state.processed[]` | URL + ingested_at；已吸進 `sources/` |
| 未處理 | `ingest_state.queue[]` | URL + added_at；owner 標到但 agent 還沒跑 |
| 無權限 | `ingest_state.skipped[]` with `reason=no_permission` | 401/403 / 頻道 token 看不到 / Doc 沒 share / repo private |
| 超出範圍 | `ingest_state.skipped[]` with `reason=out_of_scope` | owner 標出邊界外、或 agent 判斷主題無關 |
| 其他跳過 | `ingest_state.skipped[]` with `reason=redirect_loop|other`, `note=<人話>` | 死循環、broken link、檔案 > 50MB 等 |

每筆 skipped 物件至少帶 `url` / `reason` / `ts`，便於 owner 之後決定是否補救。

- 遞迴吸收只允許 owner 觸發。每進入下一層深度前,agent 必須先把「候選清單」(下一層會吸收哪些連結 + agent 判斷的 in-scope / out-of-scope 標記) 餵給 owner 看,等 owner 逐筆 approve / skip / 標 out-of-scope 後才往下一層走。沒有 owner 在線的情況下,遞迴吸收停在當前層級,把候選清單寫進 `ingest_state.queue[]` 並標 `state="awaiting_owner"` 之類的等待態,絕不擅自往下。
- Default hard guard:遞迴深度上限 2 層 (一個 Doc 引用另一個 Doc 引用第三個 Doc,第三層停)。每次遞迴上限 50 個候選來源。owner 可在指令裡覆寫 (例如 "再深 1 層" 把上限放到 3),但每次都要明寫,不會跨 session 保留。
- Owner 可要求重新同步已吸收的來源,輸出變更摘要與對既有理解的影響

### 6.4 記憶更新

- Owner 直接更新正式記憶
- Contributor 提交更新提案,內容包含建議更新的 section、新內容、理由、來源
- 所有更新版控,記錄更新者、來源、時間

### 6.5 衝突偵測

吸收新來源或新訊息時,agent 比對是否與既有記憶矛盾。

偵測到衝突時,agent 不自動覆蓋,而是提出衝突點通知 owner,由 owner 決定要不要更新。

### 6.6 查詢來源與歷史

- 使用者可查詢某段記憶的來源
- 使用者可查詢某 section 的版本歷史:以前怎麼理解、現在怎麼理解、何時改變、為什麼改變、依據哪些來源、誰更新的

### 6.7 會議紀錄

- 整理會議逐字稿或會議相關 thread
- 輸出會議背景、討論重點、決策、待辦、open questions、風險、理解變更、來源
- 萃取理解變更(原本理解、新理解、變更原因、來源)
- 產生可貼回頻道的簡短摘要

### 6.8 客戶服務支援

- 整理支援 thread,輸出問題描述、客戶環境、已嘗試處理、目前判斷、客戶期待、下一步、owner、風險、來源
- 辨識可能對客戶構成承諾的內容,標示為 confirmed commitment / proposed / internal-only / unclear
- Owner 可更新對客戶或支援事件的理解

### 6.9 誠實回答的姿態

回答時需明確標示:
- 來源
- 來源的時間
- 信心強度(來源充分 / 來源單一未確認 / 沒有來源)
- 記憶最後更新時間(從 git log 取對應 section 的最新 commit date)
- 被使用者糾正時,在 `memory/corrections.md` 新增一筆「答錯的版本 / 對的版本 / 錯在哪 / 糾正者 / 來源」，同 commit 把對應的記憶 section 也修正。回答前 agent 必須先 grep corrections.md，避免重複犯錯。

## 7. 冷啟動

新 topic 建立時:

- 吸收該 topic 在所有 channel(以及與 owner 相關的 DM,在 owner 授權範圍內)的歷史討論
- 同步吸收 topic 對應 SCM repo(若有)的內容
- 同步吸收 topic 相關的 Google Doc、本地檔案(由 owner 提供初始來源清單)
- 遞迴追蹤被引用的文件,直到 owner 設定的範圍邊界

冷啟動期間,agent 不接受任何使用者請求。被 tag 時回覆一句固定訊息:正在吸收歷史,預計完成時間。

完成後通知 owner。

## 8. 新舊衝突的處理

冷啟動之後,當新訊息或新來源與既有記憶衝突時:

- 原則:新的優先
- 但 agent 不自動覆蓋,而是提出衝突點,標示哪一段舊記憶可能被取代、依據哪一條新來源
- 由 owner 決定要不要採納

## 9. 風險與限制

### 9.1 記憶污染

Contributor 提交的內容若被輕率 approve,可能將未確認資訊寫入正式記憶。

緩解:owner 審核責任、所有更新版控、可回溯誰寫了什麼。

### 9.2 權限外洩

文件、SCM repo、頻道歷史可能含敏感資訊。

緩解:agent 僅讀取授權範圍內的資料,且記憶引用來源時保留原始連結,讓 owner 隨時可核對。

### 9.3 過度吸收

遞迴追蹤可能導致範圍失控。

緩解:遞迴吸收僅 owner 可觸發,並標示已處理與未處理來源,owner 隨時可中止。

### 9.4 回答過度自信

agent 可能把不完整資訊講得像確定結論。

緩解:回答時強制標示來源、來源時間、信心強度;沒有來源時走軟拒絕。

### 9.5 記憶不會主動偵測過時

設計上記憶不會主動老化,只有新來源覆蓋才會更新。

代價:某段記憶若長期沒有新來源,可能與現實脫節。Owner 需在使用過程中自行判斷是否需要重新同步來源。

## 10. 產品邊界

包含:
- Topic expert agent(一 topic 一 agent)
- 跨 channel / DM 的 tag 觸發
- 任務範圍限定
- 頻道 thread 整理
- GitHub / GitLab / Google Doc / 本地檔案吸收
- 遞迴追蹤
- 重新同步來源
- 會議紀錄整理
- 支援 thread 整理
- 承諾辨識
- Section-level 記憶與版控
- 輕量 cross-reference
- Owner / contributor 權限
- 衝突偵測與 owner 仲裁
- 來源追溯
- 誠實回答姿態

## 11. 一句話版本

每個重要 topic 配一個專屬 agent,只在被 tag 時啟動,從指定來源（SCM repo、文件、session 紀錄、頻道討論）吸收相關內容,整理成 section-level 版控的「目前理解」,由 owner 把關更新、衝突時主動提問、誠實標示信心與來源。
