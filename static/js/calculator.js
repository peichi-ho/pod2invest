// ── Calculator state ──────────────────────────────────────────
let riskMultiplier  = 1.0;
let stockPeriod     = '1y';
let stockCurrentPrice = 0;
let simUnit         = 'year';
let _chartDebounce  = null;
let _hostYield      = null;
let _hostCalcData   = null;
let _newsCache      = [];
let _bullUserEdited = false;
let _bearUserEdited = false;
let _episodeNodes    = [];
let _selectedScoreId = null;
let _scenarioMode    = 'single';   // 'single' | 'weighted'
let _scenarioMeta    = null;       // 最近一次 /scenario/ or /scenario-weighted/ 回傳，含 annual_vol 供 GBM 用
let _pendingOriginEpisodeId = null; // 從單集摘要頁「試算」按鈕過來時，記住是哪一集，選集清單要預選同一集
                                     // 用 episode_id 不是 summary_id：同一集的 pro/novice 是兩筆不同 summary_id，
                                     // 但 stock-timeline 只列 pro，用 episode_id 才能不管使用者當下看的是哪個 mode 都能配對到
let _currentTicker = '';            // 目前已查詢解析出的 ticker（給「查看走勢圖與新聞」按鈕跳轉用）
let _lastResolvedInputText = null;  // 股票代號輸入框最後一次成功查詢時的原始文字，判斷試算時要不要先補查
let _stockInfoInFlight = null;      // { forInput, promise }：目前正在跑的查詢，避免 blur/Enter/試算同時觸發重複打 API
let _pendingCandidates = [];        // 輸入模糊時（例如「電子」）後端回傳的候選股票清單，等使用者選一個

function _initSimDates() {
  const today      = new Date();
  const oneYearLater = new Date(today);
  oneYearLater.setFullYear(today.getFullYear() + 1);
  document.getElementById('sim-start-date').value = today.toISOString().slice(0, 10);
  document.getElementById('sim-end-date').value   = oneYearLater.toISOString().slice(0, 10);
}

_initSimDates();

// ── Host calc badge helpers ───────────────────────────────────
function _checkHostYieldDiff() {
  if (_hostYield == null) return;
  const cur = parseFloat(document.getElementById('sim-yield-base').value);
  const btn = document.getElementById('host-yield-reset');
  if (Math.abs(cur - _hostYield) > 0.01) btn.classList.remove('hidden');
  else btn.classList.add('hidden');
}

function resetToHostYield() {
  if (_hostYield == null) return;
  document.getElementById('sim-yield-base').value = _hostYield;
  _bullUserEdited = false;
  _bearUserEdited = false;
  onBaseYieldChange();
  document.getElementById('host-yield-reset').classList.add('hidden');
}

// ── Scenario derive helpers ───────────────────────────────────
function _deriveScenarios(base) {
  if (base === 0) return { bull: 5, bear: -5 };
  if (base > 0)   return { bull: +(base * 1.5).toFixed(1), bear: +(base * 0.5).toFixed(1) };
  return           { bull: +(base * 0.5).toFixed(1), bear: +(base * 1.5).toFixed(1) };
}

function onBaseYieldChange() {
  const base = parseFloat(document.getElementById('sim-yield-base').value);
  if (isNaN(base)) return;
  const { bull, bear } = _deriveScenarios(base);
  if (!_bullUserEdited) document.getElementById('sim-yield-bull').value = bull;
  if (!_bearUserEdited) document.getElementById('sim-yield-bear').value = bear;
  _refreshIfActive();
}

function _refreshIfActive() {
  if (!document.getElementById('results-section').classList.contains('hidden')) {
    calcWealth();
  }
}

// ── Episode scenario (podcast-based GBM) ──────────────────────
function _macroLabel(v) {
  if (v > 0.33)  return '🟢樂觀';
  if (v < -0.33) return '🔴悲觀';
  return '⚪中性';
}
function _riskLabel(v) {
  if (v >= 0.75) return '高風險';
  if (v >= 0.25) return '中風險';
  return '低風險';
}

// 加權模式下，選單只顯示「真的會被加權到」的集數：日期 ≥ 目前選中這集（跟後端
// compute_time_weighted_scores 的 eligible 篩選邏輯一致，純前端算，不用多打API）。
function _eligibleWeightedNodes(anchorScoreId) {
  const anchor = _episodeNodes.find(n => String(n.score_id) === String(anchorScoreId));
  if (!anchor) return [];
  return _episodeNodes.filter(n => n.published_at >= anchor.published_at);
}

function _populateEpisodeSelect() {
  const sel = document.getElementById('episode-select');
  const nodes = _scenarioMode === 'weighted'
    ? _eligibleWeightedNodes(_selectedScoreId)
    : _episodeNodes;
  sel.innerHTML = nodes.map(n => {
    const podcaster = n.podcaster ? `・${n.podcaster}` : '';
    return `<option value="${n.score_id}">${n.published_at}${podcaster}　${_macroLabel(n.macro_score)}／${_riskLabel(n.risk_score)}</option>`;
  }).join('');
  sel.value = _selectedScoreId;
}

// 「綜合觀點」按鈕本身的啟用/停用＋樣式，跟目前選中集數是否還有其他更新集數可加權有關。
function _refreshScenarioModeButtons() {
  const weightedDisabled = _eligibleWeightedNodes(_selectedScoreId).length <= 1;
  document.querySelectorAll('.scenario-mode-btn').forEach(btn => {
    const mode = btn.dataset.scenarioMode;
    const isDisabled = mode === 'weighted' && weightedDisabled;
    btn.disabled = isDisabled;
    btn.title = isDisabled ? '這已經是最新一集，沒有其他集數可以加權' : '';
    btn.className = isDisabled
      ? 'scenario-mode-btn px-3 py-1 rounded-full font-label text-[11px] font-bold transition-all text-secondary/30 cursor-not-allowed'
      : (mode === _scenarioMode
          ? 'scenario-mode-btn px-3 py-1 rounded-full font-label text-[11px] font-bold transition-all bg-tertiary-container text-white'
          : 'scenario-mode-btn px-3 py-1 rounded-full font-label text-[11px] font-bold transition-all text-secondary');
  });
}

function _sortEpisodeNodes(nodes) {
  return nodes.sort((a, b) => b.published_at.localeCompare(a.published_at) || b.score_id - a.score_id);
}

async function fetchEpisodeTimeline(ticker, preferredEpisodeId) {
  const wrap = document.getElementById('episode-scenario-wrap');
  _episodeNodes = [];
  _selectedScoreId = null;
  _scenarioMeta = null;
  wrap.classList.add('hidden');
  try {
    const res  = await fetch(`/api/calculator/stock-timeline/?ticker=${encodeURIComponent(ticker)}`);
    const data = await res.json();
    _episodeNodes = _sortEpisodeNodes((res.ok && data.nodes) ? data.nodes.slice() : []);

    let preferredNode = preferredEpisodeId != null
      ? _episodeNodes.find(n => n.episode_id != null && String(n.episode_id) === String(preferredEpisodeId))
      : null;
    let usedOnDemand = false;

    // 從單集摘要頁「試算」過來，但那一集還沒被排進 timeline（可能還沒跑過批次分類）：
    // 當場算一次、存進資料庫，補進清單，而不是默默改用最新一集資料。
    if (preferredEpisodeId != null && !preferredNode) {
      try {
        const ensureRes  = await fetch(`/api/calculator/ensure-episode-score/?episode_id=${encodeURIComponent(preferredEpisodeId)}&ticker=${encodeURIComponent(ticker)}`);
        const ensureData = await ensureRes.json();
        if (ensureRes.ok) {
          preferredNode = ensureData;
          usedOnDemand = true;
          if (!_episodeNodes.some(n => n.score_id === ensureData.score_id)) {
            _episodeNodes = _sortEpisodeNodes([..._episodeNodes, ensureData]);
          }
        }
      } catch (e) {
        // 算不出來就照舊退回最新一集，下面的邏輯會處理
      }
    }

    if (!_episodeNodes.length) { wrap.classList.add('hidden'); return; }

    wrap.classList.remove('hidden');
    _scenarioMode = 'single';
    _selectedScoreId = (preferredNode || _episodeNodes[0]).score_id;
    _populateEpisodeSelect();
    _refreshScenarioModeButtons();
    await applyScenarioFromEpisode();

    if (preferredEpisodeId != null && !preferredNode) {
      const badge = document.getElementById('scenario-source-badge');
      const fallback = _episodeNodes[0];
      badge.innerHTML = `<div class="mb-1.5 px-2 py-1.5 rounded-lg border border-error/30 bg-error/5 text-[11px] text-error font-semibold">`
        + `⚠️ 你點的那一集沒有明確可歸因的個股討論，改顯示《${fallback.podcaster || '其他節目'}》${fallback.published_at} 的資料</div>`
        + badge.innerHTML;
    } else if (usedOnDemand) {
      const badge = document.getElementById('scenario-source-badge');
      badge.innerHTML = `✨ 這一集剛剛才即時算出分數（已存起來，之後查會直接秒開）<br>` + badge.innerHTML;
    }
  } catch (e) {
    wrap.classList.add('hidden');
  }
}

function setScenarioMode(mode) {
  if (mode === 'weighted' && _eligibleWeightedNodes(_selectedScoreId).length <= 1) return; // 按鈕本身會被 disable 擋掉，這裡多一層保險
  _scenarioMode = mode;
  _populateEpisodeSelect();
  _refreshScenarioModeButtons();
  applyScenarioFromEpisode();
}

function onEpisodeSelectChange() {
  _selectedScoreId = document.getElementById('episode-select').value;
  // 換到的這集在加權模式下已經沒有其他集數可加權（例如換到最新一集）：自動退回單集模式
  if (_scenarioMode === 'weighted' && _eligibleWeightedNodes(_selectedScoreId).length <= 1) {
    _scenarioMode = 'single';
  }
  _populateEpisodeSelect();
  _refreshScenarioModeButtons();
  applyScenarioFromEpisode();
}

// 個股專屬段落原文，預設截斷、可展開，避免把側邊欄撐太長
function _renderTopicExcerpt(text) {
  if (!text) return '';
  if (text.length <= 60) return `<div class="mt-1 text-outline/70">${text}</div>`;
  const excerptId = '_topic_excerpt_' + Math.random().toString(36).slice(2, 8);
  const short = text.slice(0, 60) + '…';
  return `
    <div class="mt-1">
      <span id="${excerptId}-short" class="text-outline/70">${short}</span>
      <span id="${excerptId}-full" class="hidden text-outline/70">${text}</span>
      <button type="button" onclick="_toggleTopicExcerpt('${excerptId}', this)" class="text-secondary font-bold text-[10px] ml-1 align-baseline hover:underline">展開</button>
    </div>`;
}

function _toggleTopicExcerpt(id, btn) {
  const shortEl = document.getElementById(id + '-short');
  const fullEl  = document.getElementById(id + '-full');
  const wasShort = !shortEl.classList.contains('hidden');
  shortEl.classList.toggle('hidden');
  fullEl.classList.toggle('hidden');
  btn.textContent = wasShort ? '收合' : '展開';
}

function _renderScenarioBadge(data) {
  const el = document.getElementById('scenario-source-badge');
  let html;
  if (_scenarioMode === 'weighted') {
    html = `綜合最近 ${data.n_episodes} 集觀點加權（半衰期90天，最新更新 ${data.latest_date}）<br>`
         + `總體：${_macroLabel(data.macro_score)}／風險：${_riskLabel(data.risk_score)}`;
    if (data.top_contributors && data.top_contributors.length) {
      const rows = data.top_contributors.map(c => {
        const pct = Math.round(c.weight * 100);
        const podcaster = c.podcaster ? `・${c.podcaster}` : '';
        return `<div class="pl-2">・${c.published_at}${podcaster}（權重 ${pct}%）</div>`;
      }).join('');
      html += `<br><span class="text-outline/70">主要依據集數：</span>${rows}`;
    }
  } else {
    html = `《${data.asset_name}》${data.published_at} 該集內容<br>總體：${_macroLabel(data.macro_score)}／風險：${_riskLabel(data.risk_score)}`;
    if (data.rationale) html += `<br><span class="text-outline/70">${data.rationale}</span>`;
    html += _renderTopicExcerpt(data.topic_summary);
    if (data.summary_id != null) {
      html += `<div class="mt-1"><button type="button" onclick="_backOverride='calculator'; openDeepDive(${data.summary_id})" class="text-secondary font-bold text-[11px] hover:underline">查看完整摘要 →</button></div>`;
    }
  }
  if (data.is_preliminary_calibration) {
    html += `<br><span class="text-[10px] text-outline/60">⚠️ 初步校準版本，樣本涵蓋期間較短，參數尚未最終定案</span>`;
  }
  el.innerHTML = html;
}

let _scenarioRequestSeq = 0; // 快速切換集數時，用來判斷回應是不是過期的（避免舊回應晚到蓋掉新畫面）

async function applyScenarioFromEpisode() {
  if (!_selectedScoreId) return;
  const seq = ++_scenarioRequestSeq;
  const badge = document.getElementById('scenario-source-badge');
  badge.textContent = '載入中...';
  const path = _scenarioMode === 'weighted' ? 'scenario-weighted' : 'scenario';
  try {
    const res  = await fetch(`/api/calculator/${path}/?score_id=${_selectedScoreId}`);
    const data = await res.json();
    if (seq !== _scenarioRequestSeq) return; // 等待期間使用者已經換選別集，這筆過期了，不要蓋掉畫面
    if (!res.ok) {
      badge.textContent = data.error || '讀取失敗';
      _scenarioMeta = null;
      return;
    }
    _scenarioMeta = data;
    _bullUserEdited = false;
    _bearUserEdited = false;
    document.getElementById('sim-yield-base').value = +(data.scenario_returns.base * 100).toFixed(1);
    document.getElementById('sim-yield-bull').value = +(data.scenario_returns.bull * 100).toFixed(1);
    document.getElementById('sim-yield-bear').value = +(data.scenario_returns.bear * 100).toFixed(1);
    _renderScenarioBadge(data);
    _refreshIfActive();
  } catch (e) {
    if (seq !== _scenarioRequestSeq) return;
    badge.textContent = '讀取失敗，請稍後再試';
    _scenarioMeta = null;
  }
}

function _checkHostCalcDiff() {
  if (!_hostCalcData) return;
  const ticker  = document.getElementById('sim-ticker').value.trim();
  const endDate = document.getElementById('sim-end-date').value;
  const changed = ticker !== _hostCalcData.ticker || endDate !== _hostCalcData.endDate;
  document.getElementById('host-calc-badge').style.display = changed ? 'flex' : 'none';
}

function resetToHostCalc() {
  if (!_hostCalcData) return;
  document.getElementById('sim-ticker').value   = _hostCalcData.ticker;
  document.getElementById('sim-end-date').value = _hostCalcData.endDate;
  document.getElementById('host-calc-badge').style.display = 'none';
  fetchStockInfo();
}

// ── Slider sync (capital only) ────────────────────────────────
function syncSlider(id) {
  const inputEl = document.getElementById('sim-' + id);
  const sliderEl = document.getElementById('slider-' + id);
  if (inputEl && sliderEl) sliderEl.value = inputEl.value;
}

function syncInput(id) {
  const sliderEl = document.getElementById('slider-' + id);
  const inputEl  = document.getElementById('sim-' + id);
  if (sliderEl && inputEl) inputEl.value = sliderEl.value;
}

// ── Wealth calculation ────────────────────────────────────────
function getSimYears() {
  const s = document.getElementById('sim-start-date').value;
  const e = document.getElementById('sim-end-date').value;
  if (!s || !e) return 1;
  return Math.max((new Date(e) - new Date(s)) / (365.25 * 24 * 3600 * 1000), 0);
}

function _periodLabel(years) {
  if (years <= 0) return '';
  const totalDays = Math.round(years * 365.25);
  const yr  = Math.floor(totalDays / 365);
  const rem = totalDays - yr * 365;
  const mo  = Math.floor(rem / 30);
  const day = rem - mo * 30;

  if (yr > 0) {
    if (mo > 0) return `${yr}年${mo}月`;
    return `${yr}年`;
  }
  if (mo > 0) {
    if (day > 0) return `${mo}月${day}天`;
    return `${mo}月`;
  }
  return `${totalDays}天`;
}

function toggleScenarioHint() {
  const box = document.getElementById('scenario-hint-box');
  if (box) box.classList.toggle('hidden');
  // Close when clicking outside
  if (!box.classList.contains('hidden')) {
    setTimeout(() => {
      document.addEventListener('click', function _close(e) {
        if (!document.getElementById('scenario-hint-wrap').contains(e.target)) {
          box.classList.add('hidden');
          document.removeEventListener('click', _close);
        }
      });
    }, 0);
  }
}

// ── 試算前欄位驗證 ──────────────────────────────────────────────
function _validateSimInputs() {
  const errors = [];
  const capital = parseFloat(document.getElementById('sim-capital').value);
  if (!capital || capital <= 0) {
    errors.push({ fieldId: 'sim-capital', message: '請輸入投入金額' });
  }
  const startDate = document.getElementById('sim-start-date').value;
  const endDate   = document.getElementById('sim-end-date').value;
  if (!startDate) errors.push({ fieldId: 'sim-start-date', message: '請選擇起始日期' });
  if (!endDate)   errors.push({ fieldId: 'sim-end-date', message: '請選擇結束日期' });
  if (startDate && endDate && new Date(endDate) <= new Date(startDate)) {
    errors.push({ fieldId: 'sim-end-date', message: '結束日期必須晚於起始日期' });
  }
  const baseRateVal = document.getElementById('sim-yield-base').value;
  if (baseRateVal === '' || isNaN(parseFloat(baseRateVal))) {
    errors.push({ fieldId: 'sim-yield-base', message: '請輸入基準情境年化報酬率（查詢一支有節目觀點資料的股票會自動帶入，也可以自己輸入）' });
  }
  return errors;
}

async function handleCalcClick() {
  const errEl  = document.getElementById('sim-validation-error');

  if (_pendingCandidates.length) {
    errEl.textContent = '請先從上面的候選清單選擇正確的股票';
    errEl.classList.remove('hidden');
    return;
  }

  // 拿掉查詢按鈕後的保底：如果股票代號還沒查過（或查的是別的字），先查完再算，
  // 使用者不需要知道背後有查詢這個步驟。
  const rawTicker = document.getElementById('sim-ticker').value.trim();
  if (rawTicker && rawTicker !== _lastResolvedInputText) {
    errEl.classList.add('hidden');
    await fetchStockInfo();
    if (_pendingCandidates.length) {
      errEl.textContent = '請先從上面的候選清單選擇正確的股票';
      errEl.classList.remove('hidden');
      return;
    }
  }

  const errors = _validateSimInputs();
  if (errors.length) {
    errEl.textContent = errors[0].message;
    errEl.classList.remove('hidden');
    const field = document.getElementById(errors[0].fieldId);
    if (field) field.focus();
    return;
  }
  errEl.classList.add('hidden');
  calcWealth();
  document.getElementById('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function calcWealth() {
  const capital  = parseFloat(document.getElementById('sim-capital').value) || 0;
  const baseRate = parseFloat(document.getElementById('sim-yield-base').value);
  const bullRate = parseFloat(document.getElementById('sim-yield-bull').value);
  const bearRate = parseFloat(document.getElementById('sim-yield-bear').value);
  const years    = getSimYears();
  const months   = Math.max(Math.round(years * 12), 1);
  const period   = _periodLabel(years);

  if (isNaN(baseRate)) return;

  // Shares & invested capital
  const shares    = (stockCurrentPrice > 0 && capital > 0) ? Math.floor(capital / stockCurrentPrice) : 0;
  const invested  = (stockCurrentPrice > 0 && shares > 0)  ? shares * stockCurrentPrice : capital;
  const remaining = Math.max(capital - invested, 0);

  // Compound interest: final = invested × (1 + annualRate%)^years
  const baseFinal  = invested * Math.pow(1 + baseRate / 100, years);
  const baseProfit = baseFinal - invested;
  const baseTotal  = invested > 0 ? (baseFinal / invested - 1) * 100 : 0;
  const sign       = v => v >= 0 ? '+' : '';
  const upColor    = 'text-[#286671]';
  const downColor  = 'text-[#ba1a1a]';
  const retColor   = baseRate >= 0 ? upColor : downColor;

  // Show results section
  document.getElementById('calc-results-placeholder').classList.add('hidden');
  document.getElementById('results-section').classList.remove('hidden');

  // 輸入條件
  document.getElementById('res-period').textContent        = period || '—';
  document.getElementById('res-annual-return').textContent = sign(baseRate) + baseRate.toFixed(1) + '% 年化（基準假設）';

  // 持倉資訊
  if (stockCurrentPrice > 0 && capital > 0) {
    document.getElementById('res-shares').textContent   = shares + ' 股';
    document.getElementById('res-invested').textContent = 'NT$' + Math.round(invested).toLocaleString();
    document.getElementById('res-remaining').textContent = 'NT$' + Math.round(remaining).toLocaleString();
  } else {
    document.getElementById('res-shares').textContent    = '—';
    document.getElementById('res-invested').textContent  = capital > 0 ? 'NT$' + Math.round(capital).toLocaleString() : '—';
    document.getElementById('res-remaining').textContent = '—';
  }

  // 試算結果
  document.getElementById('res-final-value').textContent = 'NT$' + Math.round(baseFinal).toLocaleString();

  const profitEl = document.getElementById('res-profit');
  profitEl.textContent = sign(baseProfit) + 'NT$' + Math.round(Math.abs(baseProfit)).toLocaleString();
  profitEl.className = 'text-sm font-bold ' + retColor;

  const returnEl = document.getElementById('res-total-return');
  returnEl.textContent = sign(baseTotal) + baseTotal.toFixed(2) + '%';
  returnEl.className = 'text-sm font-bold ' + retColor;

  const trendIcon = document.getElementById('res-trend-icon');
  if (trendIcon) trendIcon.textContent = baseRate >= 0 ? 'trending_up' : 'trending_down';

  const dispEl = document.getElementById('sim-period-display');
  if (dispEl) dispEl.textContent = period ? `投資期間：${period}` : '';

  const annualVol = _scenarioMeta ? _scenarioMeta.annual_vol : null;
  renderScenarioChart(invested, months, bullRate, baseRate, bearRate, annualVol);
}

function _fmtVal(v) {
  if (Math.abs(v) >= 100000000) return (v / 100000000).toFixed(1) + '億';
  if (Math.abs(v) >= 10000000)  return (v / 10000).toFixed(0) + '萬';
  if (Math.abs(v) >= 1000000)   return (v / 10000).toFixed(1) + '萬';
  if (Math.abs(v) >= 100000)    return (v / 10000).toFixed(1) + '萬';
  return Math.round(v).toLocaleString();
}

// Box-Muller 標準常態亂數
function _gaussRandom() {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function renderScenarioChart(invested, months, bullRate, baseRate, bearRate, annualVol) {
  const svg = document.getElementById('scenario-svg');
  svg.innerHTML = '';

  const W = 700, H = 260, padL = 72, padR = 24, padT = 16, padB = 40;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const ns = 'http://www.w3.org/2000/svg';

  // 平滑複利曲線（沒有波動率資料時的預設 fallback）
  function genPoints(annualRate) {
    if (annualRate == null || isNaN(annualRate)) return null;
    const mr = Math.pow(1 + annualRate / 100, 1 / 12) - 1;
    return Array.from({ length: months + 1 }, (_, n) => invested * Math.pow(1 + mr, n));
  }

  // GBM 模擬路徑：樂觀/基準/悲觀共用同一組隨機震盪，只有 drift 不同，避免運氣不好交錯
  function genGBMPoints(annualRate, shocks) {
    if (annualRate == null || isNaN(annualRate)) return null;
    const monthlyDrift = Math.log(1 + annualRate / 100) / 12;
    const monthlyVol   = annualVol / Math.sqrt(12);
    let price = invested;
    const row = [price];
    shocks.forEach(z => {
      price = price * Math.exp(monthlyDrift + monthlyVol * z - 0.5 * monthlyVol * monthlyVol);
      row.push(price);
    });
    return row;
  }

  // 基準情境獨立跑 N_PATHS 次算 10~90 百分位區間帶（誠實呈現不確定性），
  // 跟 apps/calculator/services/scenario.py 的 build_scenario_chart 同一套邏輯，
  // 只是這裡改用使用者自己選的投資期間（months），不是後端固定的12個月demo。
  function genBaseBand(annualRate, vol, nPaths) {
    if (annualRate == null || isNaN(annualRate)) return null;
    const paths = [];
    for (let i = 0; i < nPaths; i++) {
      paths.push(genGBMPoints(annualRate, Array.from({ length: months }, _gaussRandom)));
    }
    const low = [], high = [];
    for (let m = 0; m <= months; m++) {
      const col = paths.map(p => p[m]).sort((a, b) => a - b);
      const pct = p => {
        const idx = (col.length - 1) * p / 100;
        const lo2 = Math.floor(idx), hi2 = Math.ceil(idx);
        if (lo2 === hi2) return col[lo2];
        const frac = idx - lo2;
        return col[lo2] * (1 - frac) + col[hi2] * frac;
      };
      low.push(pct(10));
      high.push(pct(90));
    }
    return { low, high };
  }

  let bullPts, basePts, bearPts, band;
  if (annualVol && annualVol > 0) {
    const shocks = Array.from({ length: months }, _gaussRandom);
    bullPts = genGBMPoints(bullRate, shocks);
    basePts = genGBMPoints(baseRate, shocks);
    bearPts = genGBMPoints(bearRate, shocks);
    band = genBaseBand(baseRate, annualVol, 300);
  } else {
    bullPts = genPoints(bullRate);
    basePts = genPoints(baseRate);
    bearPts = genPoints(bearRate);
    band = null;
  }

  const allVals = [...(bullPts || []), ...basePts, ...(bearPts || []), ...(band ? band.low : []), ...(band ? band.high : [])];
  const dataMin = Math.min(...allVals);
  const dataMax = Math.max(...allVals);

  // Y-axis: 上限 = 樂觀情境最終值 +10%，下限 = 保守情境最終值 -10%
  // 複利曲線單調遞增/遞減，最終值即整條線的極值；若情境值為負則改用加法緩衝避免方向反轉
  const bullFinal = bullPts ? bullPts[months] : dataMax;
  const bearFinal = bearPts ? bearPts[months] : dataMin;
  const margin = (Math.abs(bullFinal) + Math.abs(bearFinal)) * 0.05 || invested * 0.1;
  let hi = bullFinal >= 0 ? bullFinal * 1.1 : bullFinal - margin;
  let lo = bearFinal >= 0 ? bearFinal * 0.9 : bearFinal + margin;
  // 保險：若資料範圍超出上述邊界（極端情況），擴張包住全部資料
  hi = Math.max(hi, dataMax);
  lo = Math.min(lo, dataMin);

  const toX = n => padL + (n / months) * (W - padL - padR);
  const toY = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  // Adaptive X-axis labels
  function getXLabels() {
    let interval, fmt;
    if (months < 3) {
      interval = 1;
      fmt = n => n === 0 ? '現在' : `第${n}月`;
    } else if (months <= 12) {
      interval = Math.ceil(months / 6);
      fmt = n => n === 0 ? '現在' : `第${n}月`;
    } else if (months <= 36) {
      interval = 3;
      fmt = n => n === 0 ? '現在' : `第${Math.round(n / 3)}季`;
    } else {
      interval = 6;
      fmt = n => n === 0 ? '現在' : `第${Math.round(n / 12)}年`;
    }
    const pts = [];
    for (let n = 0; n <= months; n += interval) pts.push({ n, label: fmt(n) });
    if (pts[pts.length - 1].n !== months) pts.push({ n: months, label: fmt(months) });
    return pts;
  }

  // Grid lines — nice ticks
  function niceStep(range, targetCount) {
    const rough = range / targetCount;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / mag;
    const nice = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10;
    return nice * mag;
  }
  const step = niceStep(hi - lo, 4);
  const tickStart = Math.ceil(lo / step) * step;
  const ticks = [];
  for (let v = tickStart; v <= hi + step * 0.01; v += step) {
    if (v >= lo - step * 0.01) ticks.push(Math.round(v));
  }
  ticks.forEach(val => {
    const y = toY(val);
    if (y < padT - 2 || y > H - padB + 2) return;
    const gl = document.createElementNS(ns, 'line');
    gl.setAttribute('x1', padL); gl.setAttribute('x2', W - padR);
    gl.setAttribute('y1', y);   gl.setAttribute('y2', y);
    gl.setAttribute('stroke', '#e5e3d9'); gl.setAttribute('stroke-width', '1');
    svg.appendChild(gl);
    const gt = document.createElementNS(ns, 'text');
    gt.setAttribute('x', padL - 6); gt.setAttribute('y', y + 4);
    gt.setAttribute('fill', '#717879'); gt.setAttribute('font-family', 'Manrope');
    gt.setAttribute('font-size', '11'); gt.setAttribute('font-weight', '600');
    gt.setAttribute('text-anchor', 'end');
    gt.textContent = _fmtVal(val);
    svg.appendChild(gt);
  });

  // 基準情境 10~90% 機率區間帶（灰底），畫在所有線的下面
  if (band) {
    let d = '';
    band.low.forEach((v, i) => { d += (i === 0 ? 'M' : 'L') + `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`; });
    for (let i = band.high.length - 1; i >= 0; i--) { d += ` L${toX(i).toFixed(1)},${toY(band.high[i]).toFixed(1)}`; }
    d += ' Z';
    const bandPath = document.createElementNS(ns, 'path');
    bandPath.setAttribute('d', d);
    bandPath.setAttribute('fill', '#113236');
    bandPath.setAttribute('fill-opacity', '0.08');
    bandPath.setAttribute('stroke', 'none');
    svg.appendChild(bandPath);
  }

  // Draw lines
  function drawLine(pts, color, dash) {
    if (!pts) return;
    let d = '';
    pts.forEach((v, i) => { d += (i === 0 ? 'M' : 'L') + `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`; });
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d); path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color); path.setAttribute('stroke-width', '2.5');
    path.setAttribute('stroke-linecap', 'round');
    if (dash) path.setAttribute('stroke-dasharray', dash);
    svg.appendChild(path);
  }

  drawLine(bearPts, '#ba1a1a', '6,3');
  drawLine(basePts, '#113236');
  drawLine(bullPts, '#286671', '6,3');

  // X-axis labels
  getXLabels().forEach(({ n, label }) => {
    const xt = document.createElementNS(ns, 'text');
    xt.setAttribute('x', toX(n).toFixed(1)); xt.setAttribute('y', H - padB + 16);
    xt.setAttribute('fill', '#717879'); xt.setAttribute('font-family', 'Manrope');
    xt.setAttribute('font-size', '11'); xt.setAttribute('font-weight', '600');
    xt.setAttribute('text-anchor', 'middle');
    xt.textContent = label;
    svg.appendChild(xt);
  });

  // Legend: show annual rate, not total return %
  const fmtRate = r => r == null || isNaN(r) ? '' : `(${r >= 0 ? '+' : ''}${r.toFixed(1)}% 年化)`;
  const bullEl = document.getElementById('legend-bull-rate');
  const baseEl = document.getElementById('legend-base-rate');
  const bearEl = document.getElementById('legend-bear-rate');
  if (bullEl) bullEl.textContent = fmtRate(bullRate);
  if (baseEl) baseEl.textContent = fmtRate(baseRate);
  if (bearEl) bearEl.textContent = fmtRate(bearRate);

  const bandRow = document.getElementById('legend-band-row');
  if (bandRow) bandRow.className = band
    ? 'flex items-center gap-2'
    : 'hidden items-center gap-2';

  // Range note: difference between bull and bear at end
  const rangeNote = document.getElementById('scenario-range-note');
  const rangeVal  = document.getElementById('scenario-range-val');
  if (rangeNote && rangeVal && bullPts && bearPts) {
    const diff = Math.abs(bullPts[months] - bearPts[months]);
    rangeVal.textContent = 'NT$' + Math.round(diff).toLocaleString();
    rangeNote.classList.remove('hidden');
  } else if (rangeNote) {
    rangeNote.classList.add('hidden');
  }

  // ── Hover tooltip ───────────────────────────────────────────
  const overlay = document.createElementNS(ns, 'g');
  overlay.setAttribute('id', 'chart-hover-overlay');

  // Invisible hit area
  const hitRect = document.createElementNS(ns, 'rect');
  hitRect.setAttribute('x', padL); hitRect.setAttribute('y', padT);
  hitRect.setAttribute('width', W - padL - padR); hitRect.setAttribute('height', H - padT - padB);
  hitRect.setAttribute('fill', 'transparent');
  overlay.appendChild(hitRect);

  // Crosshair vertical line
  const vLine = document.createElementNS(ns, 'line');
  vLine.setAttribute('stroke', '#9b9e9f'); vLine.setAttribute('stroke-width', '1');
  vLine.setAttribute('stroke-dasharray', '4,2');
  vLine.setAttribute('y1', padT); vLine.setAttribute('y2', H - padB);
  vLine.setAttribute('visibility', 'hidden');
  overlay.appendChild(vLine);

  // Tooltip box (foreignObject for HTML rendering)
  const fo = document.createElementNS(ns, 'foreignObject');
  fo.setAttribute('width', '160'); fo.setAttribute('height', '110');
  fo.setAttribute('visibility', 'hidden');
  const foDiv = document.createElement('div');
  foDiv.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
  foDiv.style.cssText = 'background:#2d3233;color:#e1e3e3;border-radius:8px;padding:8px 10px;font-size:11px;font-family:Manrope,sans-serif;line-height:1.6;pointer-events:none;box-shadow:0 2px 8px rgba(0,0,0,0.3)';
  fo.appendChild(foDiv);
  overlay.appendChild(fo);

  // Dot markers
  function makeDot(color) {
    const c = document.createElementNS(ns, 'circle');
    c.setAttribute('r', '4'); c.setAttribute('fill', color);
    c.setAttribute('stroke', '#fff'); c.setAttribute('stroke-width', '1.5');
    c.setAttribute('visibility', 'hidden');
    overlay.appendChild(c); return c;
  }
  const bullDot = bullPts ? makeDot('#286671') : null;
  const baseDot = makeDot('#113236');
  const bearDot = bearPts ? makeDot('#ba1a1a') : null;

  svg.appendChild(overlay);

  function getXLabel(n) {
    if (n === 0) return '現在';
    if (months <= 36) return `第 ${n} 月`;
    return `第 ${Math.round(n / 12)} 年`;
  }

  hitRect.addEventListener('mousemove', e => {
    const rect = svg.getBoundingClientRect();
    const scaleX = W / rect.width;
    const mx = (e.clientX - rect.left) * scaleX;
    const rawN = Math.round((mx - padL) / (W - padL - padR) * months);
    const n = Math.max(0, Math.min(months, rawN));
    const x = toX(n);

    vLine.setAttribute('x1', x); vLine.setAttribute('x2', x);
    vLine.setAttribute('visibility', 'visible');

    const bv  = basePts[n];
    const buv = bullPts ? bullPts[n] : null;
    const bev = bearPts ? bearPts[n] : null;

    if (baseDot) { baseDot.setAttribute('cx', x); baseDot.setAttribute('cy', toY(bv)); baseDot.setAttribute('visibility', 'visible'); }
    if (bullDot && buv != null) { bullDot.setAttribute('cx', x); bullDot.setAttribute('cy', toY(buv)); bullDot.setAttribute('visibility', 'visible'); }
    if (bearDot && bev != null) { bearDot.setAttribute('cx', x); bearDot.setAttribute('cy', toY(bev)); bearDot.setAttribute('visibility', 'visible'); }

    const sign = v => v >= invested ? '+' : '';
    const fv = v => 'NT$' + Math.round(v).toLocaleString();
    let html = `<div style="font-weight:700;margin-bottom:4px;color:#aecfd4">${getXLabel(n)}</div>`;
    if (buv != null) html += `<div><span style="color:#4fb3c1">▲ 樂觀</span>　${fv(buv)}</div>`;
    html += `<div><span style="color:#8ba9ae">● 基準</span>　${fv(bv)}</div>`;
    if (bev != null) html += `<div><span style="color:#e57373">▼ 保守</span>　${fv(bev)}</div>`;
    foDiv.innerHTML = html;

    // Position tooltip: left or right of crosshair
    const foW = 165, foH = 110;
    const txOffset = 10;
    let tx = x + txOffset;
    if (tx + foW > W - padR) tx = x - foW - txOffset;
    fo.setAttribute('x', tx); fo.setAttribute('y', padT + 4);
    fo.setAttribute('width', foW); fo.setAttribute('height', foH);
    fo.setAttribute('visibility', 'visible');
  });

  hitRect.addEventListener('mouseleave', () => {
    vLine.setAttribute('visibility', 'hidden');
    fo.setAttribute('visibility', 'hidden');
    if (bullDot) bullDot.setAttribute('visibility', 'hidden');
    if (baseDot) baseDot.setAttribute('visibility', 'hidden');
    if (bearDot) bearDot.setAttribute('visibility', 'hidden');
  });
}

// ── Stock info & chart ────────────────────────────────────────
// ── 股票候選清單（輸入模糊時，例如「電子」對到好幾家公司）──────────
function _showStockCandidates(candidates, query) {
  _pendingCandidates = candidates;
  const wrap = document.getElementById('stock-candidates-wrap');
  wrap.innerHTML = candidates.map(c => `
    <button type="button" onmousedown="event.preventDefault()" onclick="selectStockCandidate('${c.ticker.replace(/'/g, "\\'")}')"
      class="block w-full text-left px-3 py-2 rounded-lg bg-surface-container-lowest hover:bg-surface-container-high text-sm font-semibold text-on-surface-variant transition-colors">
      ${c.name}　<span class="text-outline text-xs font-normal">${c.ticker}</span>
    </button>`).join('');
  wrap.classList.remove('hidden');
}

function _hideStockCandidates() {
  _pendingCandidates = [];
  const wrap = document.getElementById('stock-candidates-wrap');
  if (wrap) { wrap.classList.add('hidden'); wrap.innerHTML = ''; }
}

function selectStockCandidate(ticker) {
  document.getElementById('sim-ticker').value = ticker;
  _hideStockCandidates();
  fetchStockInfo();
}

// fetchStockInfo 可能被 blur / Enter / 試算按鈕的保底邏輯同時觸發，
// 同一個輸入內容正在查詢中就沿用同一個 promise，避免重複打 API。
async function fetchStockInfo() {
  const ticker = document.getElementById('sim-ticker').value.trim();
  if (!ticker) return;
  if (_stockInfoInFlight && _stockInfoInFlight.forInput === ticker) {
    return _stockInfoInFlight.promise;
  }
  const promise = _doFetchStockInfo(ticker);
  _stockInfoInFlight = { forInput: ticker, promise };
  try {
    await promise;
  } finally {
    if (_stockInfoInFlight && _stockInfoInFlight.forInput === ticker) _stockInfoInFlight = null;
  }
}

async function _doFetchStockInfo(ticker) {
  document.getElementById('sim-yield-base').value = '';
  document.getElementById('sim-yield-bull').value = '';
  document.getElementById('sim-yield-bear').value = '';
  _bullUserEdited = false;
  _bearUserEdited = false;
  _episodeNodes = [];
  _selectedScoreId = null;
  _scenarioMeta = null;
  _currentTicker = '';
  _hideStockCandidates();
  document.getElementById('episode-scenario-wrap').classList.add('hidden');
  document.getElementById('results-section').classList.add('hidden');
  document.getElementById('calc-results-placeholder').classList.remove('hidden');
  const status = document.getElementById('stock-query-status');
  status.textContent = '查詢中...';
  status.classList.remove('hidden');
  document.getElementById('sim-price-row').classList.add('hidden');
  try {
    const startDate = document.getElementById('sim-start-date').value;
    const endDate   = document.getElementById('sim-end-date').value;
    // 股價走勢圖只能顯示歷史資料；若結束日期落在未來（試算期間），改抓近一年歷史價格
    const isHistoricalRange = startDate && endDate && new Date(endDate) <= new Date();
    const url = isHistoricalRange
      ? `/api/calculator/stock-chart/?ticker=${encodeURIComponent(ticker)}&start_date=${startDate}&end_date=${endDate}`
      : `/api/calculator/stock-chart/?ticker=${encodeURIComponent(ticker)}&period=1y`;

    const res  = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      if (data.candidates && data.candidates.length) {
        status.textContent = `「${ticker}」有多個可能的結果，請從下面選擇：`;
        status.classList.remove('hidden');
        _showStockCandidates(data.candidates, ticker);
        return;
      }
      status.textContent = data.error || '找不到此股票，請確認代號或名稱';
      status.classList.remove('hidden');
      return;
    }
    const prices       = data.data;
    const last         = prices[prices.length - 1].close;
    const periodReturn = (last / prices[0].close - 1) * 100;

    stockCurrentPrice = last;
    _currentTicker = data.ticker;
    _lastResolvedInputText = ticker;
    document.getElementById('sim-current-price-display').textContent = 'NT$' + last.toFixed(1);
    document.getElementById('sim-stock-name').textContent = data.name;
    document.getElementById('sim-price-row').classList.remove('hidden');
    status.textContent = '';
    status.classList.add('hidden');

    const bestPeriod = _pickBestPeriod(startDate, endDate);
    stockPeriod = bestPeriod;
    const originEpisodeId = _pendingOriginEpisodeId;
    _pendingOriginEpisodeId = null;
    await fetchEpisodeTimeline(data.ticker, originEpisodeId);
  } catch(e) {
    status.textContent = '查詢失敗，請稍後再試';
    status.classList.remove('hidden');
  }
}

function setStockPeriod(period) {
  stockPeriod = period;
  _highlightPeriodBtn(period);
  if (_currentTicker) _fetchAndRenderChart(_currentTicker, period);
}

// ── Graph page（個股趨勢圖 + 新聞，從計算機頁面跳過去）──────────────
function openGraphForCurrentTicker() {
  if (!_currentTicker) return;
  showPage('graph');
  document.getElementById('stock-chart-subtitle').textContent = document.getElementById('sim-stock-name').textContent || _currentTicker;
  const bestPeriod = _pickBestPeriod(
    document.getElementById('sim-start-date').value,
    document.getElementById('sim-end-date').value
  );
  stockPeriod = bestPeriod;
  _highlightPeriodBtn(bestPeriod);
  _fetchAndRenderChart(_currentTicker, bestPeriod);
  fetchStockNews(_currentTicker);
}

function _highlightPeriodBtn(period) {
  document.querySelectorAll('.stock-period-btn').forEach(btn => {
    btn.className = btn.dataset.period === period
      ? 'stock-period-btn px-3 py-1.5 rounded-full font-label text-xs font-bold transition-all bg-tertiary-container text-white'
      : 'stock-period-btn px-3 py-1.5 rounded-full font-label text-xs font-bold transition-all text-secondary';
  });
}

function _pickBestPeriod(startDate, endDate) {
  if (!startDate || !endDate) return '1y';
  const days = (new Date(endDate) - new Date(startDate)) / 86400000;
  if (days <= 45)  return '1mo';
  if (days <= 135) return '3mo';
  if (days <= 270) return '6mo';
  return '1y';
}

async function _fetchAndRenderChart(ticker, period) {
  document.getElementById('stock-chart-placeholder').textContent = '載入中...';
  document.getElementById('stock-chart-placeholder').classList.remove('hidden');
  document.getElementById('stock-svg').classList.add('hidden');
  try {
    const res  = await fetch(`/api/calculator/stock-chart/?ticker=${encodeURIComponent(ticker)}&period=${period}`);
    const data = await res.json();
    if (!res.ok) return;
    document.getElementById('stock-chart-placeholder').classList.add('hidden');
    renderStockChart(data);
  } catch(e) {}
}

async function fetchChartForPeriod(ticker) { await _fetchAndRenderChart(ticker, stockPeriod); }
async function fetchChartForDates(ticker)  { await _fetchAndRenderChart(ticker, stockPeriod); }

function renderStockChart(data) {
  const svg    = document.getElementById('stock-svg');
  svg.innerHTML = '';
  svg.classList.remove('hidden');
  document.getElementById('stock-chart-placeholder').classList.add('hidden');
  const prices = data.data;
  if (!prices.length) return;

  const W = 800, H = 340, padL = 58, padR = 16, padT = 16, padB = 40;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  const ns   = 'http://www.w3.org/2000/svg';
  const minP = Math.min(...prices.map(d => d.close));
  const maxP = Math.max(...prices.map(d => d.close));
  const n    = prices.length;
  const toX  = i => padL + (i / (n - 1)) * (W - padL - padR);
  const toY  = v => padT + (1 - (v - minP) / (maxP - minP + 0.01)) * (H - padT - padB);
  const isUp = prices[n - 1].close >= prices[0].close;
  const lineColor = isUp ? '#113236' : '#ba1a1a';

  [0, 0.25, 0.5, 0.75, 1].forEach(r => {
    const val = minP + r * (maxP - minP);
    const y   = toY(val);
    const gl  = document.createElementNS(ns, 'line');
    gl.setAttribute('x1', padL); gl.setAttribute('x2', W - padR);
    gl.setAttribute('y1', y); gl.setAttribute('y2', y);
    gl.setAttribute('stroke', '#e5e3d9'); gl.setAttribute('stroke-width', '1');
    svg.appendChild(gl);
    const gt = document.createElementNS(ns, 'text');
    gt.setAttribute('x', padL - 6); gt.setAttribute('y', y + 4);
    gt.setAttribute('fill', '#717879'); gt.setAttribute('font-family', 'Manrope');
    gt.setAttribute('font-size', '11'); gt.setAttribute('font-weight', '600');
    gt.setAttribute('text-anchor', 'end');
    gt.textContent = val >= 1000 ? val.toFixed(0) : val.toFixed(2);
    svg.appendChild(gt);
  });

  let d = '';
  prices.forEach((p, i) => { d += (i === 0 ? 'M' : ' L') + `${toX(i)},${toY(p.close)}`; });
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', d); path.setAttribute('fill', 'none');
  path.setAttribute('stroke', lineColor); path.setAttribute('stroke-width', '2.5');
  path.setAttribute('stroke-linecap', 'round');
  svg.appendChild(path);

  for (let i = 0; i <= 4; i++) {
    const idx = Math.round(i * (n - 1) / 4);
    const xt  = document.createElementNS(ns, 'text');
    xt.setAttribute('x', toX(idx)); xt.setAttribute('y', H - padB + 20);
    xt.setAttribute('fill', '#717879'); xt.setAttribute('font-family', 'Manrope');
    xt.setAttribute('font-size', '11'); xt.setAttribute('font-weight', '600');
    xt.setAttribute('text-anchor', 'middle');
    xt.textContent = prices[idx].date.slice(5);
    svg.appendChild(xt);
  }

  const lastClose = prices[n - 1].close;
  const change    = ((lastClose - prices[0].close) / prices[0].close * 100).toFixed(2);
  const annualizedReturn = (stockPeriod === '3mo'
    ? (Math.pow(1 + parseFloat(change) / 100, 4) - 1) * 100
    : stockPeriod === '6mo'
    ? (Math.pow(1 + parseFloat(change) / 100, 2) - 1) * 100
    : stockPeriod === '2y'
    ? (Math.pow(1 + parseFloat(change) / 100, 0.5) - 1) * 100
    : parseFloat(change)).toFixed(1);

  document.getElementById('stock-name').textContent          = data.name;
  document.getElementById('stock-last-price').textContent    = 'NT$' + lastClose.toFixed(1);
  document.getElementById('stock-chart-subtitle').textContent = data.name;
  const changeEl = document.getElementById('stock-change');
  changeEl.textContent = (change >= 0 ? '+' : '') + change + '%';
  changeEl.className   = `font-['Epilogue'] font-bold text-lg ${isUp ? 'text-[#286671]' : 'text-[#ba1a1a]'}`;
  const histEl = document.getElementById('stock-hist-return');
  histEl.textContent = (annualizedReturn >= 0 ? '+' : '') + annualizedReturn + '%';
  histEl.className   = `font-['Epilogue'] font-bold text-lg ${annualizedReturn >= 0 ? 'text-[#286671]' : 'text-[#ba1a1a]'}`;
  document.getElementById('stock-info').classList.remove('hidden');
  document.getElementById('stock-data-asof').textContent = '資料更新至 ' + prices[n - 1].date;
}

// ── Stock news ────────────────────────────────────────────────
async function fetchStockNews(ticker) {
  const section = document.getElementById('stock-news-section');
  const list    = document.getElementById('stock-news-list');
  section.classList.add('hidden');
  list.innerHTML = '';
  _newsCache = [];
  try {
    const res  = await fetch(`/api/calculator/stock-news/?ticker=${encodeURIComponent(ticker)}`);
    const data = await res.json();
    if (!res.ok || !data.news?.length) return;
    _newsCache = data.news;
    list.innerHTML = data.news.map((n, i) => {
      const date      = n.pub ? new Date(n.pub).toLocaleDateString('zh-TW') : '';
      const safeTitle = n.title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      return `
        <div onclick="openNewsPanel(_newsCache[${i}])"
             class="flex items-start gap-3 p-3 rounded-lg bg-surface-container-lowest hover:bg-surface-container cursor-pointer transition-colors">
          <div class="flex-1 min-w-0">
            <p class="text-sm font-semibold text-on-surface leading-snug line-clamp-2">${safeTitle}</p>
            <p class="text-[10px] text-outline mt-1">${n.provider}${date ? ' · ' + date : ''}</p>
          </div>
          <span class="material-symbols-outlined text-outline/50 text-sm flex-shrink-0 mt-0.5">chevron_right</span>
        </div>`;
    }).join('');
    section.classList.remove('hidden');
  } catch(e) {}
}

// ── News panel ────────────────────────────────────────────────
function _newsPanelShowContent(body, text, linkUrl) {
  body.innerHTML = '';
  const paras = (text || '').split(/\n\n+/);
  paras.forEach(para => {
    if (!para.trim()) return;
    const p = document.createElement('p');
    p.className = 'text-sm leading-relaxed text-on-surface mb-3';
    const lines = para.split('\n');
    lines.forEach((line, i) => {
      p.appendChild(document.createTextNode(line));
      if (i < lines.length - 1) p.appendChild(document.createElement('br'));
    });
    body.appendChild(p);
  });
  if (!body.children.length) {
    const p = document.createElement('p');
    p.className = 'text-sm leading-relaxed text-on-surface';
    p.textContent = text;
    body.appendChild(p);
  }
  if (linkUrl) {
    const a = document.createElement('a');
    a.href = linkUrl; a.target = '_blank'; a.rel = 'noopener';
    a.className = 'inline-flex items-center gap-1 mt-4 text-xs text-primary hover:underline';
    a.innerHTML = '<span class="material-symbols-outlined text-sm">open_in_new</span>閱讀完整原文';
    body.appendChild(a);
  }
}

function _newsPanelShowFallback(body, url) {
  body.innerHTML = '';
  const msg = document.createElement('p');
  msg.className = 'text-sm text-outline mb-3';
  msg.textContent = '無法直接載入文章內容。';
  body.appendChild(msg);
  const a = document.createElement('a');
  a.href = url; a.target = '_blank'; a.rel = 'noopener';
  a.className = 'inline-flex items-center gap-1 text-sm text-primary hover:underline';
  a.innerHTML = '<span class="material-symbols-outlined text-base">open_in_new</span>開啟原文';
  body.appendChild(a);
}

async function openNewsPanel(n) {
  document.getElementById('news-panel-title').textContent = n.title;
  const date = n.pub ? new Date(n.pub).toLocaleDateString('zh-TW') : '';
  document.getElementById('news-panel-meta').textContent = [n.provider, date].filter(Boolean).join(' · ');
  document.getElementById('news-panel-overlay').classList.remove('hidden');
  document.getElementById('news-panel').classList.remove('translate-x-full');

  const body = document.getElementById('news-panel-body');
  body.innerHTML = '<p class="text-sm text-outline">載入內文中...</p>';
  try {
    const res  = await fetch(`/api/calculator/news-content/?url=${encodeURIComponent(n.url)}`);
    const data = await res.json();
    if (!data.error && data.content) _newsPanelShowContent(body, data.content, data.real_url || n.url);
    else if (n.snippet) _newsPanelShowContent(body, n.snippet, n.url);
    else _newsPanelShowFallback(body, n.url);
  } catch(e) {
    if (n.snippet) _newsPanelShowContent(body, n.snippet, n.url);
    else _newsPanelShowFallback(body, n.url);
  }
}

function closeNewsPanel() {
  document.getElementById('news-panel').classList.add('translate-x-full');
  document.getElementById('news-panel-overlay').classList.add('hidden');
}

// ── Calculator nav helpers ────────────────────────────────────
function resetCalculator() {
  document.getElementById('results-section').classList.add('hidden');
  document.getElementById('calc-results-placeholder').classList.remove('hidden');
  document.getElementById('sim-validation-error').classList.add('hidden');
  _currentTicker = '';
  _lastResolvedInputText = null;
  _hideStockCandidates();
  document.getElementById('sim-ticker').value = '';
  document.getElementById('sim-stock-name').textContent = '';
  document.getElementById('sim-current-price-display').textContent = '';
  document.getElementById('stock-query-status').textContent = '';
  document.getElementById('stock-query-status').classList.add('hidden');
  document.getElementById('stock-chart-subtitle').textContent = '查詢個股後顯示走勢';
  document.getElementById('stock-chart-placeholder').classList.remove('hidden');
  const svg = document.getElementById('stock-svg');
  if (svg) { svg.classList.add('hidden'); svg.innerHTML = ''; }
  document.getElementById('sim-yield-base').value = '';
  document.getElementById('sim-yield-bull').value = '';
  document.getElementById('sim-yield-bear').value = '';
  _bullUserEdited = false;
  _bearUserEdited = false;
  _episodeNodes = [];
  _selectedScoreId = null;
  _scenarioMeta = null;
  document.getElementById('episode-scenario-wrap').classList.add('hidden');
  stockCurrentPrice = 0;
  _hostYield = null;
  _hostCalcData = null;
  document.getElementById('host-yield-reset').classList.add('hidden');
  document.getElementById('host-calc-badge').style.display = 'none';
  _initSimDates();
}

function goToCalculatorWithStock(stockName, endDate, originEpisodeId) {
  _pendingOriginEpisodeId = originEpisodeId != null ? originEpisodeId : null;
  document.getElementById('sim-yield-base').value = '';
  document.getElementById('sim-yield-bull').value = '';
  document.getElementById('sim-yield-bear').value = '';
  _bullUserEdited = false;
  _bearUserEdited = false;
  document.getElementById('sim-capital').value  = '';
  document.getElementById('slider-capital').value = '0';
  document.getElementById('results-section').classList.add('hidden');
  const today       = new Date().toISOString().slice(0, 10);
  const resolvedEnd = endDate || (() => { const d = new Date(); d.setFullYear(d.getFullYear() + 1); return d.toISOString().slice(0, 10); })();
  _hostCalcData = { ticker: stockName, endDate: resolvedEnd };
  const endLabel = endDate ? endDate.replace(/-/g, '/') : '未指定';
  document.getElementById('host-calc-label').textContent = `主持人：${stockName}　截止 ${endLabel}`;
  document.getElementById('host-calc-badge').style.display = 'none';
  document.getElementById('sim-ticker').value     = stockName;
  document.getElementById('sim-start-date').value = today;
  document.getElementById('sim-end-date').value   = resolvedEnd;
  showPage('calculator');
  fetchStockInfo();
}

function parseYears(timeframe) {
  if (!timeframe) return null;
  if (/短期/.test(timeframe)) return 1 / 12;
  if (/中期/.test(timeframe)) return 3 / 12;
  if (/長期/.test(timeframe)) return 1;
  const yearMatch  = timeframe.match(/(\d+)\s*年/);
  const monthMatch = timeframe.match(/(\d+)\s*個?月/);
  const qMatch     = timeframe.match(/Q[1-4]/i);
  if (yearMatch)  return parseFloat(yearMatch[1]);
  if (monthMatch) return parseFloat((parseInt(monthMatch[1]) / 12).toFixed(1));
  if (qMatch)     return 0.5;
  return null;
}

function openCalculatorWithTicker(t) {
  document.getElementById('sim-ticker').value = t.ticker;
  showPage('calculator');
  _hostYield = t.expected_return != null ? t.expected_return : null;
  document.getElementById('host-yield-reset').classList.add('hidden');
  if (_hostYield != null) document.getElementById('host-yield-label').textContent = _hostYield;
  fetchStockInfo().then(() => {
    if (t.expected_return != null) {
      _bullUserEdited = false;
      _bearUserEdited = false;
      document.getElementById('sim-yield-base').value = t.expected_return;
      onBaseYieldChange();
    }
  });
}
