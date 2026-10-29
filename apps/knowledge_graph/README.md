# 知識圖譜與驗證句信心分數

本模組做兩件事：

1. **知識圖譜生成**：從 Podcast 逐字稿抽出「公司與公司之間的關係」，寫入 `knowledge_graphdb`。
2. **驗證句信心分數**：用知識圖譜裡的關係當證據，替回測紀錄（`BacktestingRecord`）裡的每一句投資看法（驗證句）打 0～100 分，寫入 `summariesdb.thesis_score`。

```
逐字稿（podcasts）
   │  generate_kg_from_transcripts
   ▼
知識圖譜（knowledge_graphdb：nodes / links）
   │  compute_thesis_scores
   ▼
驗證句信心分數（summariesdb：thesis_score）
```

| 檔案 | 用途 |
|---|---|
| `services/extract_v2.py` | 三步驟抽取：逐字稿 → 公司 + 關係（不寫資料庫） |
| `generate_by_podcast.py` | 批次處理集數、名稱統一、規則防線、寫入 `nodes` / `links` |
| `services/thesis_score.py` | 驗證句算分 |
| `services/thesis_baskets.py` | 指數、原油的代理公司籃子與權重 |
| `management/commands/generate_kg_from_transcripts.py` | 知識圖譜批次指令 |
| `management/commands/compute_thesis_scores.py` | 算分批次指令 |
| `generate.py` | 共用的 Gemini 呼叫（含 429 自動重試）、節點名稱正規化與比對 |

---

## 一、知識圖譜生成

### 1.1 資料與輸出

- **輸入**：`podcasts` 資料庫的 `PodcastEpisode` + `transcript.srt_content`（SRT 逐字稿）。
- **輸出**（`knowledge_graphdb`）：
  - `nodes(name, industry)`：公司節點。
  - `links(source, target, relation_type, reason, summary_date, podcast_source)`：公司之間的關係。
    - `summary_date`：節目發布日（`published_at`）。
    - `podcast_source`：`"{節目名} - {集名}"`，用來判斷哪些集數已處理、算分時排除同一集。

### 1.2 關係類型

| 類型 | 定義 | 方向 |
|---|---|---|
| `Supply` | 一方提供產品、零組件、代工、服務、授權給另一方 | 有方向：`source` = 供應方，`target` = 客戶 |
| `Co-impact` | 兩者因同一個具體事件或因素（可以是總體因素，如台幣升值、原料短缺）一起受益或受害 | 無方向 |
| `Substitution` | 兩者搶同一群客戶／訂單，或一方產品取代另一方 | 無方向 |

**不建立連線的情況**：只是被一起列舉、同產業、股價一起漲跌、用常識推得出來但節目沒講、假設性情境（「如果…」「可能會…」）、投資／入股／併購／持股／母子公司關係。

### 1.3 三步驟抽取（`services/extract_v2.py`）

舊做法是一次把 4 萬字逐字稿交給 LLM，同時找公司、判斷關係，再用 LLM 二階審查。在 20 集人工標註的評估集上，連線召回率 22.6%、precision 24.6%，而且將近一半的連線有一端是產業或產品。改成以下三步驟後，兩者都提升到約 40%，非公司節點幾乎消失。

#### 步驟 1：公司抽取與名稱對照

1. 逐字稿依 SRT 逐句切成約 **3,000 字**一段，相鄰段落重疊 3 句，4 段平行呼叫 LLM。
2. LLM 列出段落中的專有名詞，並用 `kind` 分類：`company`、`etf`、`person`、`country_or_government`、`industry_or_sector`、`index_or_market`、`commodity_or_currency`、`media`、`financial_research_institution`、`podcast_or_sponsor`、`other`。**程式只保留 `company` 與 `etf`**——讓模型分類比要求它「不要輸出」可靠得多。
3. 每個名稱附上：
   - `mention`：逐字稿原文寫法（**必須真的出現在該段原文**，否則視為幻覺丟棄）
   - `canonical_name`：正式名稱
   - `ticker`：確定才填
   - `industry`：產業分類（給 `nodes.industry` 用）
4. 規則：
   - 只以產品／品牌出現的，算成母公司（ChatGPT → OpenAI、Claude → Anthropic、iPhone → 蘋果）。
   - 語音辨識錯字只有在上下文能確定時才轉成公司名。
   - 評等、研究、資料、投資銀行、顧問機構（標普、穆迪、彭博、高盛、TrendForce…）、新聞媒體、節目業配不算公司。
5. **名稱合併**：用 TickerMap（`asset_name` / `zh_name` / `ticker` 三欄，經全形轉半形、去空白、轉小寫正規化）把寫法對應到 ticker；ticker 相同、或任一寫法正規化後相同，就用 union-find 合併成同一家公司。顯示名稱優先用 TickerMap 中含中文的 `zh_name`。
6. 給 LLM 的提示會附上「本段出現的已知公司：寫法 → 正式名稱(ticker)」，讓 `canonical_name` 一致；子品牌、雲端服務（Azure、AWS、Google Cloud）一律用母公司名稱。

模型：`settings.GEMINI_MODEL`（預設 `gemini-2.5-flash-lite`）。評估結果：重要公司召回率 98.9%、precision 86%。

#### 步驟 2：候選公司組合（純規則，不用 LLM）

- 兩家公司的 mention 在原文中距離 **250 字以內**，就是一組候選。
- 截取兩者一起出現的片段（前後各多取 200 字），距離越近的優先，重疊片段合併，每組最多 3 段。
- 依共同出現次數排序，每集最多 **120 組**。

#### 步驟 3：逐組判斷關係

- 每次 LLM 呼叫判斷 **8 組**，4 個平行。
- 每組只給相關片段，四選一：`Supply` / `Co-impact` / `Substitution` / `None`；Supply 另外回答哪一方是供應方。
- 非 `None` 時必須附上 **evidence_quote**：從片段逐字複製 10～80 字（可跨行），且**同時包含兩家公司的名稱**。
- 程式驗證：
  - 引用句去掉空白後必須逐字存在於逐字稿，長度 6～120 字，否則丟棄（`dropped_bad_quote`）。
  - 引用句必須同時出現兩家公司的某個寫法，否則丟棄（`dropped_quote_missing_entity`）。
  - Supply 沒有給方向的丟棄。
- prompt 內有明確反例：股價／盤勢列舉（「輝達、博通、甲骨文都跌 3%」）、只是被歸在同一類、「A、B 都是 C 的競爭對手」不代表 A、B 互搶、都要發表新產品不代表互搶。

模型：`settings.KG_RELATION_MODEL`（預設 `gemini-2.5-flash`）。flash-lite 做關係判斷過於保守，幾乎全判 None，連線召回率只有 flash 的一半不到。

#### 輸出上限與錯誤處理

- 每次呼叫限制輸出 8,192 tokens。小模型搭配 response_schema 偶爾會陷入重複輸出同一筆的迴圈，有上限才不會拖到超時；被截斷的 JSON 會救回截斷點前已完整的項目。
- **API 錯誤不吞掉**：重試用盡（例如 429 配額冷卻超過約 2.4 小時）時整集放棄、什麼都不寫入，下次執行時這集仍是「未處理」會自動補跑。若只略過一段，會把不完整結果寫進資料庫，而且這集之後會被當成已處理，永遠補不回來。

### 1.4 寫入資料庫（`generate_by_podcast.py`）

1. **只為出現在連線裡的公司建立節點**，不產生孤立節點。
2. **名稱統一**：先比對 TickerMap 別名索引，命中就用 TickerMap 的 `asset_name`；比對不到再用既有節點的正規化比對 + LLM 語意比對（`generate.py` 的 `_resolve_node_name`），避免同一家公司重複建節點。
3. **規則防線**（不完全依賴 LLM）：
   - 自環（source = target）跳過。
   - `reason` 籠統黑名單（`_is_vague_reason`）：例如「都屬於同產業」「皆受…影響」「同屬…概念」、長度 < 8 字。
   - **Supply 方向衝突**：`summary_date` 前後 7 天內已存在反方向的 Supply 連線就跳過（視為需要人工複核）。Substitution、Co-impact 不檢查方向。
   - 重複連線（同 source、target、reason）跳過。

### 1.5 指令與執行

```bash
python manage.py generate_kg_from_transcripts                                # 處理所有未處理集數
python manage.py generate_kg_from_transcripts --limit 20                     # 小範圍測試
python manage.py generate_kg_from_transcripts --published-after 2026-01-01   # 依發布日篩選
python manage.py generate_kg_from_transcripts --episode-ids 158 159          # 指定集數（不受跳過邏輯限制）
```

- 依 `published_at` 由舊到新處理；`links` 已存在該集 `podcast_source` 的視為已處理、直接跳過。中斷後重新執行同一指令會接續。
- 沒抽出任何連線的集數，每次重跑都會再處理一次（多花一些 API 費用，不影響正確性）。
- **不要用 `--episode-ids` 重跑已處理過的集數**：會繞過跳過邏輯，新舊連線疊在一起。
- 遇到 429 會自動等待（60 秒起遞增、單次上限 600 秒、最多 20 次，約 2.4 小時）。
- 成本：每集約 40 秒、LLM 呼叫約 10～20 次。
- API：`views.generate_graph_from_episode`（單集）、`views.generate_graph_from_episodes`（批次）。

### 1.6 相關設定

| 設定 | 預設值 | 用途 |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | 步驟 1、名稱語意比對、算分時的證據判斷 |
| `KG_RELATION_MODEL` | `gemini-2.5-flash` | 步驟 3 關係判斷 |

---

## 二、驗證句信心分數

### 2.1 整體流程

```
驗證句（標的 asset、方向 direction、內容 thesis、日期 start_time、講者 podcaster）
   │
   ▼
① 解析標的名稱 → 找知識圖譜裡的對應節點
   │
   ├─ 找得到公司節點 ───────────────────→ 公司模式    mode = "company"
   └─ 找不到
        ├─ 有籃子定義的指數或原油 ───────→ 籃子模式    mode = "basket"
        └─ 其他 ──────────────────────→ 只用說話者  mode = "speaker_only"
   │
   ▼
raw  = 證據 raw + 說話者加減分
分數 = 100 / (1 + e^(−raw / 30))
```

`raw = 0` 時是 50 分；正數往 100 靠近，負數往 0 靠近。

### 2.2 共用步驟

#### ① 標的名稱解析（`_resolve_asset_aliases`）

把驗證句寫的標的對應到知識圖譜裡所有可能的節點寫法：

1. 原字串本身。
2. 正規化後相同的節點（「NIKE」↔「Nike」）。
3. 同一個 ticker 的所有節點：先用 `resolve_ticker()`，查不到再用 TickerMap 三欄正規化比對；再找出圖譜裡所有解析到同一 ticker 的節點（「輝達」↔「NVIDIA」）。
   指數／商品節點要「去掉指數、ETF、基金、類股等字尾後完全相等」才算，避免「標普全球」這家公司被誤認成 S&P 500。

#### ② 證據的時間窗口與排除

| 參數 | 值 | 說明 |
|---|---|---|
| `WINDOW_DAYS` | 7 | 抓 `[start_time − 7 天, start_time]` 的連線 |
| `FALLBACK_WINDOW_DAYS` | 14 | 7 天內完全沒有可用連線時放寬 |

- 只抓 `Supply` / `Co-impact` / `Substitution`。
- **排除驗證句自己那一集**的連線（新舊兩種 `podcast_source` 格式都比對），避免自我佐證。

#### ③ 說話者加減分（三種模式都有）

用說出這句驗證句的講者（`SummaryRecord.podcaster`）在**驗證句日期之前已經結算**（`end_time < start_time`）的 pass / fail 紀錄計算，同講者同一句只算一次。只用過去的資料，避免回測時用到未來資訊。

```
差距   = (講者準確率 − 同時間點全體講者平均準確率) / 0.25 × n / (n + 10)
差距   限制在 [−1, 1]
加減分 = 15 × 差距
```

- 同產業（TickerMap.sector）樣本 ≥ 5 筆時用同產業準確率，否則用全體。
- 總樣本 < 5 筆時不加減分。
- 基準是全體平均而不是固定 50%：整體看多的驗證句比較容易過關，多數講者準確率都在 65～70%，用 50% 會變成人人加分。

| 參數 | 值 |
|---|---|
| `SPEAKER_WEIGHT` | 15 |
| `SPEAKER_FULL_SCALE_GAP` | 0.25 |
| `SPEAKER_SHRINK_K` | 10 |
| `SPEAKER_MIN_SAMPLE` | 5 |

例：講者準確率 70%、全體平均 65%、樣本 100 筆 → 差距 0.05 / 0.25 × 100/110 ≈ 0.18 → raw **+2.7**。

#### ④ 分數轉換

`SIGMOID_SCALE = 30`。原本 100 時八成分數擠在 47～56，看不出差異。

| raw | −60 | −30 | −15 | 0 | +15 | +30 | +60 |
|---|---|---|---|---|---|---|---|
| 分數 | 12 | 27 | 38 | 50 | 62 | 73 | 88 |

### 2.3 公司模式（`mode = "company"`）

標的在圖譜裡找得到公司節點時使用。

**步驟 1：證據分群**——依（鄰居公司, 關係類型）分組，組內用 LLM 兩兩比對 reason 是否在講同一件事，聚成「事實」；重複次數 n = 事實包含的連線數。避免同一件事被多集重複報導就灌分。

**步驟 2：LLM 判斷每個事實與驗證句的關係**——每句驗證句呼叫一次，所有事實一起送入，判斷：

- `stance`：`support`（強化這句話的論點）／`contradict`（削弱）／`unrelated`（不同面向，或看不出影響）
- `strength`：`strong`（直接明確）／`weak`（需要推論）

判斷對象永遠是「這個標的的這句話」：競爭對手搶到訂單，對看多該標的的驗證句是 contradict。同一條證據對不同驗證句可以得到不同判斷，所以同一週、同一標的、內容不同的驗證句會有不同分數。

**步驟 3：每個事實的貢獻**

```
貢獻 = 帶正負號的權重 × (1 + ln n) × 來源可信度
```

帶正負號的權重，依優先順序：

| 模式 | 條件 | 權重 |
|---|---|---|
| `thesis` | LLM 有判斷 | `MAGNITUDES[類型] × STANCE_SIGN × STRENGTH_WEIGHT` |
| `aligned` | LLM 沒判斷到，但查得到鄰居公司同期的情緒分數（`StockSentimentScore.macro_score` 平均） | `MAGNITUDES[類型] × RELATION_POLARITY[類型] × 鄰居方向 × 驗證句方向` |
| `legacy` | 以上皆無 | `LEGACY_SIGNED_WEIGHTS[類型]` |

| 參數 | 值 |
|---|---|
| `MAGNITUDES` | Supply 15、Co-impact 10、Substitution 20 |
| `STANCE_SIGN` | support +1、contradict −1、unrelated 0 |
| `STRENGTH_WEIGHT` | strong 1.0、weak 0.5 |
| `RELATION_POLARITY` | Supply +1、Co-impact +1、Substitution −1 |
| `DIRECTION_SIGN` | bullish +1、bearish −1 |
| `LEGACY_SIGNED_WEIGHTS` | Supply +15、Co-impact +10、Substitution −20 |

來源可信度：說出這條證據的講者（由 `podcast_source` 反查）的準確率，取自 `SpeakerAccuracy` 快照表；事實內有多位講者時取平均。

```
可信度 = 1 + (準確率 − 0.5) × 1.0，限制在 [0.5, 1.5]；樣本 < 5 筆時 = 1.0
```

**步驟 4**：`證據 raw = Σ 貢獻`，`raw = 證據 raw + 說話者加減分`。

例：2 條強烈支持的 Supply、1 條弱反駁的 Substitution，可信度 1.0：`15 + 15 − 20×0.5 = 20`，加講者 +2.7 → raw 22.7 → **約 68 分**。

### 2.4 籃子模式（`mode = "basket"`）

標的在圖譜裡找不到節點（新圖譜只收公司，沒有指數、商品節點），但 `resolve_ticker()` 解析出的 ticker 有籃子定義時使用。邏輯是：**指數漲跌來自成分股，所以看成分股本身這段期間好不好，再依權重加總**，而不是判斷成分股證據跟大盤驗證句有沒有關係（大盤驗證句多半在講籌碼、總經，成分股的產業證據幾乎都會被判成無關）。

#### 籃子定義（`services/thesis_baskets.py`）

| 籃子 | 觸發 ticker | 成分（權重為約略市值權重） |
|---|---|---|
| 台股加權指數 | `^TWII` | 前 20 大：台積電 42、鴻海 5、聯發科 4、台達電 3、廣達 2.2… |
| 櫃買指數 | `^TWOTCI` | 信驊、環球晶、世界先進、群聯… 10 家 |
| S&P 500 | `^GSPC` | 輝達 7.5、蘋果 6.5、微軟 6.5… 17 家 |
| 道瓊 | `^DJI` | 沿用 S&P 500 |
| 那斯達克 | `^IXIC` | 輝達 9、微軟 8、蘋果 8… 12 家 |
| 費城半導體 | `^SOX` | 輝達 9、博通 8、AMD 7、台積電 6… 14 家 |
| 原油 | `CL=F`（含 WTI） | 台塑化、台塑、南亞、台化、雪佛龍、埃克森美孚（正向）；中華航空（**反向 −1**） |

`sign` 表示該公司狀況對標的的方向：油價漲對航空公司是成本壓力，所以華航 −1。指數成分大幅調整時更新這張表即可。

#### 計算

1. **收集事實**：每家成分股用 ticker + 名稱找節點、抓窗口內連線（7 天，整個籃子都沒證據時放寬到 14 天）。不做 LLM 兩兩分群（籃子連線動輒上百條），改以（鄰居, 類型）分組，依被幾集提到排序，每家最多 **8 個事實**。
2. **LLM 判斷每個事實對「該成分股本身」是利多或利空**（與驗證句無關）：`impact` = positive / negative / neutral，`strength` = strong / weak。判斷結果依（公司, 鄰居, 類型, reason）**快取**，同一批次的其他驗證句直接重用。
3. **每家公司的狀況**：

   ```
   份量 = (MAGNITUDES[類型] / 15) × STRENGTH_WEIGHT × (1 + ln 集數)
   利多 = Σ positive 事實的份量
   利空 = Σ negative 事實的份量
   狀況 s_i = (利多 − 利空) / (利多 + 利空 + 2)          （一定落在 −1 ~ +1）
   ```

   一條強烈的 Supply 證據 = 1 單位，Co-impact 0.67，Substitution 1.33。

   | 情況 | s_i |
   |---|---|
   | 1 條強 Supply 利多 | 0.33 |
   | 3 條 | 0.60 |
   | 10 條 | 0.83 |
   | 利多、利空一樣多 / 沒有證據 | 0 |

4. **指數訊號與證據 raw**：

   ```
   指數訊號 = Σ (權重_i × sign_i × s_i) / 全部成分股權重      （−1 ~ +1）
   證據 raw = 驗證句方向(+1 / −1) × 指數訊號 × 60
   raw     = 證據 raw + 說話者加減分
   ```

   分母是全部成分股權重，沒有證據的公司算 0；單一公司最多只能貢獻自己的權重（台積電再多連線，最多影響 42/71 ≈ 59%），連線數再多也不會讓分數無限沖高。

| 參數 | 值 |
|---|---|
| `BASKET_COMPANY_SHRINK` | 2 |
| `BASKET_MAGNITUDE_UNIT` | 15 |
| `BASKET_MAX_RAW` | 60（指數訊號 = 1 時約 88 分） |
| `BASKET_FACTS_PER_COMPANY` | 8 |

例：台積電狀況 0.83、鴻海 0.57、其他合計貢獻 +2，權重總和 71 → 指數訊號 ≈ 0.56 → 看多驗證句約 **75 分**、看空驗證句約 **25 分**。

### 2.5 只用說話者（`mode = "speaker_only"`）

找不到節點、也沒有籃子定義時使用：`raw = 說話者加減分`，分數約 38～62。結果帶 `unscored_reason`：

| 情況 | unscored_reason |
|---|---|
| 黃金、白銀、比特幣、日圓、美元指數… | 商品或匯率，知識圖譜沒有對應的產業證據 |
| 羅素 2000、上證、Stoxx 600 | 指數，尚未定義成分股籃子 |
| 美股、美元、聯準會、台幣… | 總經或泛稱標的，知識圖譜沒有對應的產業證據 |
| 圖譜沒收錄的公司（力成、元太…） | 知識圖譜沒有收錄這家公司 |

### 2.6 批次執行與儲存

```bash
python manage.py compute_thesis_scores               # 算尚未有分數的紀錄
python manage.py compute_thesis_scores --recompute   # 全部重算
python manage.py compute_thesis_scores --limit 20    # 小範圍測試
```

- 處理對象：`BacktestingRecord` 中 asset 不為空、有 `start_time` 的紀錄，且 `start_time ≤ links 表最新的 summary_date`（知識圖譜已涵蓋的範圍）；超出範圍的留給之後執行。**請等知識圖譜跑完再算分**，否則邊界附近的驗證句證據不完整、之後也不會自動重算。
- 預設跳過已有分數的紀錄，寫入前再即時確認一次，避免兩個程序同時跑造成重複。
- 整批共用、只建一次：名稱對照索引、說話者歷史（`SpeakerHistory`）、籃子模式的利多利空快取。
- 寫入 `thesis_score`（先刪同一 `backtesting_id` 的舊紀錄再插入）：`score`、`raw`、`facts`（jsonb：每個事實的鄰居、類型、reason、重複次數、判斷與理由、貢獻；籃子模式另含成分股名稱）。
  `mode`、`speaker`、`basket`、`evidence_raw`、`window_days` 目前只在函式回傳值中，**沒有存進資料表**。
- 錯誤處理：資料庫連線錯誤在同一筆重試（等待遞增）；LLM 判斷失敗時公司模式退回 aligned / legacy，籃子模式算 0；其他錯誤跳過該筆。
- 每句的 LLM 呼叫：公司模式 = 證據分群兩兩比對（熱門股可達數十次）+ 判斷 1 次；籃子模式 ≤ 1 次（有快取）；只用說話者 0 次。
- Supabase pooler 連線上限 15，平行執行時注意每個執行緒會開 3 個資料庫連線（summariesdb、knowledge_graphdb、podcasts），建議 ≤ 4 個執行緒。

---

## 三、已知限制與評估結果

### 3.1 2026-10 評估（240 筆有回測結果的驗證句，看多／看空 × pass／fail 各 60 筆）

| 分數組成 | 看多 AUC | 看空 AUC |
|---|---|---|
| 目前參數 | 0.397 | 0.581 |
| 只用說話者（全體基準） | 0.414 | 0.589 |
| 只用說話者（同方向基準） | 0.486 | 0.588 |

（AUC 0.5 = 隨機；樣本誤差約 ±0.05）

- 目前分數**預測不出**驗證句會不會通過；交叉驗證調參後在驗證集反而更差，參數差異屬雜訊。
- `MAGNITUDES` 量級（Supply 15 / Co-impact 10 / Substitution 20）是設計上的假設，讓圖譜的關係類型直接影響證據份量，在 5～30 之間調整看不出一致差異。
- pass / fail 很大程度反映大盤方向：看多通過率約 80%、看空約 21%。若改用相對大盤的超額報酬判定，較能衡量「選股」而非大盤方向。
- 看多驗證句的證據越支持反而越常失敗（AUC 0.40），可能是利多已反映在股價，樣本不足以下定論。
- `SIGMOID_SCALE` 只影響分數的分佈寬度，不影響排名，也不影響 AUC。

### 3.2 覆蓋率

- 涵蓋期間內約 26% 的驗證句在圖譜找不到節點：58% 是指數／商品／匯率，約 7% 是總經泛稱，約 30% 是公司（名稱寫法不同，或圖譜本來就沒收錄的小型股）。
- 指數類由籃子模式補上；黃金、白銀、比特幣、總經、匯率只用說話者準確率。
- 公司模式中，籌碼面、技術面驗證句（「外資賣超」「法說會後整理」）的產業證據通常被判為無關，分數落在 50 附近。
- 早期（2025 年中以前）的驗證句，講者還沒有足夠的已結算紀錄，說話者加減分為 0。

### 3.3 其他

- 兩種講者準確率標準不一致：「來源可信度」用 `SpeakerAccuracy` 快照（含未來才結算的結果、基準固定 50%），「說話者加減分」只用當時之前的資料。
- `LEGACY_SIGNED_WEIGHTS` 不考慮看多看空（看空驗證句遇到 Supply 證據仍 +15），但 LLM 判斷幾乎都會成功，實際極少用到。
- 名稱寫法不同可補 TickerMap 別名救回（世芯KY → 世芯、好市多 → Costco、TSMC → 台積電…）；`resolve_ticker()` 的大寫規則會把產品名（DRAM、NAND、ABF）誤判成 ticker。
- 知識圖譜裡仍有同一家公司的重複節點（例如「埃克森美孚」與「ExxonMobil」），籃子定義已用多個名稱同時比對。
