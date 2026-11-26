// ── EXPLORE 頁面：探索投資機會（相關標的）────────────────────────
// 原本是 knowledge_graph app 的獨立頁面（/api/knowledge-graph/basket/），併進單頁系統後
// 跟其他分頁一樣用 showPage('explore') 切換；HTML 在 templates/pages/explore.html。
// 整支包在 IIFE 裡：run／render／esc 這類名稱太通用，不能放到全域跟別支程式撞名。
// 對外只開放 window.Explore（見檔尾），HTML 與動態產生的內容都用 Explore.xxx() 呼叫。
(function () {

let selectedPurpose = null;
let _purposeFailed = false;
let selectedTier = "均衡";
let lastData = null;

const TIER_OPTIONS = [
  { key: "積極", label: "積極型", desc: "成長導向，高風險高報酬" },
  { key: "均衡", label: "穩健型", desc: "平衡風險與報酬" },
  { key: "保守", label: "保守型", desc: "防禦導向，穩定現金流" },
];

const PURPOSE_ICONS = {
  supply_beneficiary: "upload",
  supply_customer: "download",
  diversify_exposure: "swords",
  same_theme: "hub",
};

let _initialized = false;

async function initOnce() {
  if (_initialized) return;
  _initialized = true;

  const tierGrid = document.getElementById("explore-tier-grid");
  TIER_OPTIONS.forEach(t => {
    const el = document.createElement("div");
    el.className = "tier-card cursor-pointer rounded-lg border-2 border-outline-variant/50 bg-surface-container-lowest p-4 text-center hover:border-outline transition-colors";
    el.dataset.key = t.key;
    el.innerHTML = `
      <div class="font-semibold text-base text-on-surface">${t.label}</div>
      <div class="text-[13px] text-on-surface-variant mt-0.5">${t.desc}</div>
    `;
    el.onclick = () => selectTier(t.key);
    tierGrid.appendChild(el);
  });
  selectTier("均衡");
  document.getElementById("explore-end-date").value = new Date().toISOString().split("T")[0];

  await loadPurposes();
}

async function loadPurposes() {
  try {
    const d = await (await fetch("/api/knowledge-graph/basket-purposes/")).json();
    const grid = document.getElementById("explore-purpose-grid");
    (d.purposes || []).forEach((p, i) => {
      const el = document.createElement("div");
      el.className = "purpose-card cursor-pointer rounded-lg border-2 border-outline-variant/50 bg-surface-container-lowest p-4 hover:border-outline transition-colors";
      el.dataset.key = p.key;
      el.innerHTML = `
        <span class="card-icon material-symbols-outlined text-3xl text-on-surface-variant">${PURPOSE_ICONS[p.key] || "category"}</span>
        <div class="font-semibold text-base text-on-surface mt-2">${esc(p.label)}</div>
        <div class="text-[13px] text-on-surface-variant mt-0.5 leading-snug">${esc(p.description)}</div>
      `;
      el.onclick = () => selectPurpose(p.key);
      grid.appendChild(el);
      if (i === 0) selectPurpose(p.key);
    });
  } catch (e) {
    _purposeFailed = true;
    showError("投資目的載入失敗：" + e.message);
  }
}

// 切到這一頁時由 app.js 的 _renderPage() 呼叫
function onShow() {
  if (!_initialized) initOnce();
  else if (_purposeFailed) {     // 之前沒載到投資目的（例如那時網路斷了）就再試一次
    _purposeFailed = false;
    hideError();
    loadPurposes();
  }
  // 先在背景把「查詢標的」搜尋建議的索引建好，打第一個字時就不用等 3~4 秒
  fetch("/api/knowledge-graph/seed-suggest/?warm=1").catch(() => {});
}

function selectPurpose(key) {
  selectedPurpose = key;
  document.querySelectorAll(".purpose-card").forEach(el => el.classList.toggle("active", el.dataset.key === key));
}

function selectTier(key) {
  selectedTier = key;
  document.querySelectorAll(".tier-card").forEach(el => el.classList.toggle("active", el.dataset.key === key));
}

function clearSeed() {
  document.getElementById("explore-seed-input").value = "";
  closeSeedDropdown();
}

// ── 查詢標的搜尋建議 ──────────────────────────────────────────
let _seedTimer = null;
let _seedReq = 0;

function onSeedInput() {
  const q = document.getElementById("explore-seed-input").value.trim();
  clearTimeout(_seedTimer);
  if (!q) { closeSeedDropdown(); return; }
  _seedTimer = setTimeout(() => runSeedSuggest(q), 300);
}

function closeSeedDropdown() {
  _seedReq++;                      // 還在路上的舊回應不要再蓋回來
  clearTimeout(_seedTimer);
  const dd = document.getElementById("explore-seed-dropdown");
  dd.classList.add("hidden");
  dd.innerHTML = "";
}

async function runSeedSuggest(q) {
  const dd = document.getElementById("explore-seed-dropdown");
  const reqId = ++_seedReq;
  dd.innerHTML = '<p class="text-outline text-base p-4 text-center">搜尋中...</p>';
  dd.classList.remove("hidden");
  try {
    const res = await fetch("/api/knowledge-graph/seed-suggest/?q=" + encodeURIComponent(q));
    const data = await res.json();
    if (reqId !== _seedReq) return;     // 使用者已經輸入了更新的內容
    if (!res.ok) throw new Error(data.error || res.status);
    const list = data.suggestions || [];
    dd.innerHTML = list.length ? list.map(s => `
      <div data-name="${escAttr(s.name)}" onclick="Explore.pickSeedSuggestion(this.dataset.name)"
        class="flex items-center justify-between gap-3 px-4 py-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
        <div class="flex items-baseline gap-2 min-w-0">
          <span class="text-xs font-bold text-outline uppercase tracking-wider flex-shrink-0 px-1.5 py-0.5 rounded bg-surface-container-highest">${esc(s.market)}</span>
          <span class="text-lg font-bold text-tertiary-container truncate">${esc(s.name)}</span>
          ${s.code !== s.name ? `<span class="text-base text-outline flex-shrink-0">${esc(s.code)}</span>` : ""}
        </div>
      </div>`).join("")
      : '<p class="text-outline text-base p-4 text-center">圖譜裡找不到相關的投資標的</p>';
  } catch (e) {
    if (reqId === _seedReq) dd.innerHTML = '<p class="text-error text-base p-4 text-center">搜尋失敗，請稍後再試</p>';
  }
}

// 點建議只把名稱填進輸入框（還要選關係類型再按查詢），名稱就是圖譜節點名，查詢時才對得到
function pickSeedSuggestion(name) {
  document.getElementById("explore-seed-input").value = name;
  closeSeedDropdown();
}

// 從個股詳情頁的「探索相關標的」過來（assets.js 呼叫）：把名稱填進「查詢標的」；
// 名稱不一定等於圖譜裡的節點名（查詢要用節點名才對得到），所以再用代號問搜尋建議，
// 找到代號完全相同的標的就換成它的節點名。只填入、不自動查詢：關係類型、投資類型還要讓使用者確認。
const _nodeNameBySymbol = {};   // 代號 → 查到過的節點名
let _activeSeed = null;         // 目前畫面上（查詢中或已查完）是哪個標的的結果

async function openWithSeed(seed, symbol) {
  const inp = document.getElementById("explore-seed-input");
  // 畫面上留著別支標的的結果會讓人以為是這支的，清掉；同一支就保留，來回切換不用重查
  if (_activeSeed && _activeSeed !== seed && _activeSeed !== _nodeNameBySymbol[symbol]) resetResults();
  closeSeedDropdown();
  hideError();
  inp.value = _nodeNameBySymbol[symbol] || seed || symbol;
  if (!symbol || _nodeNameBySymbol[symbol]) return;
  const filled = inp.value;
  try {
    const res = await fetch("/api/knowledge-graph/seed-suggest/?q=" + encodeURIComponent(symbol));
    if (!res.ok) return;
    const hit = ((await res.json()).suggestions || []).find(s => String(s.code).toLowerCase() === symbol.toLowerCase());
    if (!hit) return;
    _nodeNameBySymbol[symbol] = hit.name;
    if (inp.value === filled) inp.value = hit.name;     // 使用者已經改過輸入框就不要蓋掉
  } catch (e) { /* 查不到就維持帶過來的名稱 */ }
}

// 回到還沒查詢的狀態：中斷進行中的查詢、收掉結果／詳情面板／標題
function resetResults() {
  if (currentSearch) { currentSearch.close(); currentSearch = null; }
  _scoreReq++;
  _activeSeed = null;
  lastData = null;
  setLoading(false);
  closeDetail();
  document.getElementById("explore-results").classList.add("hidden");
  document.getElementById("explore-title").textContent = "探索投資機會";
}

// 右上角「查看個股資訊」：這組切換鈕對應的是個股清單。上一頁就是個股頁時走返回（捲動位置會還原），
// 如果個股頁停在某支標的的詳情，收起來回清單。
function showAssetsList() {
  const prev = _pageHistory[_pageHistory.length - 1];
  if (prev && prev.page === "assets") goBack(); else showPage("assets");
  if (!document.getElementById("assets-detail-view").classList.contains("hidden")) {
    _backOverride = null; _backOverrideOwner = null; _backOverrideArmed = false;
    closeAssetDetail();
  }
}

document.addEventListener("click", (e) => {
  const dd = document.getElementById("explore-seed-dropdown");
  const inp = document.getElementById("explore-seed-input");
  if (dd && inp && !dd.contains(e.target) && e.target !== inp) closeSeedDropdown();
});

let currentSearch = null;

const STEP_ICONS = {
  start: "travel_explore",
  candidate_prep: "travel_explore",
  relation_check: "psychology",
  synthesize: "auto_awesome",
  financial_screen: "query_stats",
};

function run() {
  const seed = document.getElementById("explore-seed-input").value.trim();
  closeSeedDropdown();
  hideError();
  if (!seed) { showError("請輸入標的名稱"); return; }
  if (!selectedPurpose) { showError("請選擇關係類型"); return; }

  const windowDays = document.getElementById("explore-window-days").value;
  const params = new URLSearchParams({
    seed, purpose: selectedPurpose, window_days: windowDays,
    end_date: document.getElementById("explore-end-date").value,
    risk_tier: selectedTier,
  });

  document.getElementById("explore-results").classList.add("hidden");
  _activeSeed = seed;
  setLoading(true);
  updateProgress({ stage: "start", label: "準備中…", percent: 0 });

  if (currentSearch) currentSearch.close();
  const es = new EventSource("/api/knowledge-graph/basket-stream-data/?" + params);
  currentSearch = es;

  es.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch (e) { return; }

    if (msg.type === "progress") {
      updateProgress(msg);
    } else if (msg.type === "done") {
      es.close();
      currentSearch = null;
      setLoading(false);
      lastData = msg.result;
      render(msg.result);
    } else if (msg.type === "error") {
      es.close();
      currentSearch = null;
      setLoading(false);
      showError(msg.error || "查詢發生錯誤");
    }
  };

  es.onerror = () => {
    es.close();
    if (currentSearch === es) {
      currentSearch = null;
      setLoading(false);
      showError("查詢連線發生錯誤，請重試");
    }
  };
}

function updateProgress(msg) {
  document.getElementById("explore-progress-icon").textContent = STEP_ICONS[msg.stage] || "hourglass_top";
  document.getElementById("explore-progress-label").textContent = msg.label || "處理中…";
  document.getElementById("explore-progress-sub").textContent =
    (msg.item_current != null && msg.item_total) ? `第 ${msg.item_current} / ${msg.item_total} 項` : "";
  const pct = Math.max(0, Math.min(100, Math.round(msg.percent ?? 0)));
  document.getElementById("explore-progress-percent").textContent = pct + "%";
  document.getElementById("explore-progress-bar").style.width = pct + "%";
}

function render(data) {
  document.getElementById("explore-title").textContent = `探索與 ${data.seed} 相關的投資機會`;
  document.getElementById("explore-result-title").textContent = `找到 ${data.basket.length} 個相關標的`;

  const grid = document.getElementById("explore-basket-grid");
  if (!data.basket.length) {
    const suggestions = data.seed_suggestions || [];
    grid.className = "";
    grid.innerHTML = suggestions.length ? `
      <div class="text-center py-14 text-on-surface-variant text-base">
        圖譜裡找不到「${esc(data.seed)}」，你是不是要找：
        <div class="flex gap-2 justify-center flex-wrap mt-4">
          ${suggestions.map(s => `<span class="px-4 py-1.5 rounded-full border border-outline-variant text-base cursor-pointer hover:border-tertiary-container hover:text-tertiary-container transition-colors" onclick="Explore.pickSuggestion('${esc(s).replace(/'/g, "\\'")}')">${esc(s)}</span>`).join("")}
        </div>
      </div>` : `<div class="text-center py-14 text-on-surface-variant text-base">沒有找到符合條件的標的，試試看調整窗口長度或換一個標的。</div>`;
  } else {
    grid.className = "grid grid-cols-1 md:grid-cols-2 gap-4";
    grid.innerHTML = data.basket.map((c, i) => cardHtml(c, i)).join("");
    loadAssetScores(data);
  }
  document.getElementById("explore-results").classList.remove("hidden");
}

// 「Podcast 看法」分數：Basket 結果畫好後另外算（要跑 LLM 判斷），不拖慢原本的搜尋。
// 只反映窗口期間節目的觀點，回測跟之後的漲跌沒有關係，不能當成預測。
let _scoreReq = 0;
const KG_SCORE_COLORS = { "偏多": "#2e7d32", "偏空": "#c62828", "中性": "#6b6b6b", "無資料": "#6b6b6b" };

async function loadAssetScores(data) {
  const req = ++_scoreReq;
  const params = new URLSearchParams({ end_date: data.end_date, window_days: data.window_days });
  data.basket.forEach(c => params.append("node", c.node));
  let scores = null;
  try {
    const res = await fetch("/api/knowledge-graph/asset-scores/?" + params);
    if (res.ok) scores = (await res.json()).scores;
  } catch (e) { /* 下面統一顯示無法計算 */ }
  if (req !== _scoreReq) return;  // 使用者已經發起新的搜尋
  data.basket.forEach((c, i) => {
    const el = document.getElementById(`kg-score-${i}`);
    if (el) el.innerHTML = kgScoreHtml(scores && scores[c.node], data.window_days);
  });
}

function kgScoreHtml(s, windowDays) {
  if (!s) return `<span class="text-sm text-on-surface-variant">無法計算</span>`;
  const reasonLines = (title, reasons) =>
    reasons.length ? [title, ...reasons.map(r => `・${r.reason}`)] : [];
  const tip = [
    ...reasonLines("利多：", s.bullish_reasons || []),
    ...reasonLines("利空：", s.bearish_reasons || []),
  ].join("\n") || "這段期間沒有判斷出利多或利空的理由";
  return `
    <div class="text-xs text-on-surface-variant">近 ${esc(windowDays)} 天 Podcast 看法</div>
    <div class="text-2xl font-extrabold mt-0.5 cursor-help" style="color:${KG_SCORE_COLORS[s.label] || "#6b6b6b"}" title="${escAttr(tip)}">${s.score == null ? "—" : esc(s.score)}</div>`;
}

function pickSuggestion(name) {
  document.getElementById("explore-seed-input").value = name;
  run();
}

function relationLabel(strategy) {
  return { supply_upstream: "供應鏈上游", supply_downstream: "供應鏈下游", substitute: "競爭對手", co_impact: "共同主題" }[strategy] || "";
}

function fVerifyDots(layer2) {
  if (!layer2 || layer2.status === "unavailable" || !layer2.f_score_max) return "";
  const total = layer2.f_score_max, pass = layer2.f_score;
  let dots = "";
  for (let i = 0; i < total; i++) {
    dots += `<span class="dot ${i < pass ? "bg-[#2e7d32]" : "bg-outline-variant"}"></span>`;
  }
  return dots;
}

// 路徑上相鄰兩家公司之間的箭頭：供應鏈上游「A ← B」＝B 供應 A，下游「A → B」＝A 供應 B，其他關係沒有方向
function pathArrow(strategy) {
  return { supply_upstream: "←", supply_downstream: "→" }[strategy] || "—";
}

// 間接標的 = 跟查詢標的之間隔了別家公司（路徑超過一步）
function isIndirect(c) {
  return (c.hops || 1) > 1;
}

// 「聯發科 ← 台積電 ← 帆宣」：查詢標的淺青、中間公司灰、標的本身深色
function pathChipsHtml(c) {
  const path = c.path && c.path.length ? c.path : [lastData.seed, c.node];
  const arrow = `<span class="text-outline text-sm">${pathArrow(lastData.strategy)}</span>`;
  return path.map((n, i) => {
    const cls = i === 0 ? "bg-[#e1f2f5] text-secondary"
      : (i === path.length - 1 ? "bg-tertiary-container text-on-tertiary font-semibold" : "bg-surface-container text-on-surface-variant");
    return (i > 0 ? arrow : "") + `<span class="text-[13px] px-2.5 py-0.5 rounded-full ${cls}">${esc(n)}</span>`;
  }).join("");
}

// 間接標的的次數、集數是整條路徑累計的，在數字下面補一行小字，跟財務驗證的圓點同一列
function indirectNoteHtml(c) {
  return isIndirect(c)
    ? `<div class="h-4 mt-1.5 flex items-center justify-center text-xs text-outline">(間接提及)</div>` : "";
}

function cardHtml(c, idx) {
  const layer2 = c.layer2;
  const ticker = layer2 && layer2.ticker ? layer2.ticker : "";
  const fLabel = (!layer2 || layer2.status === "unavailable" || !layer2.f_score_max)
    ? "無法評估" : `${layer2.f_score} / ${layer2.f_score_max} 通過`;
  const combinedLabel = layer2 && layer2.combined_label;

  const rawReasons = (c.path_reasons || []).flatMap(seg => seg.reasons || []);
  // 間接標的：路徑每一段各取一條，才看得到「標的怎麼接到查詢標的」；直接標的維持前兩條
  const bulletReasons = isIndirect(c)
    ? (c.path_reasons || []).map(seg => (seg.reasons || [])[0]).filter(Boolean).slice(0, 3)
    : rawReasons.slice(0, 2);
  const indirect = isIndirect(c);

  return `
  <div class="bg-surface-container-lowest border border-outline-variant/40 @container rounded-xl p-5 flex flex-col hover:border-tertiary-container/50 transition-colors cursor-pointer" onclick="Explore.openDetail(${idx})">
    <div class="flex items-center gap-2.5 flex-wrap mb-3">
      <span class="w-10 h-10 flex items-center justify-center rounded-full bg-tertiary-container text-on-tertiary text-lg font-bold flex-shrink-0">${idx + 1}</span>
      ${ticker ? `<span class="text-lg text-on-surface-variant font-semibold">${esc(ticker)}</span>` : ""}
      ${ticker && combinedLabel ? `<span class="hidden @[360px]:block w-px h-5 bg-outline-variant"></span>` : ""}
      ${combinedLabel ? `<span class="text-sm px-3 py-1 rounded-full bg-[#fdf0dc] text-[#8d4f00] font-bold whitespace-nowrap">${esc(combinedLabel)}</span>` : ""}
    </div>
    <div class="flex items-start justify-between gap-3 ${indirect ? "mb-3" : "mb-4"}">
      <div class="font-headline font-extrabold text-2xl text-on-surface">${esc(c.node)}</div>
      <div id="kg-score-${idx}" class="text-right leading-tight flex-shrink-0">
        <div class="text-xs text-on-surface-variant">Podcast 看法</div>
        <div class="text-sm text-on-surface-variant mt-0.5">計算中…</div>
      </div>
    </div>
    ${indirect ? `
      <div class="flex items-center gap-1.5 flex-wrap mb-4">
        <span class="text-xs text-on-surface-variant mr-0.5">間接相連</span>${pathChipsHtml(c)}
      </div>` : ""}

    <div class="grid grid-cols-2 text-center rounded-2xl bg-[#f0f5f5] py-4 px-1 mb-4">
      <div class="px-0.5">
        <div class="text-sm text-on-surface-variant">Podcast 數</div>
        <div class="text-base sm:text-lg font-bold text-on-surface mt-1 whitespace-nowrap">${c.source_diversity ?? 0} 個</div>
        ${indirectNoteHtml(c)}
      </div>
      <div class="px-0.5 border-l border-outline-variant/50">
        <div class="text-sm text-on-surface-variant">財務驗證</div>
        <div class="text-base sm:text-lg font-bold text-on-surface mt-1 whitespace-nowrap">${esc(fLabel)}</div>
        <div class="h-4 mt-1.5 flex gap-1.5 items-center justify-center">${fVerifyDots(layer2)}</div>
      </div>
    </div>

    ${bulletReasons.length ? `
      <div class="mb-4">
        <div class="text-sm text-on-surface-variant font-semibold mb-2">被提及的主要理由</div>
        <ul class="text-base text-on-surface space-y-1.5 pl-4">
          ${bulletReasons.map(r => `<li class="flex gap-2.5 leading-snug"><span class="w-1.5 h-1.5 mt-2 rounded-full bg-on-surface flex-shrink-0"></span><span>${esc(r)}</span></li>`).join("")}
        </ul>
      </div>
    ` : ""}

    <div class="mt-auto flex items-center justify-between px-5 py-4 rounded-2xl bg-[#f0f5f5] text-secondary font-bold text-lg">
      查看詳情
      <span class="material-symbols-outlined text-2xl">chevron_right</span>
    </div>
  </div>`;
}

// 詳情面板「每一段關係的理由與出處」：一條原始提及 = 一句理由 + 它出自哪一集（可點，新分頁開該集摘要）
const SEG_VISIBLE_ROWS = 5;

function segmentRowHtml(r) {
  const title = [r.show_name, r.episode_title].filter(Boolean).join(" · ") || "（來源不詳）";
  const episode = r.summary_id
    ? `<a href="/?open_summary=${Number(r.summary_id)}" target="_blank" rel="noopener" title="${escAttr(title)}" class="flex items-center gap-1.5 text-sm text-secondary hover:opacity-80 min-w-0">
         <span class="material-symbols-outlined text-base flex-shrink-0">headphones</span>
         <span class="truncate underline underline-offset-2">${esc(title)}</span>
         <span class="material-symbols-outlined text-sm flex-shrink-0">open_in_new</span>
       </a>`
    : `<div class="flex items-center gap-1.5 text-sm text-outline min-w-0" title="摘要庫裡沒有這一集">
         <span class="material-symbols-outlined text-base flex-shrink-0">headphones</span>
         <span class="truncate">${esc(title)}</span>
       </div>`;
  return `
    <div class="py-3 border-t border-outline-variant/30 first:border-t-0 first:pt-0">
      <div class="text-base text-on-surface leading-snug mb-1">${esc(r.reason || "（無描述）")}</div>
      ${episode}
      <div class="text-xs text-outline mt-0.5 pl-6">${esc(r.summary_date || "")}</div>
    </div>`;
}

function connectionsHtml(c) {
  const segs = (c.path_reasons || []).filter(seg => (seg.rows || []).length);
  if (!segs.length) return "";
  const arrow = pathArrow(lastData.strategy);
  const blocks = segs.map((seg, si) => {
    const rows = seg.rows;
    const hidden = rows.slice(SEG_VISIBLE_ROWS);
    const capNote = seg.mention_count > rows.length
      ? `<div class="text-xs text-outline mt-2">共 ${seg.mention_count} 次，這裡列出最近 ${rows.length} 筆</div>` : "";
    return `
      <div class="border border-outline-variant/40 rounded-2xl p-4 mb-3">
        <div class="flex items-center justify-between gap-2 flex-wrap mb-3">
          <span class="text-sm px-3 py-0.5 rounded-full bg-[#e1f2f5] text-secondary font-semibold">${esc(seg.from)} ${arrow} ${esc(seg.to)}</span>
          <span class="text-sm text-on-surface-variant whitespace-nowrap">${seg.mention_count} 次 · ${seg.source_diversity} 集</span>
        </div>
        ${rows.slice(0, SEG_VISIBLE_ROWS).map(segmentRowHtml).join("")}
        ${hidden.length ? `
          <div id="seg-more-${si}" class="hidden">${hidden.map(segmentRowHtml).join("")}</div>
          <button type="button" onclick="Explore.toggleSegMore(${si}, ${hidden.length})" class="mt-1 text-sm text-secondary font-semibold flex items-center gap-1">
            <span id="seg-more-label-${si}">展開其餘 ${hidden.length} 筆</span>
            <span id="seg-more-icon-${si}" class="material-symbols-outlined text-base">expand_more</span>
          </button>` : ""}
        ${capNote}
      </div>`;
  }).join("");
  return `
    <div class="font-headline font-bold text-base text-on-surface mb-1">每一段關係的理由與出處</div>
    <div class="text-xs text-on-surface-variant mb-3">集數可點擊，會在新分頁開啟該集摘要</div>
    ${blocks}`;
}

function toggleSegMore(si, n) {
  const open = document.getElementById("seg-more-" + si).classList.toggle("hidden") === false;
  document.getElementById("seg-more-label-" + si).textContent = open ? "收合" : `展開其餘 ${n} 筆`;
  document.getElementById("seg-more-icon-" + si).textContent = open ? "expand_less" : "expand_more";
}

const CHECK_DISPLAY_ORDER = [
  "營業利益為正", "營收年增為正",
  "EPS年增為正", "EPS環比增加",
  "三年營收CAGR為正",
  "毛利率較去年同期未惡化",
  "負債比率未惡化", "資產週轉率未惡化",
];

function openDetail(idx) {
  const c = lastData.basket[idx];
  const layer2 = c.layer2;
  const ticker = layer2 && layer2.ticker ? layer2.ticker : "-";

  let financeSection = "";
  if (!layer2 || layer2.status === "unavailable") {
    financeSection = `<div class="text-base text-on-surface-variant bg-surface-container-low rounded-lg p-4">財務資料無法評估，可能是海外標的資料源未涵蓋，或找不到對應股票代號。</div>`;
  } else {
    const details = [...(layer2.f_score_details || [])].sort((a, b) => {
      if (a.pass !== b.pass) return a.pass ? -1 : 1;
      const rankA = CHECK_DISPLAY_ORDER.indexOf(a.name);
      const rankB = CHECK_DISPLAY_ORDER.indexOf(b.name);
      return rankA - rankB;
    });
    const checklistItems = details.map(row => `
      <div class="flex items-center gap-1.5 py-1.5">
        <span class="material-symbols-outlined text-lg flex-shrink-0 ${row.pass ? "text-[#2e7d32]" : "text-error"}">${row.pass ? "check_circle" : "cancel"}</span>
        <span class="text-sm text-on-surface leading-snug">${esc(row.name)}</span>
      </div>`).join("");

    const explanationRows = details.map(row => `
      <div class="flex items-start gap-1.5">
        <span class="material-symbols-outlined text-base flex-shrink-0 mt-0.5 ${row.pass ? "text-[#2e7d32]" : "text-error"}">${row.pass ? "check_circle" : "cancel"}</span>
        <div>
          <span class="font-semibold text-on-surface">${esc(row.name)}</span>
          <span class="text-on-surface-variant">（實際值 ${esc(row.actual ?? "-")}，判斷標準 ${esc(row.threshold)}）</span>
          ${row.explanation ? `<div class="mt-0.5">${esc(row.explanation)}</div>` : ""}
        </div>
      </div>`).join("");

    financeSection = `
      <div class="font-headline font-bold text-base text-on-surface mb-2">財務體質驗證（${layer2.f_score}/${layer2.f_score_max} 項通過）</div>
      <div class="grid grid-cols-2 gap-x-3 gap-y-1">
        ${checklistItems}
      </div>
      <div class="flex items-center justify-end gap-1 mt-2 text-sm text-secondary font-semibold cursor-pointer" onclick="Explore.toggleCheckExplanations()">
        查看指標解釋 <span id="explore-check-explanations-icon" class="material-symbols-outlined text-base">expand_more</span>
      </div>
      <div id="explore-check-explanations" class="hidden mt-2 text-[13px] text-on-surface-variant bg-surface-container-low rounded-lg p-3 space-y-2">
        ${explanationRows}
      </div>
      <div class="text-xs text-on-surface-variant mt-3 space-y-0.5">
        <div>資料期間：${esc(layer2.fiscal_period_end || "-")}</div>
        <div>財務資料來源：${layer2.data_source === "yfinance" ? "Yahoo Finance" : (layer2.data_source === "finmind" ? "公司財報（FinMind）" : "-")}</div>
      </div>
    `;
  }

  const rewardSection = rewardSectionHtml(layer2);

  const body = document.getElementById("explore-detail-body");
  body.innerHTML = `
    <div class="flex items-start justify-between mb-5">
      <div class="flex items-center gap-2">
        <span class="w-7 h-7 flex items-center justify-center rounded-full bg-tertiary-container text-on-tertiary text-base font-bold">${idx + 1}</span>
        <span class="text-base text-on-surface-variant font-medium">${esc(ticker)}</span>
      </div>
      <button onclick="Explore.closeDetail()" class="w-8 h-8 flex items-center justify-center rounded-full hover:bg-surface-container transition-colors">
        <span class="material-symbols-outlined text-outline">close</span>
      </button>
    </div>

    <div class="font-headline font-extrabold text-2xl text-on-surface mb-2">${esc(c.node)}</div>
    <span class="inline-block text-[13px] px-2.5 py-0.5 rounded-full bg-secondary-container text-on-secondary-container font-medium mb-4">${relationLabel(lastData.strategy)}</span>

    <div class="grid grid-cols-2 gap-2 text-center border-y border-outline-variant/30 py-3 mb-4">
      <div>
        <div class="text-xs text-on-surface-variant">Podcast 數</div>
        <div class="text-lg font-bold text-on-surface mt-0.5">${c.source_diversity ?? 0} 個</div>
        ${indirectNoteHtml(c)}
      </div>
      <div>
        <div class="text-xs text-on-surface-variant">財務驗證</div>
        <div class="text-lg font-bold text-on-surface mt-0.5">${layer2 && layer2.f_score_max ? `${layer2.f_score}/${layer2.f_score_max}` : "無法評估"}</div>
        <div class="h-4 mt-1.5 flex gap-1 items-center justify-center">${fVerifyDots(layer2)}</div>
      </div>
    </div>

    <div class="text-sm font-semibold text-on-surface-variant mb-1.5">路徑</div>
    <div class="flex items-center flex-wrap gap-1.5 mb-4">${pathChipsHtml(c)}</div>

    <div class="text-sm font-semibold text-on-surface-variant mb-1.5">被提及的主要理由</div>
    <div class="text-base text-on-surface leading-relaxed mb-2">${esc(c.synthesized_reason || "查無具體描述，僅圖譜路徑相連")}</div>
    <div class="mb-5"></div>

    ${connectionsHtml(c)}

    <div class="mb-3"></div>

    ${financeSection}

    ${rewardSection}

    <button class="w-full mt-6 py-3 rounded-lg border border-outline-variant text-on-surface text-base font-semibold flex items-center justify-center gap-2 hover:bg-surface-container transition-colors">
      <span class="material-symbols-outlined text-xl">bookmark_border</span>加入觀察清單
    </button>
  `;

  document.getElementById("explore-detail-overlay").classList.remove("hidden");
  const panel = document.getElementById("explore-detail-panel");
  panel.classList.remove("translate-x-full");
}

const GROWTH_METRIC_INFO = [
  { label: "營收年增", key: "revenue_yoy_pct", icon: "receipt_long", note: "反映生意規模最近有沒有在擴大，是最直接但也最短期的成長訊號" },
  { label: "EPS年增", key: "eps_yoy_pct", icon: "payments", note: "股東實際分得的獲利有沒有變多，比營收成長更貼近股東，但單季數字波動也較大" },
  { label: "三年CAGR", key: "cagr_3y_pct", icon: "bar_chart", note: "拉長時間看整體年化成長，確認是否有結構性延續，不是曇花一現" },
];

function fmtPct(v) {
  return v != null ? `${v > 0 ? "+" : ""}${v}%` : "無法評估";
}

const GROWTH_BADGE_COLORS = {
  "成長強勁": "bg-[#e3f2e3] text-[#2e7d32]",
  "成長溫和": "bg-[#fdf0dc] text-[#8d4f00]",
  "成長有限": "bg-surface-container text-on-surface-variant",
};
const VALUATION_BADGE_COLORS = {
  "相對偏高": "bg-[#fdf0dc] text-[#8d4f00]",
  "相對合理": "bg-surface-container text-on-surface-variant",
  "相對偏低": "bg-[#e3f2e3] text-[#2e7d32]",
};

function trendSparkSvg(direction) {
  // 純裝飾用的示意走勢，不是根據歷史季度數值畫出來的真實 sparkline（跟之前
  // 討論過的一致：只需要方向感，不需要為此多傳歷史原始數值到前端）。
  const color = direction === "up" ? "#2e7d32" : direction === "down" ? "#ba1a1a" : "#717879";
  const path = direction === "up" ? "M2,26 L14,20 L24,24 L36,12 L48,16 L60,4"
    : direction === "down" ? "M2,4 L14,10 L24,6 L36,18 L48,14 L60,26"
    : "M2,15 L60,15";
  const dotY = direction === "up" ? 4 : direction === "down" ? 26 : 15;
  return `<svg width="52" height="26" viewBox="0 0 62 30" fill="none" class="flex-shrink-0">
    <path d="${path}" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="60" cy="${dotY}" r="3.5" fill="${color}"/>
  </svg>`;
}

function growthRowHtml(m, value, isFirst) {
  const known = value != null;
  const direction = known ? (value > 0 ? "up" : value < 0 ? "down" : "flat") : "flat";
  const valueColor = known ? (value > 0 ? "text-[#2e7d32]" : value < 0 ? "text-error" : "text-on-surface") : "text-on-surface-variant";
  return `
    <div class="flex items-center gap-2.5 md:gap-3 py-2.5 md:py-3 ${isFirst ? "" : "border-t border-outline-variant/30"}" title="${esc(m.note)}">
      <span class="w-9 h-9 md:w-11 md:h-11 flex-shrink-0 flex items-center justify-center rounded-full bg-[#e3f2e3]">
        <span class="material-symbols-outlined text-xl md:text-2xl text-[#2e7d32]">${m.icon}</span>
      </span>
      <div class="flex-1 min-w-0">
        <div class="text-xs md:text-sm text-on-surface-variant">${m.label}</div>
        <div class="text-base md:text-xl font-bold ${valueColor} whitespace-nowrap">${fmtPct(value)}</div>
      </div>
      ${trendSparkSvg(direction)}
    </div>`;
}

function valuationGaugeSvg(layer2) {
  const changePct = layer2.valuation_change_pct;
  const clamped = changePct == null ? 0 : Math.max(-30, Math.min(30, changePct));
  const rotation = (clamped / 30) * 90;
  return `
    <svg viewBox="0 0 200 128" class="w-full max-w-[210px] md:max-w-[300px] mx-auto block">
      <defs>
        <linearGradient id="valGaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#8fbf9f" />
          <stop offset="50%" stop-color="#ecdca3" />
          <stop offset="100%" stop-color="#e3a06a" />
        </linearGradient>
      </defs>
      <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="url(#valGaugeGrad)" stroke-width="12" stroke-linecap="round" />
      <line x1="100" y1="14" x2="100" y2="26" stroke="#1c1c16" stroke-width="2" />
      <polygon points="96,101 104,101 100,32" fill="#1c1c16" transform="rotate(${rotation} 100 100)" />
      <circle cx="100" cy="100" r="4.5" fill="#1c1c16" />
      <text x="20" y="121" font-size="11" fill="#5c8f6e" text-anchor="middle">偏低</text>
      <text x="100" y="10" font-size="11" fill="#414849" text-anchor="middle">合理（歷史平均P/S）</text>
      <text x="180" y="121" font-size="11" fill="#b5793f" text-anchor="middle">偏高</text>
    </svg>`;
}

function growthFeatureCardHtml(layer2) {
  const rows = GROWTH_METRIC_INFO.map((m, i) => growthRowHtml(m, layer2[m.key], i === 0)).join("");
  const badgeCls = GROWTH_BADGE_COLORS[layer2.growth_label] || "bg-surface-container text-on-surface-variant";
  return `
    <div class="h-full flex flex-col bg-surface-container-lowest border border-outline-variant/30 rounded-xl p-3 md:p-5">
      <div class="flex items-center justify-between mb-1 md:mb-2">
        <div class="text-sm md:text-lg font-bold text-on-surface">成長特徵</div>
        ${layer2.growth_label ? `<span class="text-xs md:text-sm px-2 py-0.5 md:px-3 md:py-1 rounded-full font-semibold ${badgeCls}">${esc(layer2.growth_label)}</span>` : ""}
      </div>
      <div class="flex-1 flex flex-col justify-evenly">
        ${rows}
      </div>
    </div>`;
}

function valuationFeatureCardHtml(layer2) {
  const badgeCls = VALUATION_BADGE_COLORS[layer2.valuation_label] || "bg-surface-container text-on-surface-variant";
  return `
    <div class="bg-surface-container-lowest border border-outline-variant/30 rounded-xl p-3 md:p-5">
      <div class="flex items-center justify-between mb-2 md:mb-4">
        <div class="text-sm md:text-lg font-bold text-on-surface">估值特徵</div>
        ${layer2.valuation_label ? `<span class="text-xs md:text-sm px-2 py-0.5 md:px-3 md:py-1 rounded-full font-semibold ${badgeCls}">${esc(layer2.valuation_label)}</span>` : ""}
      </div>
      ${valuationGaugeSvg(layer2)}
      <div class="grid grid-cols-3 gap-1.5 md:gap-2 mt-2 md:mt-4 text-center">
        <div class="bg-surface-container-low rounded-[14px] py-2 px-1 md:py-3 md:px-1.5">
          <div class="text-xs md:text-xs text-on-surface-variant whitespace-nowrap">目前P/S</div>
          <div class="text-sm md:text-xl font-bold text-on-surface mt-0.5 whitespace-nowrap">${layer2.current_ps != null ? layer2.current_ps + "x" : "-"}</div>
        </div>
        <div class="bg-surface-container-low rounded-[14px] py-2 px-1 md:py-3 md:px-1.5">
          <div class="text-xs md:text-xs text-on-surface-variant whitespace-nowrap">歷史平均P/S</div>
          <div class="text-sm md:text-xl font-bold text-on-surface mt-0.5 whitespace-nowrap">${layer2.avg_ps != null ? layer2.avg_ps + "x" : "-"}</div>
        </div>
        <div class="bg-surface-container-low rounded-[14px] py-2 px-1 md:py-3 md:px-1.5">
          <div class="text-xs md:text-xs text-on-surface-variant whitespace-nowrap">相對歷史平均</div>
          <div class="text-sm md:text-xl font-bold text-on-surface mt-0.5 whitespace-nowrap">${fmtPct(layer2.valuation_change_pct)}</div>
        </div>
      </div>
      ${layer2.valuation_note ? `<div class="text-xs md:text-sm text-on-surface-variant mt-2 md:mt-3 leading-snug">${esc(layer2.valuation_note)}</div>` : ""}
    </div>`;
}

function rewardSectionHtml(layer2) {
  if (!layer2 || layer2.status === "unavailable") {
    return `
      <div class="mt-5 pt-4 border-t border-outline-variant/30">
        <div class="font-headline font-bold text-base text-on-surface mb-2">成長與估值特徵</div>
        <div class="text-sm text-on-surface-variant bg-surface-container-low rounded-lg p-3">目前無法評估（可能缺股價資料），不影響財務驗證結果。</div>
      </div>`;
  }

  return `
    <div class="mt-5 pt-4 border-t border-outline-variant/30">
      <div class="font-headline font-bold text-base md:text-lg text-on-surface mb-1">成長與估值特徵</div>
      <div class="text-xs md:text-sm text-on-surface-variant mb-3">跟上面的財務驗證是同一組數字的不同呈現方式，不代表股價預期報酬或投資建議。</div>
      <div class="grid grid-cols-2 gap-3 md:gap-5">
        ${growthFeatureCardHtml(layer2)}
        ${valuationFeatureCardHtml(layer2)}
      </div>
    </div>`;
}

function toggleCheckExplanations() {
  document.getElementById("explore-check-explanations").classList.toggle("hidden");
}

function closeDetail() {
  document.getElementById("explore-detail-overlay").classList.add("hidden");
  document.getElementById("explore-detail-panel").classList.add("translate-x-full");
}

function setLoading(on) {
  document.getElementById("explore-run-btn").disabled = on;
  document.getElementById("explore-progress-panel").classList.toggle("hidden", !on);
}
function showError(msg) {
  const el = document.getElementById("explore-error");
  el.textContent = msg; el.classList.remove("hidden");
}
function hideError() {
  document.getElementById("explore-error").classList.add("hidden");
}
function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
function escAttr(s) { return esc(s).replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }

window.Explore = {
  onShow, openWithSeed, showAssetsList,
  onSeedInput, closeSeedDropdown, clearSeed, pickSeedSuggestion, pickSuggestion, run,
  openDetail, closeDetail, toggleSegMore, toggleCheckExplanations,
};
})();
