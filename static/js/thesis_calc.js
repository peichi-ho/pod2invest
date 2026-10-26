// ── 論點情境試算(計算機頁面的第二個模擬器) ─────────────────────────────────
// 上方選集 → 切到「論點情境試算」→ ① 勾選情境(可以都不勾) → ② 試算期間與參數(選填) → ③ 按「開始試算」
// → 後端推算試算期間結束時的 EPS 與目標價。結果只在按下「開始試算」後顯示，之後再改條件會提示重新試算，
// 避免使用者一打開就看到一堆數字、不知道是怎麼來的(2026-10-26 調整)。
// 跟「股價模擬」(歷史股價拔靴模擬)共用上方的選標的、選集，但結果分開顯示、不互相比較——
// 兩者計算方式完全不同，拔靴模擬也不是有公信力的基準(2026-10-26 起拿掉「落在第幾百分位」)。
// 計算都在後端(apps/calculator/thesis_views.py)，這裡只負責模擬器切換、勾選狀態、參數覆寫與畫面。
// 選集共用上方「選擇投資標的」(calculator.js)；依賴 calculator.js 的 _escHtml()、mode。

const THESIS_API_URL = '/api/calculator/thesis-valuation/';

const _thesis = {
  scoreId: null,
  thesisId: null,
  checked: new Set(),
  overrides: {},       // 使用者手動拉過的參數，key 同後端 levers
  months: null,        // 使用者自己改的試算期間；null = 用這句論點的回測期間
  data: null,          // 最新一次 API 回應(條件一改就重抓，用來更新情境清單與參數預設值)
  shown: null,         // 按「開始試算」時的回應，結果區只顯示這份
  stale: false,        // 按過「開始試算」之後條件又改了
  seq: 0,              // 請求序號，過期的回應不要蓋掉新的結果
  timer: null,
};

// 帶數字的情境同一時間只能選一個：EPS 只能直接指定一個值、本益比也只能一個
function _thesisExclusiveGroup(s) {
  if (s.type === 'EPS_FORECAST' && s.figures.eps) return 'eps';
  if ((s.type === 'RERATING_UP' || s.type === 'RERATING_DOWN') && s.figures.pe) return 'pe';
  return null;
}

// ── 模擬器切換 ──
// 上方「選擇投資標的」兩個模擬器共用；下面一次只顯示一個，結果不放在一起比較。
// 論點情境試算一定要有節目論點，所以只在「從節目挑選」模式可以切過來。
let _calcModule = 'sim';
let _thesisPendingScore = null;   // 上方目前選的那一集；切到論點情境試算時才載入，避免沒在看也去抓資料

function showCalcModule(m) {
  if (m === 'thesis' && typeof mode !== 'undefined' && mode !== 'episode') return;
  _calcModule = m;
  ['sim', 'thesis'].forEach(k => {
    const on = k === m;
    document.getElementById('calc-mod-' + k).classList.toggle('hidden', !on);
    const btn = document.getElementById('calc-mod-btn-' + k);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.classList.toggle('bg-tertiary-container', on);
    btn.classList.toggle('text-white', on);
    btn.classList.toggle('text-on-surface-variant', !on);
  });
  if (m === 'thesis' && _thesisPendingScore && String(_thesis.scoreId) !== String(_thesisPendingScore)) {
    loadThesisValuation(_thesisPendingScore);
  }
}

// calculator.js 的 setMode() 會呼叫：手動輸入時停用論點情境試算，並切回股價模擬
function onCalcModeChanged(m) {
  const btn = document.getElementById('calc-mod-btn-thesis');
  if (!btn) return;
  const ok = m === 'episode';
  btn.disabled = !ok;
  btn.title = ok ? '' : '論點情境試算需要「從節目挑選」';
  document.getElementById('calc-mod-hint').textContent = ok ? '' : '論點情境試算需要先切到「從節目挑選」';
  if (!ok && _calcModule === 'thesis') showCalcModule('sim');
}

// calculator.js 的 onEpisodeChange() 會呼叫：上方換了一集
function onCalcEpisodeChanged(scoreId) {
  _thesisPendingScore = scoreId;
  if (_calcModule === 'thesis') loadThesisValuation(scoreId);
}

function loadThesisValuation(scoreId) {
  _thesis.scoreId = scoreId;
  _thesis.thesisId = null;
  _thesis.checked = new Set();
  _thesis.overrides = {};
  _thesis.months = null;
  _thesis.data = null;
  _thesis.shown = null;
  _thesis.stale = false;
  _thesisSetStatus('載入論點與財報基準中…');
  document.getElementById('thesis-body').classList.add('hidden');
  document.getElementById('thesis-text').classList.add('hidden');
  document.getElementById('thesis-results').classList.add('hidden');
  _thesisFetch(0);
}

function onThesisSelect(id) {
  _thesis.thesisId = id;
  _thesis.checked = new Set();
  _thesis.overrides = {};
  _thesis.months = null;
  _thesis.shown = null;
  document.getElementById('thesis-results').classList.add('hidden');
  _thesisFetch(0);
}

function _thesisConditionsChanged() {
  if (_thesis.shown) _thesis.stale = true;
  _thesisRunHint();
}

function thesisToggle(idx, on) {
  const scenarios = (_thesis.data && _thesis.data.scenarios) || [];
  const s = scenarios[idx];
  if (!s) return;
  if (on) {
    const group = _thesisExclusiveGroup(s);
    if (group) scenarios.forEach(o => { if (o.idx !== idx && _thesisExclusiveGroup(o) === group) _thesis.checked.delete(o.idx); });
    _thesis.checked.add(idx);
  } else {
    _thesis.checked.delete(idx);
  }
  _thesis.overrides = {};   // 改了情境就回到情境算出的預設值，不然滑桿會蓋掉情境效果
  _thesisConditionsChanged();
  _thesisFetch(150);
}

function thesisPreset(kind) {
  const scenarios = (_thesis.data && _thesis.data.scenarios) || [];
  _thesis.checked = new Set();
  const used = new Set();
  if (kind !== 'base') {
    scenarios.forEach(s => {
      if (!s.checkable || (kind === 'support' ? s.stance !== 'support' : s.stance === 'support')) return;
      const group = _thesisExclusiveGroup(s);
      if (group && used.has(group)) return;   // 同一組只取第一個(節目最先提到的數字)
      if (group) used.add(group);
      _thesis.checked.add(s.idx);
    });
  }
  _thesis.overrides = {};
  _thesisConditionsChanged();
  _thesisFetch(0);
}

function thesisResetOverrides() {
  _thesis.overrides = {};
  _thesisConditionsChanged();
  _thesisFetch(0);
}

function thesisLeverInput(key, raw, scale) {
  const v = parseFloat(raw);
  if (Number.isNaN(v)) { delete _thesis.overrides[key]; }
  else { _thesis.overrides[key] = v / scale; }
  const label = document.getElementById('thesis-lever-val-' + key);
  if (label && !Number.isNaN(v)) label.textContent = scale === 100 ? v.toFixed(1) + '%' : v.toFixed(key === 'eps' ? 2 : 1);
  _thesisConditionsChanged();
  _thesisFetch(350);
}

function thesisSetMonths(raw) {
  const v = parseInt(raw, 10);
  _thesis.months = Number.isNaN(v) ? null : Math.min(36, Math.max(1, v));
  _thesisConditionsChanged();
  _thesisFetch(350);
}

// 按下「開始試算」：用目前條件重抓一次，結果區才顯示這份
async function thesisRun() {
  const btn = document.getElementById('thesis-run');
  btn.disabled = true;
  document.getElementById('thesis-run-hint').textContent = '試算中…';
  const data = await _thesisFetch(0, true);
  btn.disabled = false;
  if (!data) { _thesisRunHint(); return; }
  _thesis.shown = data;
  _thesis.stale = false;
  _thesisRenderResults(data);
  _thesisRunHint();
  document.getElementById('thesis-results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function _thesisRunHint() {
  const el = document.getElementById('thesis-run-hint');
  const results = document.getElementById('thesis-results');
  if (!_thesis.shown) {
    el.textContent = '勾選好情境後按「開始試算」；都不勾也可以，代表公司延續目前的成長。';
    results.classList.add('hidden');
    return;
  }
  el.textContent = _thesis.stale ? '條件已經改變，按「開始試算」更新結果' : '';
  el.classList.toggle('text-[#a16207]', _thesis.stale);
  el.classList.toggle('font-bold', _thesis.stale);
  results.classList.toggle('opacity-40', _thesis.stale);
}

// immediate=true 時回傳 Promise<data>(給「開始試算」等結果)；否則 debounce，只更新情境清單與參數預設值
function _thesisFetch(delay, immediate) {
  clearTimeout(_thesis.timer);
  const run = async () => {
    if (!_thesis.scoreId) return null;
    const mySeq = ++_thesis.seq;
    const params = new URLSearchParams({ score_id: _thesis.scoreId });
    if (_thesis.thesisId) params.set('thesis_id', _thesis.thesisId);
    if (_thesis.checked.size) params.set('checked', [..._thesis.checked].join(','));
    if (_thesis.months) params.set('months', _thesis.months);
    Object.entries(_thesis.overrides).forEach(([k, v]) => params.set(k, v));
    try {
      const resp = await fetch(THESIS_API_URL + '?' + params.toString());
      const data = await resp.json();
      if (mySeq !== _thesis.seq) return null;
      if (!resp.ok) { _thesisSetStatus('載入失敗：' + (data.error || resp.statusText)); return null; }
      _thesis.data = data;
      if (!_thesis.thesisId && data.thesis_id) _thesis.thesisId = data.thesis_id;
      _thesisRender(data);
      return data;
    } catch (e) {
      if (mySeq !== _thesis.seq) return null;
      _thesisSetStatus('載入失敗：' + e);
      return null;
    }
  };
  if (immediate) return run();
  _thesis.timer = setTimeout(run, delay);
  return null;
}

function _thesisSetStatus(text) {
  const el = document.getElementById('thesis-status');
  el.textContent = text;
  el.classList.toggle('hidden', !text);
}

// ── 格式 ──
function _tPct(x, signed) {
  if (x == null) return '—';
  const v = (x * 100).toFixed(1) + '%';
  return signed && x >= 0 ? '+' + v : v;
}
function _tNum(x, d) {
  if (x == null) return '—';
  return Number(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function _tMoney(x, ticker) {
  if (x == null) return '—';
  return /\.TWO?$/.test(ticker) ? _tNum(x / 1e8, 0) + ' 億' : _tNum(x / 1e9, 2) + 'B';
}
// 台股慣例：漲紅、跌綠(跟選集資訊的看多紅/看空綠一致)
function _tReturnColor(x) {
  if (x == null) return 'text-outline';
  return x >= 0 ? 'text-[#ba1a1a]' : 'text-[#1e8e3e]';
}

// ── 畫面：論點、情境、試算條件(每次條件改變都更新) ──
function _thesisRender(d) {
  document.getElementById('thesis-asof').textContent = d.as_of || '—';

  const sel = document.getElementById('thesis-select');
  if (d.theses && d.theses.length > 1) {
    sel.innerHTML = d.theses.map((t, i) =>
      `<option value="${t.id}" ${t.id === d.thesis_id ? 'selected' : ''}>論點 ${i + 1}：${_escHtml(t.thesis.slice(0, 36))}${t.thesis.length > 36 ? '…' : ''}</option>`).join('');
    sel.classList.remove('hidden');
  } else {
    sel.classList.add('hidden');
  }

  const textEl = document.getElementById('thesis-text');
  if (!d.theses || !d.theses.length) {
    _thesisSetStatus(d.reason || '這一集對這檔股票沒有可試算的論點。');
    document.getElementById('thesis-body').classList.add('hidden');
    textEl.classList.add('hidden');
    return;
  }
  const current = d.theses.find(t => t.id === d.thesis_id) || d.theses[0];
  const dirChip = current.direction === 'bullish'
    ? '<span class="inline-block mr-2 px-2 py-0.5 rounded-full text-xs font-bold text-[#ba1a1a] bg-[#ba1a1a]/10">看多</span>'
    : current.direction === 'bearish'
      ? '<span class="inline-block mr-2 px-2 py-0.5 rounded-full text-xs font-bold text-[#1e8e3e] bg-[#1e8e3e]/10">看空</span>' : '';
  textEl.innerHTML = dirChip + _escHtml(current.thesis) +
    (current.timeframe ? `<span class="text-xs text-outline ml-2">（時間：${_escHtml(current.timeframe)}）</span>` : '');
  textEl.classList.remove('hidden');

  document.getElementById('thesis-scenarios').innerHTML = _thesisScenariosHtml(d);
  document.getElementById('thesis-info').innerHTML = _thesisInfoHtml(d);
  ['support', 'against'].forEach(kind => {
    const btn = document.getElementById('thesis-preset-' + kind);
    const n = (d.scenarios || []).filter(s => s.checkable && (kind === 'support' ? s.stance === 'support' : s.stance !== 'support')).length;
    btn.disabled = n === 0;
    btn.title = n === 0 ? (kind === 'support' ? '這句論點沒有支持的情境' : '這句論點沒有拆出反方情境') : '';
  });
  document.getElementById('thesis-body').classList.remove('hidden');

  if (!d.available) {
    _thesisSetStatus('這檔股票目前無法試算：' + (d.reason || '資料不足') + '。上方的情境仍可閱讀。');
    document.getElementById('thesis-run').disabled = true;
    document.getElementById('thesis-levers').innerHTML = '';
    document.getElementById('thesis-results').classList.add('hidden');
    return;
  }
  document.getElementById('thesis-run').disabled = false;
  _thesisSetStatus(d.no_driver_reason ? '這句論點沒有拆出具體情境：' + d.no_driver_reason + '（仍可直接試算或手動調整參數）' : '');

  const monthsInput = document.getElementById('thesis-months');
  if (document.activeElement !== monthsInput) monthsInput.value = d.horizon_months;
  document.getElementById('thesis-months-hint').textContent =
    d.horizon_months === d.horizon_default ? `預設依${d.horizon_source}` : `預設是 ${d.horizon_default} 個月（${d.horizon_source}）`;

  document.getElementById('thesis-levers').innerHTML = _thesisLeversHtml(d);
  document.getElementById('thesis-reset').classList.toggle('hidden', !(d.overridden && d.overridden.length));
  _thesisRunHint();
}

function _thesisScenarioItem(s, checked) {
  const figs = [];
  if (s.figures.eps) figs.push(`EPS ${_tNum(s.figures.eps, 2)}${s.figures.year ? '｜' + s.figures.year : ''}${s.figures_source === 'thesis' ? '｜取自論點摘要' : ''}`);
  if (s.figures.pe) figs.push(`本益比 ${_tNum(s.figures.pe, 1)} 倍`);
  if (s.figures.target_low) figs.push(`${s.source === 'analyst' ? '法人' : '節目觀點'} ${_tNum(s.figures.target_low, 0)}${s.figures.target_high && s.figures.target_high !== s.figures.target_low ? '～' + _tNum(s.figures.target_high, 0) : ''}`);
  const box = s.checkable
    ? `<input type="checkbox" id="thesis-cb-${s.idx}" class="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-[#113236]" ${checked ? 'checked' : ''} onchange="thesisToggle(${s.idx}, this.checked)">`
    : '<span class="mt-1 h-4 w-4 shrink-0"></span>';
  return `<li class="flex gap-3 items-start py-2.5 border-t border-outline-variant/15 first:border-t-0">
    ${box}
    <label for="thesis-cb-${s.idx}" class="min-w-0 space-y-1 ${s.checkable ? 'cursor-pointer' : ''}">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span class="font-bold text-sm text-tertiary-container">${_escHtml(s.label)}</span>
        ${s.effects.length ? `<span class="text-xs text-outline">${_escHtml(s.effects.join('、'))}</span>` : ''}
        ${figs.map(f => `<span class="text-xs font-bold px-2 py-0.5 rounded bg-surface-container border border-outline-variant/40">${_escHtml(f)}</span>`).join('')}
        ${s.provisional ? '<span class="text-xs font-bold text-[#a16207]">幅度暫定</span>' : ''}
      </div>
      <div class="text-xs text-outline leading-relaxed">「${_escHtml(s.quote)}」</div>
    </label>
  </li>`;
}

function _thesisScenariosHtml(d) {
  const all = d.scenarios || [];
  const checked = new Set([..._thesis.checked]);
  const card = (title, note, list, empty) => `
    <div class="bg-surface-container-lowest rounded-xl px-5 py-3 min-w-0">
      <div class="flex items-baseline justify-between gap-2 pb-1">
        <h5 class="text-sm font-bold text-tertiary-container">${title}</h5>
        <span class="text-xs text-outline">${note}</span>
      </div>
      ${list.length ? `<ul>${list.map(s => _thesisScenarioItem(s, checked.has(s.idx))).join('')}</ul>`
                    : `<p class="text-sm text-outline py-2">${empty}</p>`}
    </div>`;
  const support = all.filter(s => s.checkable && s.stance === 'support');
  const against = all.filter(s => s.checkable && s.stance !== 'support');
  return card('支持節目論點', `${support.length} 個`, support, '這句論點沒有拆出支持的情境')
    + card('反對節目論點', `${against.length} 個（多半是節目提到的風險）`, against, '這句論點沒有拆出反方情境');
}

// 只供參考的情境(技術面、目標價位、沒有數字的 EPS 上修)收在折疊區
function _thesisInfoHtml(d) {
  const info = (d.scenarios || []).filter(s => !s.checkable);
  if (!info.length) return '';
  return `<details class="bg-surface-container-lowest rounded-xl px-5 py-3">
    <summary class="text-sm font-bold text-tertiary-container cursor-pointer select-none">節目還提到（只供參考，不參與試算）<span class="font-normal text-outline ml-2">${info.length} 個：技術面、目標價位、沒有數字的 EPS 上修</span></summary>
    <ul class="mt-2">${info.map(s => _thesisScenarioItem(s, false)).join('')}</ul>
  </details>`;
}

// 依計算路線決定要給哪些滑桿：完整(營收、毛利率、費用率)、缺毛利(營業利益率)、只有淨利(淨利率)、虧損(P/S)
function _thesisLeversHtml(d) {
  const L = d.levers || {};
  const A = d.levers_auto || {};
  const route = d.baseline.route;
  const defs = [{ key: 'revenue_growth', label: '營收年增率', min: -50, max: 150, step: 0.5, scale: 100 }];
  if (route === 'full') {
    defs.push({ key: 'gross_margin', label: '毛利率', min: 0, max: 100, step: 0.1, scale: 100 });
    defs.push({ key: 'opex_ratio', label: '營業費用率', min: 0, max: 80, step: 0.1, scale: 100 });
  } else if (route === 'no_gross') {
    defs.push({ key: 'operating_margin', label: '營業利益率', min: -20, max: 80, step: 0.1, scale: 100 });
  } else if (route === 'net') {
    defs.push({ key: 'net_margin', label: '淨利率', min: -20, max: 80, step: 0.1, scale: 100 });
  }
  if (route === 'ps') {
    defs.push({ key: 'ps', label: '股價營收比（P/S）', min: 0.1, max: Math.max(50, Math.ceil((L.ps || 1) * 2)), step: 0.1, scale: 1 });
  } else {
    defs.push({ key: 'pe', label: '本益比', min: 3, max: Math.max(80, Math.ceil((L.pe || 1) * 1.5)), step: 0.1, scale: 1 });
  }
  const overridden = new Set(d.overridden || []);
  const fmt = (v, scale) => scale === 100 ? (v * 100).toFixed(1) + '%' : v.toFixed(1) + (scale === 1 ? ' 倍' : '');
  const sliders = defs.map(def => {
    const v = L[def.key];
    if (v == null) return '';
    const auto = A[def.key];
    return `<div class="space-y-1 min-w-0">
      <div class="flex justify-between items-baseline text-sm gap-2">
        <label for="thesis-lever-${def.key}" class="font-bold text-outline">${def.label}${overridden.has(def.key) ? ' <span class="text-xs text-[#a16207]">已手動調整</span>' : ''}</label>
        <span id="thesis-lever-val-${def.key}" class="font-['Epilogue'] font-bold text-tertiary-container">${fmt(v, def.scale)}</span>
      </div>
      <input id="thesis-lever-${def.key}" type="range" min="${def.min}" max="${def.max}" step="${def.step}" value="${v * def.scale}"
        oninput="thesisLeverInput('${def.key}', this.value, ${def.scale})" class="w-full h-1.5 cursor-pointer">
      <div class="text-xs text-outline">依情境：${auto == null ? '—' : fmt(auto, def.scale)}</div>
    </div>`;
  }).join('');
  const epsBox = route === 'ps' ? '' : `<div class="space-y-1 min-w-0">
      <label for="thesis-lever-eps" class="text-sm font-bold text-outline">直接指定 EPS</label>
      <input id="thesis-lever-eps" type="number" step="0.01" min="0" placeholder="留空則由營收與利潤率推算"
        value="${L.eps_override != null ? L.eps_override : ''}" oninput="thesisLeverInput('eps', this.value, 1)"
        class="w-full px-3 py-2 bg-surface-container border-none rounded-DEFAULT text-sm focus:ring-2 focus:ring-secondary">
      <div class="text-xs text-outline">填了就跳過營收與利潤率，直接用 EPS × 本益比</div>
    </div>`;
  return sliders + epsBox;
}

// ── 畫面：結果(只在按下「開始試算」時更新) ──
function _thesisRenderResults(d) {
  document.getElementById('thesis-result').innerHTML = _thesisResultHtml(d);
  document.getElementById('thesis-waterfall').innerHTML = _thesisWaterfallHtml(d);
  document.getElementById('thesis-baseline').innerHTML = _thesisBaselineHtml(d);
  document.getElementById('thesis-results').classList.remove('hidden');
}

function _thesisResultHtml(d) {
  const p = d.projection;
  const b = d.baseline;
  const L = d.levers || {};
  const m = p.months;
  const multipleLabel = p.route === 'ps' ? '股價營收比' : '本益比';

  // 計算過程：每個數字怎麼來的
  const steps = [];
  steps.push(['預估營收', `近四季 ${_tMoney(b.revenue, d.ticker)} ×（1 + 年增率 ${_tPct(L.revenue_growth)}）<sup>${m}/12</sup>`, _tMoney(p.revenue, d.ticker)]);
  if (p.route !== 'ps') {
    if (L.eps_override) {
      steps.push(['預估 EPS', '直接使用指定的 EPS', _tNum(p.eps, 2)]);
    } else {
      const profitName = p.route === 'net' ? '淨利' : '營業利益';
      steps.push(['預估 EPS', `近四季 ${_tNum(b.eps, 2)} × ${profitName}倍數 ${_tNum(p.profit_factor, 3)}`
        + ((L.eps_multiplier || 1) !== 1 ? ` × 財務風險 ${_tNum(L.eps_multiplier, 2)}` : ''), _tNum(p.eps, 2)]);
    }
    steps.push([multipleLabel, p.multiple === b.pe ? `發布日股價 ${_tNum(b.price, 2)} ÷ 近四季 EPS ${_tNum(b.eps, 2)}` : `現值 ${_tNum(b.pe, 1)} 倍，依情境或手動調整`, `${_tNum(p.multiple, 1)} 倍`]);
    steps.push(['目標價', `預估 EPS ${_tNum(p.eps, 2)} × ${multipleLabel} ${_tNum(p.multiple, 1)}`, _tNum(p.target_price, 2)]);
  } else {
    steps.push([multipleLabel, `現值 ${_tNum(b.ps, 1)} 倍`, `${_tNum(p.multiple, 1)} 倍`]);
    steps.push(['目標價', `預估營收 × ${multipleLabel} ÷ 股數`, _tNum(p.target_price, 2)]);
  }
  const stepRows = steps.map(([k, how, v]) => `
    <div class="grid grid-cols-[5.5rem_minmax(0,1fr)_auto] gap-3 items-baseline py-1.5 border-t border-outline-variant/15 first:border-t-0">
      <span class="text-sm font-bold text-outline">${k}</span>
      <span class="text-xs text-outline leading-relaxed">${how}</span>
      <span class="text-sm font-bold text-tertiary-container text-right whitespace-nowrap">${v}</span>
    </div>`).join('');

  const marginChanged = ['gross_margin', 'opex_ratio', 'operating_margin', 'net_margin']
    .some(k => L[k] != null && b[k] != null && Math.abs(L[k] - b[k]) > 1e-9);
  const marginNote = p.margin_share < 1 && p.route !== 'ps' && marginChanged
    ? `<li>· 試算期間 ${m} 個月，利潤率的變化只反映 ${Math.round(p.margin_share * 100)}%（到期時的近四季只有部分是新的利潤率）</li>` : '';
  const growthNote = b.growth_note ? `<li class="text-[#a16207] font-bold">· ${_escHtml(b.growth_note)}</li>` : '';
  const targets = (d.analyst_targets || []).map(t =>
    `${_tNum(t.low, 0)}${t.high && t.high !== t.low ? '～' + _tNum(t.high, 0) : ''}（${t.source === 'analyst' ? '法人' : '節目觀點'}）`);
  const notes = (d.notes || []).map(n => `<li>· ${_escHtml(n)}</li>`).join('');

  return `
    <div class="flex items-end justify-between gap-3 flex-wrap">
      <div>
        <div class="text-xs text-outline font-bold uppercase tracking-widest">${m} 個月後的目標價（每股）</div>
        <div class="font-['Epilogue'] text-3xl font-bold text-tertiary-container">${_tNum(p.target_price, 2)}</div>
      </div>
      <div class="text-right">
        <div class="text-xs text-outline font-bold uppercase tracking-widest">相對發布日股價 ${_tNum(b.price, 2)}</div>
        <div class="font-['Epilogue'] text-2xl font-bold ${_tReturnColor(p.implied_return)}">${_tPct(p.implied_return, true)}</div>
      </div>
    </div>
    <div>
      <div class="text-xs text-outline font-bold uppercase tracking-widest mb-1">計算過程</div>
      ${stepRows}
    </div>
    ${(marginNote || growthNote || notes) ? `<ul class="text-xs text-outline space-y-0.5 pt-2 border-t border-outline-variant/15">${growthNote}${marginNote}${notes}</ul>` : ''}
    ${targets.length ? `<div class="text-xs text-outline pt-2 border-t border-outline-variant/15">節目提到的價位（僅供對照）：${targets.join('、')}</div>` : ''}`;
}

function _thesisBaselineHtml(d) {
  const b = d.baseline;
  const h = b.history || {};
  const parts = [`基準：近四季（至 ${_escHtml(b.ttm_end || '—')}）EPS ${_tNum(b.eps, 2)}`];
  if (b.pe != null) parts.push(`本益比 ${_tNum(b.pe, 1)} 倍${h.pe ? `（過去 5 年中位數 ${_tNum(h.pe.p50, 1)} 倍）` : ''}`);
  if (b.gross_margin != null) parts.push(`毛利率 ${_tPct(b.gross_margin)}`);
  if (b.operating_margin != null) parts.push(`營業利益率 ${_tPct(b.operating_margin)}`);
  if (b.revenue_growth != null) parts.push(`目前營收年增率 ${_tPct(b.revenue_growth)}`);
  let text = parts.join('，') + '。';
  if (b.reason) text += ` ${_escHtml(b.reason)}。`;
  text += ' 都不勾時，營收照目前的年增率成長、其他數字維持現值，所以報酬大約等於這段期間的營收成長；'
    + '勾一個情境，就從現值加減這家公司過去 5 年這個數字一般的波動幅度。這是以歷史表現為基礎的參考幅度，'
    + '不是這個事件一定會造成的效果；本益比預設不變，這段期間市場若重新評價，目標價會有很大落差。標示「幅度暫定」的情境還沒有校準。';
  return text;
}

// 瀑布圖：發布日股價 → 延續目前成長 → 每個勾選的情境 → 手動調整 → 目標價。
// 橫條都從 0 起算(不截斷座標軸，避免小變動看起來很大)；漲紅跌綠沿用台股慣例。
function _thesisWaterfallHtml(d) {
  const steps = d.waterfall;
  const price = d.baseline.price;
  if (!steps || !steps.length || !price) return '<p class="text-sm text-outline">這組條件算不出目標價，無法拆解。</p>';
  const values = [price, ...steps.map(s => s.value)];
  const hi = Math.max(...values) * 1.05;
  const pos = v => Math.max(0, Math.min(100, v / hi * 100));

  // 情境貢獻是 0 時說明原因：EPS 已直接指定，或同方向已經調整過
  const scenarios = d.scenarios || [];
  const order = (d.checked || []).map(i => scenarios[i]);
  let epsFixed = false;
  const zeroReasons = [];
  order.forEach(s => {
    zeroReasons.push(epsFixed && s && s.type !== 'RERATING_UP' && s.type !== 'RERATING_DOWN' && !s.type.startsWith('MACRO')
      ? 'EPS 已直接指定，營收與利潤率的變化不再影響結果'
      : '同一個參數同一個方向已經調整過，不重複計算');
    if (s && s.type === 'EPS_FORECAST' && s.figures.eps) epsFixed = true;
  });

  // 寬螢幕：名稱｜長條｜數字 三欄；手機：名稱與數字一行、長條另起一行佔滿寬度
  const row = (label, sub, left, width, color, valueText, deltaText, deltaColor) => `
    <div class="grid grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-1">
      <div class="min-w-0 order-1">
        <div class="text-sm font-bold text-tertiary-container truncate" title="${_escHtml(label)}">${_escHtml(label)}</div>
        ${sub ? `<div class="text-[11px] text-outline leading-tight">${_escHtml(sub)}</div>` : ''}
      </div>
      <div class="relative h-5 rounded bg-surface-container order-3 col-span-2 sm:order-2 sm:col-span-1">
        <div class="absolute inset-y-0 rounded ${color}" style="left:${left}%;width:${Math.max(width, 0.6)}%"></div>
      </div>
      <div class="text-right text-sm font-['Epilogue'] font-bold tabular-nums whitespace-nowrap order-2 sm:order-3">
        ${deltaText ? `<span class="${deltaColor}">${deltaText}</span>` : valueText}
      </div>
    </div>`;

  const fmt = v => _tNum(v, 0);
  let scenarioIdx = 0;
  const rows = [row('發布日股價', d.as_of, 0, pos(price), 'bg-[#113236]/40', fmt(price))];
  let prev = price;
  steps.forEach(s => {
    const up = s.delta >= 0;
    const zero = Math.abs(s.delta) < 0.005 * price;
    let sub = s.kind === 'trend' ? '營收照目前年增率成長，其他維持現值' : s.kind === 'manual' ? '滑桿或直接指定 EPS' : '';
    if (s.kind === 'scenario') {
      if (zero) sub = zeroReasons[scenarioIdx] || '';
      scenarioIdx += 1;
    }
    const left = pos(Math.min(prev, s.value));
    const width = pos(Math.max(prev, s.value)) - left;
    const color = zero ? 'bg-outline/40' : up ? 'bg-[#ba1a1a]/70' : 'bg-[#1e8e3e]/70';
    const delta = zero ? '0' : (up ? '+' : '−') + fmt(Math.abs(s.delta));
    rows.push(row(s.label, sub, left, width, color, '', delta, zero ? 'text-outline' : up ? 'text-[#ba1a1a]' : 'text-[#1e8e3e]'));
    prev = s.value;
  });
  rows.push(row('目標價', `${d.projection.months} 個月後`, 0, pos(prev), 'bg-[#113236]', fmt(prev)));

  return `
    <div class="flex items-baseline justify-between gap-2">
      <h5 class="text-xs text-outline font-bold uppercase tracking-widest">目標價怎麼來的</h5>
      <span class="text-xs text-outline">每一步相對前一步的變化</span>
    </div>
    <div class="space-y-1">${rows.join('')}</div>
    <p class="text-xs text-outline">依情境在清單中的順序逐一加入計算，各步加總等於目標價與發布日股價的差距；順序不同時每一步的金額會不同，但最後的目標價一樣。</p>`;
}

// calculator.js 載入時已經呼叫過 setMode('manual')，那時這個檔案還沒載入，這裡補套用一次初始模式
onCalcModeChanged(typeof mode !== 'undefined' ? mode : 'manual');
