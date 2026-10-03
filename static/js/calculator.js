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
let _currentTicker = '';            // 目前已查詢解析出的 ticker（給「查看走勢圖與新聞」按鈕跳轉用）
let _lastResolvedInputText = null;  // 股票代號輸入框最後一次成功查詢時的原始文字，判斷試算時要不要先補查
let _stockInfoInFlight = null;      // { forInput, promise }：目前正在跑的查詢，避免 blur/Enter/試算同時觸發重複打 API
let _pendingCandidates = [];        // 輸入模糊時（例如「電子」）後端回傳的候選股票清單，等使用者選一個

// ── 試算模擬器：歷史拔靴逐步模擬(B方案，apps/calculator/services/bootstrap_path_simulator.py) ──
// 每一步都從真實歷史日報酬率隨機抽樣，真的跑N次路徑，不是套公式算出來的。
// 跟下面 /preview/bootstrap-path/ 完全獨立測試頁用的是同一套後端API跟前端邏輯，
// 這裡是正式接進calculator.html後的版本。
const B_API_URL = '/api/calculator/bootstrap-path-preview/';
const B_EPISODES_URL = '/api/calculator/bootstrap-path-preview/episodes/';
const B_SOURCE_TEXT_URL = '/api/calculator/bootstrap-path-preview/source-text/';

let mode = 'manual';
let episodes = [];
let lastResult = null;
let sourceTextRequestSeq = 0;  // 避免快速切換節目時，舊的source-text回應蓋掉新選的那一集

function setMode(m) {
  mode = m;
  document.getElementById('mode-btn-manual').classList.toggle('active', m === 'manual');
  document.getElementById('mode-btn-episode').classList.toggle('active', m === 'episode');
  document.getElementById('panel-manual').classList.toggle('hidden', m !== 'manual');
  document.getElementById('panel-episode').classList.toggle('hidden', m !== 'episode');
}

function syncFromSlider(key) {
  document.getElementById('in-' + key).value = document.getElementById('slider-' + key).value;
  document.getElementById('lbl-' + key).textContent = document.getElementById('slider-' + key).value;
}
function syncFromInput(key) {
  const v = document.getElementById('in-' + key).value;
  document.getElementById('slider-' + key).value = v;
  document.getElementById('lbl-' + key).textContent = v;
}
// 風險程度(低/中/高)、市場立場(看空/中立/看多)這兩組用三顆按鈕取代滑桿+數字框，
// 底層數值(0/0.5/1、-1/0/1)還是寫進同一個in-risk/in-macro(改成hidden input)，
// runSimulation()讀值的地方完全不用改。
function setScoreButton(key, value) {
  document.getElementById('in-' + key).value = value;
  document.querySelectorAll('#btns-' + key + ' button').forEach(btn => {
    btn.classList.toggle('active', parseFloat(btn.dataset.value) === value);
  });
}
// 跟三顆按鈕(低/中/高、看空/中立/看多)用同一套文字，純文字顯示(例如選集數後的說明列)
// 也統一改用這兩個函式，不要再直接印0.5、-1這種原始數字給使用者看。
function _riskWordLabel(v) {
  if (v >= 1) return '高';
  if (v >= 0.5) return '中';
  return '低';
}
function _macroWordLabel(v) {
  if (v > 0) return '看多';
  if (v < 0) return '看空';
  return '中立';
}
function syncInvestedFromSlider() {
  document.getElementById('in-invested').value = document.getElementById('slider-invested').value;
}
function syncInvestedFromInput() {
  const v = document.getElementById('in-invested').value;
  if (v !== '') document.getElementById('slider-invested').value = v;
}

async function loadEpisodes() {
  try {
    const resp = await fetch(B_EPISODES_URL);
    const data = await resp.json();
    episodes = data.episodes || [];
    const sel = document.getElementById('in-episode');
    sel.innerHTML = episodes.map(e =>
      `<option value="${e.score_id}">${e.published_at || '未知日期'}　${e.asset_name}(${e.ticker})　${e.podcaster || ''}</option>`
    ).join('');
    if (_pendingCalcTarget) {
      _applyPendingCalcTarget();
    } else if (episodes.length) {
      onEpisodeChange();
    }
  } catch (e) {
    const info = document.getElementById('episode-info');
    if (info) info.textContent = '載入節目清單失敗: ' + e;
  }
}
loadEpisodes();

// 從 deep_dive「試算」按鈕跳轉過來時，優先用episode_id+ticker精準配對(同一集裡常常
// 不只討論一支股票，只比episode_id會抓到同一集裡隨便哪一筆、不一定是使用者點的那支
// ——這是原本就有的bug，只是要同一集裡兩支股票都已經進過清單才會顯現出來)；配不到
// 就退而求其次只配ticker(可能配到別集同一支股票的資料)。這兩種都配不到，原本會直接
// 放棄、默默維持上一次選到的舊資料，使用者完全看不出來哪裡出錯(這一集這支股票剛好
// 還沒被批次分類算過分數時就會這樣)——改成配不到時呼叫 ensure-episode-score 即時算
// 一次，跟另一套「podcast驅動情境」系統(_loadScenarioForEpisode)的fallback邏輯一致，
// 不要讓兩套入口對同一種情況的處理不一樣。
async function _applyPendingCalcTarget() {
  if (!_pendingCalcTarget || !episodes.length) return;
  const target = _pendingCalcTarget;
  _pendingCalcTarget = null;
  let match = episodes.find(e => target.episodeId != null && e.episode_id === target.episodeId && e.ticker === target.ticker)
    || episodes.find(e => e.ticker === target.ticker);
  if (!match && target.episodeId != null) {
    try {
      const res = await fetch(`/api/calculator/ensure-episode-score/?episode_id=${encodeURIComponent(target.episodeId)}&ticker=${encodeURIComponent(target.ticker)}`);
      const data = await res.json();
      if (res.ok) {
        match = data;
        if (!episodes.some(e => e.score_id === data.score_id)) {
          episodes = [data, ...episodes];
          const sel = document.getElementById('in-episode');
          const opt = document.createElement('option');
          opt.value = data.score_id;
          opt.textContent = `${data.published_at || '未知日期'}　${data.asset_name}(${data.ticker})　${data.podcaster || ''}`;
          sel.insertBefore(opt, sel.firstChild);
        }
      } else {
        console.warn('ensure-episode-score失敗:', data.error);
      }
    } catch (e) {
      console.warn('ensure-episode-score呼叫失敗:', e);
    }
  }
  if (match) {
    document.getElementById('in-episode').value = match.score_id;
  }
  onEpisodeChange();
}

function onEpisodeChange() {
  const scoreId = document.getElementById('in-episode').value;
  const ep = episodes.find(e => String(e.score_id) === String(scoreId));
  if (!ep) return;
  let info = `${ep.asset_name}(${ep.ticker})　發布於${ep.published_at}　風險：${_riskWordLabel(ep.risk_score)}　立場：${_macroWordLabel(ep.macro_score)}`;
  if (ep.default_months) info += `　｜　該集backtesting時間範圍約${ep.default_months}個月`;
  document.getElementById('episode-info').textContent = info;

  const sourceLink = document.getElementById('episode-source-link');
  if (ep.summary_id) {
    sourceLink.href = '/?open_summary=' + ep.summary_id;
    sourceLink.classList.remove('hidden');
  } else {
    sourceLink.classList.add('hidden');
  }

  setScoreButton('risk', ep.risk_score);
  setScoreButton('macro', ep.macro_score);

  if (ep.default_months) {
    const m = Math.min(60, ep.default_months);
    document.getElementById('in-months').value = m;
    document.getElementById('slider-months').value = Math.min(36, m);
    document.getElementById('lbl-months').textContent = m;
  }

  document.getElementById('episode-rationale').textContent = ep.rationale || '(無)';
  loadSourceText(scoreId);
}

async function loadSourceText(scoreId) {
  const mySeq = ++sourceTextRequestSeq;
  const el = document.getElementById('episode-source-text');
  el.textContent = '載入中...';
  try {
    const resp = await fetch(B_SOURCE_TEXT_URL + '?score_id=' + encodeURIComponent(scoreId));
    const data = await resp.json();
    if (mySeq !== sourceTextRequestSeq) return;
    el.textContent = resp.ok ? (data.full_context || data.topic_summary || '(這集沒有找到對應的原始段落)') : ('載入失敗: ' + data.error);
  } catch (e) {
    if (mySeq !== sourceTextRequestSeq) return;
    el.textContent = '載入失敗: ' + e;
  }
}

async function runSimulation() {
  const months = document.getElementById('in-months').value;
  const risk = document.getElementById('in-risk').value;
  const macro = document.getElementById('in-macro').value;
  const nsim = document.getElementById('in-nsim').value;

  const statusEl = document.getElementById('status-text');
  const panel = document.getElementById('result-panel');
  const placeholder = document.getElementById('calc-placeholder');
  statusEl.textContent = '跑模擬中...(要抓歷史股價+跑模擬，可能要幾秒)';
  panel.classList.add('hidden');

  const params = new URLSearchParams({ months, risk_score: risk, macro_score: macro, n_simulations: nsim });
  if (mode === 'episode') {
    const scoreId = document.getElementById('in-episode').value;
    if (!scoreId) { statusEl.textContent = '請先選一集'; return; }
    params.set('score_id', scoreId);
  } else {
    const ticker = document.getElementById('in-ticker').value.trim();
    if (!ticker) { statusEl.textContent = '請輸入股票代號'; return; }
    params.set('ticker', ticker);
  }

  try {
    const resp = await fetch(B_API_URL + '?' + params.toString());
    const data = await resp.json();
    if (!resp.ok) {
      statusEl.textContent = '錯誤: ' + (data.error || resp.statusText);
      return;
    }
    statusEl.textContent = '';
    lastResult = data;
    renderResult(data);
    placeholder.classList.add('hidden');
    placeholder.hidden = true; // space-y-6只認原生hidden屬性，不認class="hidden"，
    // 只加class的話space-y-6還是會把它當成「存在」的前一個手足元素，在它跟
    // result-panel之間插入多的上邊距，造成兩欄卡片頂端對不齊
    panel.classList.remove('hidden');
  } catch (e) {
    statusEl.textContent = '請求失敗: ' + e;
  }
}

function fmtPct(x) {
  return (x * 100 >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%';
}
function fmtMoney(x) {
  return 'NT$' + Math.round(x).toLocaleString();
}

function renderResult(data) {
  document.getElementById('stat-start').textContent = data.start_price.toFixed(2);
  // 「保守情境/積極情境」這兩個數字用conservative_return/aggressive_return(P25/P75)，
  // 不是p10_return/p90_return——灰色區間帶(下面renderChart用的band_low/band_high)
  // 維持P10~P90，兩者刻意脫鉤，見bootstrap_path_simulator_v2.py docstring。
  document.getElementById('stat-p10').textContent = fmtPct(data.conservative_return);
  document.getElementById('stat-median').textContent = fmtPct(data.median_return);
  document.getElementById('stat-p90').textContent = fmtPct(data.aggressive_return);

  renderChart(data);
  renderMoneyStats();
}

function renderChart(data) {
  const svg = document.getElementById('chart-svg');
  svg.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';

  const W = 700, H = 320, padL = 60, padR = 20, padT = 16, padB = 30;
  const steps = data.time_steps;
  const n = steps.length;

  const actualVals = (data.actual_line || []).filter(v => v != null);
  const allVals = [...data.band_low, ...data.band_high, ...data.conservative_path, ...data.aggressive_path, ...actualVals];
  const vMin = Math.min(...allVals);
  const vMax = Math.max(...allVals);
  const pad = (vMax - vMin) * 0.05 || vMax * 0.1;
  const lo = vMin - pad, hi = vMax + pad;

  const toX = i => padL + (i / (n - 1)) * (W - padL - padR);
  const toXByMonth = m => padL + (m / data.months) * (W - padL - padR);
  const toY = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  function pathFor(arr) {
    return arr.map((v, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  }

  for (let k = 0; k <= 4; k++) {
    const v = lo + (hi - lo) * k / 4;
    const y = toY(v);
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', padL); line.setAttribute('x2', W - padR);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
    line.setAttribute('stroke', '#e5e3d9'); line.setAttribute('stroke-width', '1');
    svg.appendChild(line);
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', padL - 8); text.setAttribute('y', y + 3);
    text.setAttribute('text-anchor', 'end'); text.setAttribute('font-family', 'Manrope');
    text.setAttribute('font-size', '11'); text.setAttribute('font-weight', '600'); text.setAttribute('fill', '#717879');
    text.textContent = v.toFixed(0);
    svg.appendChild(text);
  }

  const bandD = pathFor(data.band_high) + ' L' + toX(n - 1).toFixed(1) + ',' + toY(data.band_low[n - 1]).toFixed(1) + ' '
    + data.band_low.slice().reverse().map((v, i) => `L${toX(n - 1 - i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ') + ' Z';
  const bandPath = document.createElementNS(ns, 'path');
  bandPath.setAttribute('d', bandD);
  bandPath.setAttribute('fill', '#113236');
  bandPath.setAttribute('fill-opacity', '0.10');
  bandPath.setAttribute('stroke', 'none');
  svg.appendChild(bandPath);

  function drawLine(pathArr, color, dash, width) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', pathFor(pathArr));
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', width || '2.5');
    path.setAttribute('stroke-linecap', 'round');
    if (dash) path.setAttribute('stroke-dasharray', dash);
    svg.appendChild(path);
  }

  // 保守/積極這兩條線用跟統計卡片(stat-p10/stat-p90)一樣的顏色(#ba1a1a紅/#286671青)，
  // 全站對「保守=紅、積極=青」維持一致的視覺語言；虛線、線寬比基準線細一點，
  // 避免跟基準線搶視覺重心，但又要比灰色區間帶的邊緣更顯眼、看得出這是特別標出來的兩條線。
  drawLine(data.conservative_path, '#ba1a1a', '5,3', '2');
  drawLine(data.aggressive_path, '#286671', '5,3', '2');
  drawLine(data.median_path, '#113236');

  const legend = document.getElementById('chart-legend');
  legend.innerHTML = `
    <span class="flex items-center gap-2"><svg width="22" height="4"><line x1="0" y1="2" x2="22" y2="2" stroke="#113236" stroke-width="2.5"/></svg><span class="font-bold text-tertiary-container">模擬基準</span></span>
    <span class="flex items-center gap-2"><svg width="22" height="4"><line x1="0" y1="2" x2="22" y2="2" stroke="#286671" stroke-width="2" stroke-dasharray="5,3"/></svg><span class="text-outline">積極情境(P75)</span></span>
    <span class="flex items-center gap-2"><svg width="22" height="4"><line x1="0" y1="2" x2="22" y2="2" stroke="#ba1a1a" stroke-width="2" stroke-dasharray="5,3"/></svg><span class="text-outline">保守情境(P25)</span></span>
    <span class="flex items-center gap-2"><span class="inline-block w-3 h-2 rounded-sm" style="background:#113236;opacity:0.10;border:1px solid #113236"></span><span class="text-outline">P10~P90統計區間帶</span></span>
  `;

  if (data.actual_line && data.actual_line.some(v => v != null)) {
    let segStart = null;
    let d = '';
    data.actual_line.forEach((v, i) => {
      if (v == null) { segStart = null; return; }
      d += (segStart === null ? 'M' : 'L') + toXByMonth(i).toFixed(1) + ',' + toY(v).toFixed(1) + ' ';
      segStart = i;
    });
    const actualPath = document.createElementNS(ns, 'path');
    actualPath.setAttribute('d', d.trim());
    actualPath.setAttribute('fill', 'none');
    actualPath.setAttribute('stroke', '#d97f12');
    actualPath.setAttribute('stroke-width', '2.5');
    actualPath.setAttribute('stroke-linecap', 'round');
    svg.appendChild(actualPath);
    legend.innerHTML += `<span class="flex items-center gap-2"><svg width="22" height="4"><line x1="0" y1="2" x2="22" y2="2" stroke="#d97f12" stroke-width="2.5"/></svg><span class="font-bold" style="color:#d97f12">真實股價</span></span>`;
  }

  const totalMonths = data.months;
  const xLabelCount = Math.min(6, totalMonths);
  for (let k = 0; k <= xLabelCount; k++) {
    const monthVal = Math.round(totalMonths * k / xLabelCount);
    const idx = Math.round((n - 1) * k / xLabelCount);
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', toX(idx)); text.setAttribute('y', H - padB + 16);
    text.setAttribute('text-anchor', 'middle'); text.setAttribute('font-family', 'Manrope');
    text.setAttribute('font-size', '11'); text.setAttribute('font-weight', '600'); text.setAttribute('fill', '#717879');
    text.textContent = `第${monthVal}月`;
    svg.appendChild(text);
  }

  // 游標懸浮的判斷點是「每個月」，跟上面畫時間軸文字標籤(疏一點，例如每2個月一個)
  // 是兩件獨立的事——標籤維持原本疏密，但滑鼠移到任何一個月份都要有反應。
  const hoverPoints = [];
  for (let m = 0; m <= totalMonths; m++) {
    const idx = Math.round((n - 1) * m / totalMonths);
    hoverPoints.push({ monthVal: m, idx });
  }

  attachChartHover(svg, ns, data, hoverPoints, toX, toY, { W, H, padL, padR, padT, padB });
}

// 滑鼠移到圖表上時，每個月份都會顯示那個時間點的保守/基準/積極情境數值；
// 如果那個月剛好有真實股價資料(當集模式、已經走到/過了那個月)，多顯示一行
// 真實股價，沒有資料的月份就不顯示這行，不是每個月都一定有真實股價可比較。
function attachChartHover(svg, ns, data, hoverPoints, toX, toY, dims) {
  const { W, H, padT, padB, padL, padR } = dims;

  const guide = document.createElementNS(ns, 'line');
  guide.setAttribute('y1', padT); guide.setAttribute('y2', H - padB);
  guide.setAttribute('stroke', '#717879'); guide.setAttribute('stroke-width', '1');
  guide.setAttribute('stroke-dasharray', '3,3');
  guide.setAttribute('opacity', '0');
  svg.appendChild(guide);

  function makeDot(color) {
    const dot = document.createElementNS(ns, 'circle');
    dot.setAttribute('r', '4');
    dot.setAttribute('fill', color);
    dot.setAttribute('stroke', '#fff');
    dot.setAttribute('stroke-width', '1.5');
    dot.setAttribute('opacity', '0');
    svg.appendChild(dot);
    return dot;
  }
  // P10/P90是區間帶的邊緣，本身不是重點數字，用中性灰色；保守(P25)/積極(P75)
  // 才是真正有語意的兩個點，顏色跟統計卡片(stat-p10/stat-p90)、圖表線條保持一致。
  const dotP10band = makeDot('#a8a49a');
  const dotMedian = makeDot('#113236');
  const dotP90band = makeDot('#a8a49a');
  const dotConservative = makeDot('#ba1a1a');
  const dotAggressive = makeDot('#286671');
  const dotActual = makeDot('#d97f12');

  const tooltip = document.createElementNS(ns, 'g');
  tooltip.setAttribute('opacity', '0');
  tooltip.style.pointerEvents = 'none';
  const tooltipBg = document.createElementNS(ns, 'rect');
  tooltipBg.setAttribute('rx', '6');
  tooltipBg.setAttribute('fill', '#182422');
  tooltipBg.setAttribute('fill-opacity', '0.92');
  tooltip.appendChild(tooltipBg);

  // 順序由高到低：月份標題、P90路徑(灰)、積極情境P75(青)、基準(白)、
  // 保守情境P25(紅)、P10路徑(灰)——顏色盡量貼近對應的點/線顏色，方便對照。
  const lineColors = ['#c8d0ce', '#c8d0ce', '#8fd4cb', '#ffffff', '#ff9b9b', '#c8d0ce'];
  const textEls = lineColors.map(color => {
    const t = document.createElementNS(ns, 'text');
    t.setAttribute('font-family', 'Manrope'); t.setAttribute('font-size', '11'); t.setAttribute('font-weight', '600');
    t.setAttribute('fill', color);
    tooltip.appendChild(t);
    return t;
  });
  const actualText = document.createElementNS(ns, 'text');
  actualText.setAttribute('font-family', 'Manrope'); actualText.setAttribute('font-size', '11'); actualText.setAttribute('font-weight', '600');
  actualText.setAttribute('fill', '#ffb545');
  tooltip.appendChild(actualText);
  svg.appendChild(tooltip);

  const fmt = v => Math.round(v).toString();
  const boxW = 148;

  function show(point) {
    const x = toX(point.idx);
    guide.setAttribute('x1', x); guide.setAttribute('x2', x); guide.setAttribute('opacity', '0.6');

    const yP10 = toY(data.band_low[point.idx]);
    const yP90 = toY(data.band_high[point.idx]);
    const yMed = toY(data.median_path[point.idx]);
    const yCons = toY(data.conservative_path[point.idx]);
    const yAggr = toY(data.aggressive_path[point.idx]);
    dotP10band.setAttribute('cx', x); dotP10band.setAttribute('cy', yP10); dotP10band.setAttribute('opacity', '1');
    dotP90band.setAttribute('cx', x); dotP90band.setAttribute('cy', yP90); dotP90band.setAttribute('opacity', '1');
    dotMedian.setAttribute('cx', x); dotMedian.setAttribute('cy', yMed); dotMedian.setAttribute('opacity', '1');
    dotConservative.setAttribute('cx', x); dotConservative.setAttribute('cy', yCons); dotConservative.setAttribute('opacity', '1');
    dotAggressive.setAttribute('cx', x); dotAggressive.setAttribute('cy', yAggr); dotAggressive.setAttribute('opacity', '1');

    // P10/P90路徑是灰色區間帶的邊緣，跟「保守情境/積極情境」(P25/P75)是兩組
    // 不同的數字，分開列出來，不要互相混用。
    textEls[0].textContent = `第${point.monthVal}月`;
    textEls[1].textContent = `P90路徑　　${fmt(data.band_high[point.idx])}`;
    textEls[2].textContent = `積極情境　${fmt(data.aggressive_path[point.idx])}`;
    textEls[3].textContent = `基準　　　　${fmt(data.median_path[point.idx])}`;
    textEls[4].textContent = `保守情境　${fmt(data.conservative_path[point.idx])}`;
    textEls[5].textContent = `P10路徑　　${fmt(data.band_low[point.idx])}`;

    const actualVal = data.actual_line ? data.actual_line[point.monthVal] : null;
    const hasActual = actualVal != null;
    if (hasActual) {
      actualText.textContent = `真實股價　${fmt(actualVal)}`;
      dotActual.setAttribute('cx', x); dotActual.setAttribute('cy', toY(actualVal)); dotActual.setAttribute('opacity', '1');
    } else {
      actualText.textContent = '';
      dotActual.setAttribute('opacity', '0');
    }

    const lineCount = 6 + (hasActual ? 1 : 0);
    const boxH = 16 + lineCount * 20;

    let boxX = x + 14;
    if (boxX + boxW > W - padR) boxX = x - 14 - boxW;
    const boxY = padT + 4;
    tooltipBg.setAttribute('x', boxX); tooltipBg.setAttribute('y', boxY);
    tooltipBg.setAttribute('width', boxW); tooltipBg.setAttribute('height', boxH);
    [...textEls, actualText].forEach((t, i) => {
      t.setAttribute('x', boxX + 10);
      t.setAttribute('y', boxY + 19 + i * 20);
    });
    tooltip.setAttribute('opacity', '1');
  }

  function hide() {
    guide.setAttribute('opacity', '0');
    [dotP10band, dotP90band, dotMedian, dotConservative, dotAggressive, dotActual].forEach(d => d.setAttribute('opacity', '0'));
    tooltip.setAttribute('opacity', '0');
  }

  hoverPoints.forEach((point, k) => {
    const x = toX(point.idx);
    const prevX = k === 0 ? padL : (toX(hoverPoints[k - 1].idx) + x) / 2;
    const nextX = k === hoverPoints.length - 1 ? (W - padR) : (toX(hoverPoints[k + 1].idx) + x) / 2;
    const hit = document.createElementNS(ns, 'rect');
    hit.setAttribute('x', prevX); hit.setAttribute('y', padT);
    hit.setAttribute('width', Math.max(0, nextX - prevX)); hit.setAttribute('height', H - padT - padB);
    hit.setAttribute('fill', 'transparent');
    hit.style.cursor = 'pointer';
    hit.addEventListener('mouseenter', () => show(point));
    hit.addEventListener('mouseleave', hide);
    svg.appendChild(hit);
  });
}

function renderMoneyStats() {
  const investedStr = document.getElementById('in-invested').value;
  const wrap = document.getElementById('money-stats');
  const sharesNote = document.getElementById('money-shares-note');
  if (!lastResult) {
    wrap.classList.add('hidden'); wrap.hidden = true;
    sharesNote.classList.add('hidden'); sharesNote.hidden = true;
    return;
  }
  const invested = Math.max(0, Number(investedStr) || 0);
  document.getElementById('money-start').textContent = fmtMoney(invested);
  // 跟renderResult()一樣，金額試算的保守/積極用conservative_return/aggressive_return(P25/P75)。
  document.getElementById('money-p10').textContent = fmtMoney(invested * (1 + lastResult.conservative_return));
  document.getElementById('money-median').textContent = fmtMoney(invested * (1 + lastResult.median_return));
  document.getElementById('money-p90').textContent = fmtMoney(invested * (1 + lastResult.aggressive_return));
  wrap.classList.remove('hidden'); wrap.hidden = false;

  // 只能買整股，投入金額除以起始股價無條件捨去，剩下的零頭顯示出來，
  // 不然使用者會誤以為市值是用「買得到零股」的連續金額去算的。
  const startPrice = lastResult.start_price;
  if (startPrice > 0) {
    const shares = Math.floor(invested / startPrice);
    const leftover = invested - shares * startPrice;
    sharesNote.textContent = `以起始股價試算，約可買 ${shares} 股（找零 ${fmtMoney(leftover)}，實際下單以券商規則為準）`;
    sharesNote.classList.remove('hidden'); sharesNote.hidden = false;
  } else {
    sharesNote.classList.add('hidden'); sharesNote.hidden = true;
  }
}

document.getElementById('btn-run').addEventListener('click', runSimulation);
setMode('manual');

// ── Podcast-driven scenario (apps/calculator/services/scenario.py) ─────
// 有比對到這支股票的 podcast 分析時，樂觀/保守情境改用真實校準過的公式算，
// 圖表也改用後端算好的 GBM 模擬（基準區間帶 + 樂觀/保守示範線），
// 而不是單純的複利曲線。使用者手動改動任一個報酬率輸入框時，會自動退回複利曲線
// （因為後端模擬線是針對原始那組報酬率跑的，改動後線型跟數字就對不上了）。
let _scenarioNodes      = null;  // /stock-timeline/ 回傳的這支股票所有已分類集數
let _scenarioAnchorId   = null;  // 目前選定的集數 score_id
let _scenarioMode       = 'episode'; // 'episode'（當集原始預測）｜ 'weighted'（最新綜合預測）
let _scenarioSource     = null;  // /scenario/ 或 /scenario-weighted/ 的完整回應
let _scenarioChart      = null;  // 其中的 GBM 模擬資料（months/bull_line/base_line/bear_line/band/start_price）
let _scenarioDeltas     = null;  // { bullDelta, bearDelta }，讓使用者改動基準時仍套用同一組 spread/lean
let _scenarioAutoRates  = null;  // 自動帶入時的 {base, bull, bear}，用來判斷輸入框是否還跟後端模擬一致
let _scenarioFetchSeq   = 0;     // 每次抓 timeline/scenario 都遞增，讓過期的非同步回應可以被忽略（避免race condition）
let _pendingOriginEpisodeId = null; // 從 Deep Dive「試算」過來時指定的集數；抓完 timeline 後用一次就清掉
let _scenarioLoading    = false; // 是否還在抓 timeline/scenario，用來跟「已經查完、確定沒有資料」區分，
                                  // 避免查詢中途誤判成沒資料、或誤用還沒套用GBM模擬的複利曲線畫圖

function _currentSimMonths() {
  return Math.max(Math.round(getSimYears() * 12), 1);
}

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
  // 有比對到 podcast 分析：套用同一組 spread/lean（k/R_MAX/L_MAX/add 已用真實資料校準過），
  // 用 delta 而不是重新算 spread，是因為 spread 本身由 annual_vol/risk_score 決定、跟 base 無關，
  // 使用者改動 base 時，樂觀/保守應該跟著平移同樣的寬度，不是重新套 1.5x/0.5x。
  if (_scenarioDeltas) {
    return {
      bull: +(base + _scenarioDeltas.bullDelta).toFixed(1),
      bear: +Math.max(base - _scenarioDeltas.bearDelta, -90).toFixed(1),
    };
  }
  if (base === 0) return { bull: 5, bear: -5 };
  if (base > 0)   return { bull: +(base * 1.5).toFixed(1), bear: +(base * 0.5).toFixed(1) };
  return           { bull: +(base * 0.5).toFixed(1), bear: +(base * 1.5).toFixed(1) };
}

// ── Podcast scenario source (StockSentimentScore → scenario.py) ────────
// _scenarioFetchSeq：所有跟這個區塊有關的非同步請求（換股票/換集數/換模式/投資期間改變重抓GBM）
// 都共用同一個遞增序號。發出請求時記下當時的序號，回應回來時比對序號還是不是最新的，
// 不是的話直接丟棄——避免慢的舊請求晚回來蓋掉快的新請求（例如使用者連續切換股票或投資期間）。
function _sortScenarioNodes(nodes) {
  return nodes.slice().sort((a, b) => (a.published_at < b.published_at ? 1 : -1));
}

async function _fetchScenarioSource(ticker) {
  const seq = ++_scenarioFetchSeq;
  _scenarioNodes = null;
  _scenarioAnchorId = null;
  _scenarioMode = 'episode';
  _scenarioSource = null;
  _scenarioChart = null;
  _scenarioDeltas = null;
  _scenarioAutoRates = null;
  _scenarioLoading = true;
  const originEpisodeId = _pendingOriginEpisodeId;
  _pendingOriginEpisodeId = null;
  _renderScenarioSourceUI();

  try {
    const res  = await fetch(`/api/calculator/stock-timeline/?ticker=${encodeURIComponent(ticker)}`);
    const data = await res.json();
    if (seq !== _scenarioFetchSeq) return; // 已經有更新的請求發出，這筆回應過期了
    _scenarioNodes = (res.ok && data.nodes) ? _sortScenarioNodes(data.nodes) : [];

    let anchor = originEpisodeId != null
      ? _scenarioNodes.find(n => n.episode_id != null && String(n.episode_id) === String(originEpisodeId))
      : null;

    // 從單集摘要頁「試算」過來，但那一集（甚至整支股票）還沒被排進 timeline，可能還沒跑過批次分類：
    // 當場算一次、存進資料庫、補進清單，而不是默默顯示「尚無資料」。這裡不能只在 _scenarioNodes 非空時才嘗試，
    // 批次分類還沒涵蓋到的股票才是最常見的情況（timeline 整個是空的），這時候更需要靠這個 fallback 補上。
    if (originEpisodeId != null && !anchor) {
      try {
        const ensureRes  = await fetch(`/api/calculator/ensure-episode-score/?episode_id=${encodeURIComponent(originEpisodeId)}&ticker=${encodeURIComponent(ticker)}`);
        const ensureData = await ensureRes.json();
        if (seq !== _scenarioFetchSeq) return;
        if (ensureRes.ok) {
          anchor = ensureData;
          if (!_scenarioNodes.some(n => n.score_id === ensureData.score_id)) {
            _scenarioNodes = _sortScenarioNodes([..._scenarioNodes, ensureData]);
          }
        }
      } catch (e) { /* 算不出來就照舊往下走，交給下面的空狀態判斷 */ }
    }

    if (!_scenarioNodes.length) { _scenarioNodes = null; _scenarioLoading = false; _renderScenarioSourceUI(); return; }

    await _loadScenarioForScoreId((anchor || _scenarioNodes[0]).score_id, 'episode', seq);
  } catch (e) {
    if (seq !== _scenarioFetchSeq) return;
    _scenarioLoading = false;
    _renderScenarioSourceUI();
  }
}

async function _loadScenarioForScoreId(scoreId, mode, seq, baseOverridePct) {
  if (seq === undefined) seq = ++_scenarioFetchSeq;
  _scenarioAnchorId = scoreId;
  _scenarioMode = mode;
  _scenarioLoading = true;
  const endpoint = mode === 'weighted' ? 'scenario-weighted' : 'scenario';
  const months = _currentSimMonths();
  // baseOverridePct 只有在「使用者自己改了基準情境、但沒有直接手動改樂觀/保守」時才會傳進來，
  // 讓後端用這個新base重新算一次GBM模擬（spread/lean不變），而不是整個放棄GBM退回複利曲線。
  const baseParam = baseOverridePct != null ? `&base=${encodeURIComponent(baseOverridePct)}` : '';
  try {
    const res  = await fetch(`/api/calculator/${endpoint}/?score_id=${encodeURIComponent(scoreId)}&months=${months}${baseParam}`);
    const data = await res.json();
    if (seq !== _scenarioFetchSeq) return; // 過期回應，忽略
    if (!res.ok) {
      _scenarioSource = null; _scenarioChart = null; _scenarioDeltas = null; _scenarioAutoRates = null;
      _scenarioLoading = false;
      _renderScenarioSourceUI();
      return;
    }
    _scenarioSource = data;
    _scenarioChart = {
      months: data.months,
      bull_line: data.bull_line,
      base_line: data.base_line,
      bear_line: data.bear_line,
      base_band_low: data.base_band_low,
      base_band_high: data.base_band_high,
      actual_line: data.actual_line, // 這段期間實際發生的股價；還沒到的月份是 null
      start_price: data.start_price,
    };
    _scenarioLoading = false;
    _applyScenarioSourceToInputs(); // 內部會呼叫 _refreshIfActive()，圖表因此拿到最新資料重新畫一次
    _renderScenarioSourceUI();
  } catch (e) {
    if (seq !== _scenarioFetchSeq) return;
    _scenarioSource = null; _scenarioChart = null; _scenarioDeltas = null; _scenarioAutoRates = null;
    _scenarioLoading = false;
    _renderScenarioSourceUI();
  }
}

// B方案(歷史拔靴模擬)的樂觀/基準/保守是模擬跑出來的P70/中位數/P30，不是使用者可以
// 直接輸入、後端拿去重算的假設值(不像舊版spread/lean公式那樣可以從base反推)，
// 所以有podcast分析資料時這三個欄位要設成唯讀，只在「完全沒有podcast資料」的
// 純手動輸入fallback情境才開放編輯（見 _renderScenarioSourceUI 的無資料分支）。
function _setYieldInputsReadonly(readonly) {
  ['sim-yield-base', 'sim-yield-bull', 'sim-yield-bear'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.readOnly = readonly;
    el.classList.toggle('opacity-60', readonly);
    el.classList.toggle('cursor-not-allowed', readonly);
  });
  const note = document.getElementById('scenario-hint-editable-note');
  if (note) note.classList.toggle('hidden', readonly);
}

function _applyScenarioSourceToInputs() {
  if (!_scenarioSource) return;
  const r = _scenarioSource.scenario_returns;
  const base = +(r.base * 100).toFixed(1);
  const bull = +(r.bull * 100).toFixed(1);
  const bear = +(r.bear * 100).toFixed(1);
  _scenarioDeltas = { bullDelta: bull - base, bearDelta: base - bear };
  _scenarioAutoRates = { base, bull, bear };
  _bullUserEdited = false;
  _bearUserEdited = false;
  document.getElementById('sim-yield-base').value = base;
  document.getElementById('sim-yield-bull').value = bull;
  document.getElementById('sim-yield-bear').value = bear;
  _setYieldInputsReadonly(true);
  _refreshIfActive();
}

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

// 加權模式下，只有「日期 ≥ 目前選中這集」的集數才會真的被納入計算（跟後端
// compute_time_weighted_scores 的 eligible 篩選邏輯一致，純前端算，不用多打API），
// 用這個判斷「綜合觀點」現在切換過去是否有意義。
function _eligibleWeightedNodes(anchorScoreId) {
  if (!_scenarioNodes) return [];
  const anchor = _scenarioNodes.find(n => String(n.score_id) === String(anchorScoreId));
  if (!anchor) return [];
  return _scenarioNodes.filter(n => n.published_at >= anchor.published_at);
}

function setScenarioMode(mode) {
  if (!_scenarioAnchorId || mode === _scenarioMode) return;
  if (mode === 'weighted' && _eligibleWeightedNodes(_scenarioAnchorId).length <= 1) return; // 按鈕本身會被 disable 擋掉，這裡多一層保險
  _loadScenarioForScoreId(_scenarioAnchorId, mode);
}

function onScenarioEpisodeChange() {
  const select = document.getElementById('scenario-episode-select');
  if (!select || !select.value) return;
  // 換到的這集在加權模式下已經沒有其他集數可加權（例如換到最新一集）：自動退回當集模式
  const mode = (_scenarioMode === 'weighted' && _eligibleWeightedNodes(select.value).length <= 1)
    ? 'episode' : _scenarioMode;
  _loadScenarioForScoreId(select.value, mode);
}

function _renderScenarioSourceUI() {
  const panel = document.getElementById('scenario-source-panel');
  const empty = document.getElementById('scenario-source-empty');
  const hintDefault = document.getElementById('scenario-hint-default');
  const hintPodcast = document.getElementById('scenario-hint-podcast');
  const hintLabel   = document.getElementById('scenario-hint-label');
  if (!panel || !empty) return;

  if (!_scenarioNodes || !_scenarioNodes.length) {
    panel.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = _scenarioLoading
      ? '正在查詢 Podcast 分析中…'
      : '此股票尚無 Podcast 分析紀錄，樂觀/保守情境為系統自動推算，你也可以自行輸入。';
    if (hintDefault) hintDefault.classList.remove('hidden');
    if (hintPodcast) hintPodcast.classList.add('hidden');
    if (hintLabel) hintLabel.textContent = '依基準情境自動推算，可自行修改';
    _setYieldInputsReadonly(false); // 沒有podcast資料時退回純手動輸入，三個欄位都可以編輯
    return;
  }
  empty.classList.add('hidden');
  panel.classList.remove('hidden');
  if (hintDefault) hintDefault.classList.add('hidden');
  if (hintPodcast) hintPodcast.classList.remove('hidden');
  if (hintLabel) hintLabel.textContent = '依 Podcast 分析歷史拔靴模擬算出，唯讀';

  const eligible = _eligibleWeightedNodes(_scenarioAnchorId);
  const weightedDisabled = eligible.length <= 1;

  // 「當集」跟「綜合觀點」共用同一個下拉選單：當集列出這支股票所有集數，
  // 綜合觀點只列出「日期 ≥ 目前選中這集」真的會被加權到的集數（eligible），選別的集數
  // 就是把加權的錨點換掉，跟當集模式換集數是同一套邏輯（見 onScenarioEpisodeChange）。
  const select = document.getElementById('scenario-episode-select');
  if (select) {
    const options = _scenarioMode === 'weighted' ? eligible : _scenarioNodes;
    select.innerHTML = options.map(n => {
      const podcaster = n.podcaster ? `・${n.podcaster}` : '';
      return `<option value="${n.score_id}">${n.published_at || '未知日期'}${podcaster}　${_macroLabel(n.macro_score)}／${_riskLabel(n.risk_score)}</option>`;
    }).join('');
    if (_scenarioAnchorId) select.value = _scenarioAnchorId;
  }

  const activeCls   = 'text-xs font-bold px-3 py-1 rounded-full bg-tertiary-container text-white transition-colors';
  const inactiveCls = 'text-xs font-bold px-3 py-1 rounded-full text-outline hover:bg-surface-container transition-colors';
  const disabledCls = 'text-xs font-bold px-3 py-1 rounded-full text-outline/30 cursor-not-allowed';
  const epBtn = document.getElementById('scenario-mode-episode');
  const wtBtn = document.getElementById('scenario-mode-weighted');
  if (epBtn) epBtn.className = _scenarioMode === 'episode'  ? activeCls : inactiveCls;
  if (wtBtn) {
    wtBtn.disabled = weightedDisabled;
    wtBtn.title = weightedDisabled ? '這已經是最新一集，沒有其他集數可以加權' : '';
    wtBtn.className = weightedDisabled ? disabledCls : (_scenarioMode === 'weighted' ? activeCls : inactiveCls);
  }

  const detail = document.getElementById('scenario-source-detail');
  if (detail && _scenarioSource) {
    const riskLabel  = _scenarioSource.risk_score >= 1 ? '高' : _scenarioSource.risk_score >= 0.5 ? '中' : '低';
    const macroLabel = _scenarioSource.macro_score > 0 ? '樂觀' : _scenarioSource.macro_score < 0 ? '悲觀' : '中性';
    let html;
    if (_scenarioMode === 'weighted' && _scenarioSource.n_episodes != null) {
      // 哪些集數被加權到、要選別的集數當加權錨點，都靠上面跟「當集」共用的下拉選單處理，
      // 這裡只顯示彙總後的結果，不再重複列一次集數清單。
      html = `綜合 <b>${_scenarioSource.n_episodes}</b> 集節目看法（半衰期90天加權，越新集數影響越大）：風險<b>${riskLabel}</b>・展望<b>${macroLabel}</b>`;
      if (_scenarioSource.top_contributors && _scenarioSource.top_contributors.length) {
        const names = _scenarioSource.top_contributors.map(c => `${c.published_at}${c.podcaster ? '・' + c.podcaster : ''}`).join('、');
        html += `<div class="mt-1 text-on-surface-variant">主要依據：${names}</div>`;
      }
    } else {
      html = `${_scenarioSource.published_at || ''} 節目判斷：風險<b>${riskLabel}</b>・展望<b>${macroLabel}</b>`;
      if (_scenarioSource.rationale) {
        const snippet = _scenarioSource.rationale.length > 80 ? _scenarioSource.rationale.slice(0, 80) + '…' : _scenarioSource.rationale;
        html += `<div class="mt-1 italic text-on-surface-variant">「${snippet}」</div>`;
      }
      html += _renderTopicExcerpt(_scenarioSource.topic_summary);
      if (_scenarioSource.summary_id != null) {
        html += `<div class="mt-2"><button type="button" onclick="_backOverride='calculator'; openDeepDive(${_scenarioSource.summary_id})" class="text-secondary font-bold text-xs hover:underline">查看完整摘要 →</button></div>`;
      }
    }
    detail.innerHTML = html;
  }
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

// 個股專屬段落原文，預設截斷、可展開，避免把側邊欄撐太長
function _renderTopicExcerpt(text) {
  if (!text) return '';
  if (text.length <= 140) return `<div class="mt-1 text-on-surface-variant">${text}</div>`;
  const excerptId = '_topic_excerpt_' + Math.random().toString(36).slice(2, 8);
  const short = text.slice(0, 140) + '…';
  return `
    <div class="mt-1">
      <span id="${excerptId}-short" class="text-on-surface-variant">${short}</span>
      <span id="${excerptId}-full" class="hidden text-on-surface-variant">${text}</span>
      <button type="button" onclick="_toggleTopicExcerpt('${excerptId}', this)" class="text-secondary font-bold text-xs ml-1 align-baseline hover:underline">展開</button>
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

// 舊版「投入金額×複利」試算已經被B方案(歷史拔靴模擬，見本檔案下方 runSimulation())取代，
// 這個函式現在什麼都不做——保留空函式只是因為 base.html 開機腳本目前還會呼叫它一次，
// 拿掉呼叫端之前先留著避免噴錯。
function calcWealth() {}

function _showScenarioChartLoading() {
  const svg = document.getElementById('scenario-svg');
  if (!svg) return;
  svg.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';
  const t = document.createElementNS(ns, 'text');
  t.setAttribute('x', '350'); t.setAttribute('y', '130');
  t.setAttribute('text-anchor', 'middle');
  t.setAttribute('fill', '#9b9e9f');
  t.setAttribute('font-family', 'Manrope'); t.setAttribute('font-size', '14'); t.setAttribute('font-weight', '600');
  t.textContent = '圖表載入中…';
  svg.appendChild(t);
  const gbmNote = document.getElementById('scenario-gbm-note');
  if (gbmNote) gbmNote.classList.add('hidden');
  const actualNote = document.getElementById('scenario-actual-note');
  if (actualNote) actualNote.classList.add('hidden');
  const rangeNote = document.getElementById('scenario-range-note');
  if (rangeNote) rangeNote.classList.add('hidden');
}

function _fmtVal(v) {
  if (Math.abs(v) >= 100000000) return (v / 100000000).toFixed(1) + '億';
  if (Math.abs(v) >= 10000000)  return (v / 10000).toFixed(0) + '萬';
  if (Math.abs(v) >= 1000000)   return (v / 10000).toFixed(1) + '萬';
  if (Math.abs(v) >= 100000)    return (v / 10000).toFixed(1) + '萬';
  return Math.round(v).toLocaleString();
}

function renderScenarioChart(invested, months, bullRate, baseRate, bearRate, gbmChart) {
  const svg = document.getElementById('scenario-svg');
  svg.innerHTML = '';

  const W = 700, H = 260, padL = 72, padR = 24, padT = 16, padB = 40;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const ns = 'http://www.w3.org/2000/svg';

  let plotMonths, bullPts, basePts, bearPts, bandLow, bandHigh, actualPts;

  if (gbmChart) {
    // 後端已經用真實校準過的公式 + GBM 模擬跑好整條路徑（見 apps/calculator/services/scenario.py），
    // 這裡只需要把「股價路徑」換算成「持倉市值路徑」（乘上 invested/start_price 的縮放比例）。
    plotMonths = gbmChart.months.length - 1;
    const scale = gbmChart.start_price > 0 ? invested / gbmChart.start_price : 0;
    bullPts  = gbmChart.bull_line.map(p => p * scale);
    basePts  = gbmChart.base_line.map(p => p * scale);
    bearPts  = gbmChart.bear_line.map(p => p * scale);
    bandLow  = gbmChart.base_band_low.map(p => p * scale);
    bandHigh = gbmChart.base_band_high.map(p => p * scale);
    // 還沒到的月份後端回 null，這裡保留 null（不是0），畫線時會自動在那裡斷開，不會硬畫到0。
    actualPts = gbmChart.actual_line ? gbmChart.actual_line.map(p => (p == null ? null : p * scale)) : null;
  } else {
    plotMonths = months;
    function genPoints(annualRate) {
      if (annualRate == null || isNaN(annualRate)) return null;
      const mr = Math.pow(1 + annualRate / 100, 1 / 12) - 1;
      return Array.from({ length: months + 1 }, (_, n) => invested * Math.pow(1 + mr, n));
    }
    bullPts = genPoints(bullRate);
    basePts = genPoints(baseRate);
    bearPts = genPoints(bearRate);
    bandLow = bandHigh = actualPts = null;
  }

  const gbmNote = document.getElementById('scenario-gbm-note');
  if (gbmNote) gbmNote.classList.toggle('hidden', !gbmChart);
  const actualNote = document.getElementById('scenario-actual-note');
  const hasActual = !!(actualPts && actualPts.some(v => v != null));
  if (actualNote) actualNote.classList.toggle('hidden', !hasActual);
  const legendActualRow = document.getElementById('legend-actual-row');
  if (legendActualRow) {
    legendActualRow.classList.toggle('hidden', !hasActual);
    legendActualRow.style.display = hasActual ? 'flex' : 'none';
  }

  const allVals = [...(bullPts || []), ...basePts, ...(bearPts || []), ...(bandLow || []), ...(bandHigh || []),
    ...((actualPts || []).filter(v => v != null))];
  const dataMin = Math.min(...allVals);
  const dataMax = Math.max(...allVals);

  let hi, lo;
  if (gbmChart) {
    // GBM 路徑本身有震盪，不是單調曲線，最終值不等於極值，直接包住全部資料點加緩衝即可
    const pad = (dataMax - dataMin) * 0.08 || invested * 0.1;
    hi = dataMax + pad;
    lo = dataMin - pad;
  } else {
    // Y-axis: 上限 = 樂觀情境最終值 +10%，下限 = 保守情境最終值 -10%
    // 複利曲線單調遞增/遞減，最終值即整條線的極值；若情境值為負則改用加法緩衝避免方向反轉
    const bullFinal = bullPts ? bullPts[plotMonths] : dataMax;
    const bearFinal = bearPts ? bearPts[plotMonths] : dataMin;
    const margin = (Math.abs(bullFinal) + Math.abs(bearFinal)) * 0.05 || invested * 0.1;
    hi = bullFinal >= 0 ? bullFinal * 1.1 : bullFinal - margin;
    lo = bearFinal >= 0 ? bearFinal * 0.9 : bearFinal + margin;
    // 保險：若資料範圍超出上述邊界（極端情況），擴張包住全部資料
    hi = Math.max(hi, dataMax);
    lo = Math.min(lo, dataMin);
  }

  const toX = n => padL + (n / plotMonths) * (W - padL - padR);
  const toY = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  // Adaptive X-axis labels
  function getXLabels() {
    let interval, fmt;
    if (plotMonths < 3) {
      interval = 1;
      fmt = n => n === 0 ? '現在' : `第${n}月`;
    } else if (plotMonths <= 12) {
      interval = Math.ceil(plotMonths / 6);
      fmt = n => n === 0 ? '現在' : `第${n}月`;
    } else if (plotMonths <= 36) {
      interval = 3;
      fmt = n => n === 0 ? '現在' : `第${Math.round(n / 3)}季`;
    } else {
      interval = 6;
      fmt = n => n === 0 ? '現在' : `第${Math.round(n / 12)}年`;
    }
    const pts = [];
    for (let n = 0; n <= plotMonths; n += interval) pts.push({ n, label: fmt(n) });
    if (pts[pts.length - 1].n !== plotMonths) pts.push({ n: plotMonths, label: fmt(plotMonths) });
    return pts;
  }

  // Statistical band (基準情境 1000 次模擬的 10~90 百分位區間)，只有走 GBM 分支時才有
  if (bandLow && bandHigh) {
    let d = `M${toX(0).toFixed(1)},${toY(bandLow[0]).toFixed(1)}`;
    for (let i = 1; i < bandLow.length; i++) d += ` L${toX(i).toFixed(1)},${toY(bandLow[i]).toFixed(1)}`;
    for (let i = bandHigh.length - 1; i >= 0; i--) d += ` L${toX(i).toFixed(1)},${toY(bandHigh[i]).toFixed(1)}`;
    d += ' Z';
    const band = document.createElementNS(ns, 'path');
    band.setAttribute('d', d);
    band.setAttribute('fill', '#113236');
    band.setAttribute('fill-opacity', '0.10');
    band.setAttribute('stroke', 'none');
    svg.appendChild(band);
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

  // Draw lines — null 值會斷開成新的一段，不會硬連過去（給實際股價線用，還沒到的月份是null）
  function drawLine(pts, color, dash) {
    if (!pts) return;
    let d = '';
    let started = false;
    pts.forEach((v, i) => {
      if (v == null) { started = false; return; }
      d += (started ? 'L' : 'M') + `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`;
      started = true;
    });
    if (!d) return;
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
  drawLine(actualPts, '#4a4a4a'); // 實際股價，畫在最上層最顯眼

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
  if (bandRow) bandRow.className = (bandLow && bandHigh)
    ? 'flex items-center gap-2'
    : 'hidden items-center gap-2';

  // Range note: difference between bull and bear at end
  const rangeNote = document.getElementById('scenario-range-note');
  const rangeVal  = document.getElementById('scenario-range-val');
  if (rangeNote && rangeVal && bullPts && bearPts) {
    const diff = Math.abs(bullPts[plotMonths] - bearPts[plotMonths]);
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
  const actualDot = actualPts ? makeDot('#4a4a4a') : null;

  svg.appendChild(overlay);

  function getXLabel(n) {
    if (n === 0) return '現在';
    if (plotMonths <= 36) return `第 ${n} 月`;
    return `第 ${Math.round(n / 12)} 年`;
  }

  hitRect.addEventListener('mousemove', e => {
    const rect = svg.getBoundingClientRect();
    const scaleX = W / rect.width;
    const mx = (e.clientX - rect.left) * scaleX;
    const rawN = Math.round((mx - padL) / (W - padL - padR) * plotMonths);
    const n = Math.max(0, Math.min(plotMonths, rawN));
    const x = toX(n);

    vLine.setAttribute('x1', x); vLine.setAttribute('x2', x);
    vLine.setAttribute('visibility', 'visible');

    const bv  = basePts[n];
    const buv = bullPts ? bullPts[n] : null;
    const bev = bearPts ? bearPts[n] : null;
    const av  = actualPts ? actualPts[n] : null;

    if (baseDot) { baseDot.setAttribute('cx', x); baseDot.setAttribute('cy', toY(bv)); baseDot.setAttribute('visibility', 'visible'); }
    if (bullDot && buv != null) { bullDot.setAttribute('cx', x); bullDot.setAttribute('cy', toY(buv)); bullDot.setAttribute('visibility', 'visible'); }
    if (bearDot && bev != null) { bearDot.setAttribute('cx', x); bearDot.setAttribute('cy', toY(bev)); bearDot.setAttribute('visibility', 'visible'); }
    if (actualDot) {
      if (av != null) { actualDot.setAttribute('cx', x); actualDot.setAttribute('cy', toY(av)); actualDot.setAttribute('visibility', 'visible'); }
      else actualDot.setAttribute('visibility', 'hidden');
    }

    const sign = v => v >= invested ? '+' : '';
    const fv = v => 'NT$' + Math.round(v).toLocaleString();
    let html = `<div style="font-weight:700;margin-bottom:4px;color:#aecfd4">${getXLabel(n)}</div>`;
    if (buv != null) html += `<div><span style="color:#4fb3c1">▲ 樂觀</span>　${fv(buv)}</div>`;
    html += `<div><span style="color:#8ba9ae">● 基準</span>　${fv(bv)}</div>`;
    if (bev != null) html += `<div><span style="color:#e57373">▼ 保守</span>　${fv(bev)}</div>`;
    if (av != null) html += `<div><span style="color:#bcbcbc">■ 實際</span>　${fv(av)}</div>`;
    foDiv.innerHTML = html;

    // Position tooltip: left or right of crosshair
    const foW = 165, foH = av != null ? 130 : 110;
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
    if (actualDot) actualDot.setAttribute('visibility', 'hidden');
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
  _scenarioNodes = null; _scenarioAnchorId = null; _scenarioSource = null;
  _scenarioChart = null; _scenarioDeltas = null; _scenarioAutoRates = null;
  _renderScenarioSourceUI();
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
    _highlightPeriodBtn(bestPeriod);
    renderStockChart(data);
    _fetchAndRenderChart(ticker, bestPeriod);
    fetchStockNews(ticker);
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
  const bestPeriod = '1y'; // 試算模擬器改用拉桿選投資期間(月)，不再有起訖日期，固定用1年當走勢圖預設區間
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

// ids 可選：讓其他頁面（如 assets.js 的標的詳情頁）重用同一份渲染邏輯，
// 只要傳自己的 DOM id 對照表就好，不用複製整份函式。不傳就用預設值，
// calculator 頁面本身的呼叫方式完全不用改。
function renderStockChart(data, ids = {}) {
  const svg    = document.getElementById(ids.svg || 'stock-svg');
  svg.innerHTML = '';
  svg.classList.remove('hidden');
  document.getElementById(ids.placeholder || 'stock-chart-placeholder').classList.add('hidden');
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

  // ── Crosshair + price tooltip：滑鼠/手指移動時，貼齊最近的資料點畫十字線 + 提示框 ──
  const chGroup = document.createElementNS(ns, 'g');
  chGroup.style.display = 'none';
  chGroup.style.pointerEvents = 'none';

  const hLine = document.createElementNS(ns, 'line');
  hLine.setAttribute('x1', padL); hLine.setAttribute('x2', W - padR);
  hLine.setAttribute('stroke', '#717879'); hLine.setAttribute('stroke-width', '1');
  hLine.setAttribute('stroke-dasharray', '4,3');
  chGroup.appendChild(hLine);

  const vLine = document.createElementNS(ns, 'line');
  vLine.setAttribute('y1', padT); vLine.setAttribute('y2', H - padB);
  vLine.setAttribute('stroke', '#717879'); vLine.setAttribute('stroke-width', '1');
  vLine.setAttribute('stroke-dasharray', '4,3');
  chGroup.appendChild(vLine);

  const chDot = document.createElementNS(ns, 'circle');
  chDot.setAttribute('r', '4');
  chDot.setAttribute('fill', lineColor);
  chDot.setAttribute('stroke', '#fff');
  chDot.setAttribute('stroke-width', '1.5');
  chGroup.appendChild(chDot);

  const ttBg = document.createElementNS(ns, 'rect');
  ttBg.setAttribute('rx', '6');
  ttBg.setAttribute('fill', '#113236');
  chGroup.appendChild(ttBg);

  const ttDate = document.createElementNS(ns, 'text');
  ttDate.setAttribute('fill', '#ffffff'); ttDate.setAttribute('font-family', 'Manrope');
  ttDate.setAttribute('font-size', '11'); ttDate.setAttribute('font-weight', '600');
  chGroup.appendChild(ttDate);

  const ttPrice = document.createElementNS(ns, 'text');
  ttPrice.setAttribute('fill', '#ffffff'); ttPrice.setAttribute('font-family', 'Epilogue');
  ttPrice.setAttribute('font-size', '13'); ttPrice.setAttribute('font-weight', '700');
  chGroup.appendChild(ttPrice);

  svg.appendChild(chGroup);

  function moveCrosshairTo(clientX) {
    // 用 SVG 自己的座標轉換 API，才能正確處理 preserveAspectRatio 縮放/置中造成的落差，
    // 不能單純用畫面寬度去除以 viewBox 寬度換算。
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    const svgX = pt.matrixTransform(ctm.inverse()).x;

    let idx = Math.round(((svgX - padL) / (W - padL - padR)) * (n - 1));
    idx = Math.max(0, Math.min(n - 1, idx));

    const px = toX(idx), py = toY(prices[idx].close);
    hLine.setAttribute('y1', py); hLine.setAttribute('y2', py);
    vLine.setAttribute('x1', px); vLine.setAttribute('x2', px);
    chDot.setAttribute('cx', px); chDot.setAttribute('cy', py);

    ttDate.textContent  = prices[idx].date;
    ttPrice.textContent = 'NT$' + prices[idx].close.toFixed(2);
    ttDate.setAttribute('x', 0); ttDate.setAttribute('y', 16);
    ttPrice.setAttribute('x', 0); ttPrice.setAttribute('y', 34);
    const boxW = Math.max(ttDate.getBBox().width, ttPrice.getBBox().width) + 20;
    const boxH = 42;
    let boxX = px + 12;
    if (boxX + boxW > W - padR) boxX = px - boxW - 12; // 靠右邊界時翻到左邊，避免被裁掉
    boxX = Math.max(padL, boxX);
    const boxY = Math.max(padT, Math.min(py - boxH / 2, H - padB - boxH));

    ttBg.setAttribute('x', boxX); ttBg.setAttribute('y', boxY);
    ttBg.setAttribute('width', boxW); ttBg.setAttribute('height', boxH);
    ttDate.setAttribute('x', boxX + 10); ttDate.setAttribute('y', boxY + 16);
    ttPrice.setAttribute('x', boxX + 10); ttPrice.setAttribute('y', boxY + 34);

    chGroup.style.display = '';
  }

  const hitOverlay = document.createElementNS(ns, 'rect');
  hitOverlay.setAttribute('x', 0); hitOverlay.setAttribute('y', 0);
  hitOverlay.setAttribute('width', W); hitOverlay.setAttribute('height', H);
  hitOverlay.setAttribute('fill', 'transparent');
  hitOverlay.style.cursor = 'crosshair';
  hitOverlay.addEventListener('mousemove', e => moveCrosshairTo(e.clientX));
  hitOverlay.addEventListener('mouseleave', () => { chGroup.style.display = 'none'; });
  hitOverlay.addEventListener('touchmove', e => {
    if (e.touches[0]) moveCrosshairTo(e.touches[0].clientX);
    e.preventDefault();
  }, { passive: false });
  hitOverlay.addEventListener('touchend', () => { chGroup.style.display = 'none'; });
  svg.appendChild(hitOverlay);

  const lastClose = prices[n - 1].close;
  const change    = ((lastClose - prices[0].close) / prices[0].close * 100).toFixed(2);
  const annualizedReturn = (stockPeriod === '3mo'
    ? (Math.pow(1 + parseFloat(change) / 100, 4) - 1) * 100
    : stockPeriod === '6mo'
    ? (Math.pow(1 + parseFloat(change) / 100, 2) - 1) * 100
    : stockPeriod === '2y'
    ? (Math.pow(1 + parseFloat(change) / 100, 0.5) - 1) * 100
    : parseFloat(change)).toFixed(1);

  document.getElementById(ids.name || 'stock-name').textContent          = data.name;
  document.getElementById(ids.lastPrice || 'stock-last-price').textContent    = 'NT$' + lastClose.toFixed(1);
  document.getElementById(ids.subtitle || 'stock-chart-subtitle').textContent = data.name;
  // 台股慣例是漲＝紅、跌＝綠，跟美股配色相反，跟 ASSETS 頁面排名列表用同一組顏色
  // （見 static/js/assets.js 的 _assetRowHtml）。
  const changeEl = document.getElementById(ids.change || 'stock-change');
  changeEl.textContent = (change >= 0 ? '+' : '') + change + '%';
  changeEl.className   = `font-['Epilogue'] font-bold text-lg ${isUp ? 'text-[#ba1a1a]' : 'text-[#1e8e3e]'}`;
  const histEl = document.getElementById(ids.histReturn || 'stock-hist-return');
  histEl.textContent = (annualizedReturn >= 0 ? '+' : '') + annualizedReturn + '%';
  histEl.className   = `font-['Epilogue'] font-bold text-lg ${annualizedReturn >= 0 ? 'text-[#ba1a1a]' : 'text-[#1e8e3e]'}`;
  document.getElementById(ids.info || 'stock-info').classList.remove('hidden');
  document.getElementById(ids.dataAsof || 'stock-data-asof').textContent = '資料更新至 ' + prices[n - 1].date;
}

// ── Stock news ────────────────────────────────────────────────
// ids 可選，同 renderStockChart() 的做法。
async function fetchStockNews(ticker, ids = {}) {
  const section = document.getElementById(ids.section || 'stock-news-section');
  const list    = document.getElementById(ids.list || 'stock-news-list');
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
  _currentTicker = '';
  _lastResolvedInputText = null;
  _hideStockCandidates();
  stockCurrentPrice = 0;

  // B方案(歷史拔靴模擬)區塊重置回預設狀態
  setMode('manual');
  document.getElementById('in-ticker').value = '2330.TW';
  document.getElementById('in-months').value = 12;
  document.getElementById('slider-months').value = 12;
  document.getElementById('lbl-months').textContent = 12;
  setScoreButton('risk', 0);
  setScoreButton('macro', 0);
  document.getElementById('in-invested').value = '';
  document.getElementById('slider-invested').value = '0';
  document.getElementById('result-panel').classList.add('hidden');
  const placeholderEl = document.getElementById('calc-placeholder');
  placeholderEl.classList.remove('hidden');
  placeholderEl.hidden = false;
  document.getElementById('status-text').textContent = '';
  lastResult = null;
}

// 從 deep_dive 頁面某一筆待驗證預測的「試算」按鈕跳轉過來：切到B方案的「從節目挑選」
// 模式，並記下目標episode/ticker，等節目清單載入完成後自動選中對應那一集(見loadEpisodes)。
let _pendingCalcTarget = null;

function goToCalculatorWithStock(stockName, endDate, originEpisodeId) {
  _pendingCalcTarget = { ticker: stockName, episodeId: originEpisodeId != null ? originEpisodeId : null };
  showPage('calculator');
  setMode('episode');
  _applyPendingCalcTarget();
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
