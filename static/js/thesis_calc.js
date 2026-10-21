// ── 論點情境試算(計算機「從節目挑選」模式) ─────────────────────────────────
// 選定一集一檔股票後：列出節目論點拆出的情境 → 使用者勾選、調整參數 → 後端推算
// 12 個月後的 EPS 與目標價，並標出落在歷史股價模擬(12 個月拔靴)的第幾百分位。
// 計算都在後端(apps/calculator/thesis_views.py)，這裡只負責勾選狀態、參數覆寫與畫面。
// 依賴 calculator.js 的 _escHtml()；calculator.js 在 onEpisodeChange()/setMode() 會呼叫這裡。

const THESIS_API_URL = '/api/calculator/thesis-valuation/';

const _thesis = {
  scoreId: null,
  thesisId: null,
  checked: new Set(),
  overrides: {},       // 使用者手動拉過的參數，key 同後端 levers
  data: null,
  seq: 0,              // 請求序號，過期的回應不要蓋掉新的結果
  timer: null,
};

// 帶數字的情境同一時間只能選一個：EPS 只能直接指定一個值、本益比也只能一個
function _thesisExclusiveGroup(s) {
  if (s.type === 'EPS_FORECAST' && s.figures.eps) return 'eps';
  if ((s.type === 'RERATING_UP' || s.type === 'RERATING_DOWN') && s.figures.pe) return 'pe';
  return null;
}

function loadThesisValuation(scoreId) {
  _thesis.scoreId = scoreId;
  _thesis.thesisId = null;
  _thesis.checked = new Set();
  _thesis.overrides = {};
  _thesis.data = null;
  const panel = document.getElementById('thesis-panel');
  if (panel && typeof mode !== 'undefined') panel.classList.toggle('hidden', mode !== 'episode');
  _thesisSetStatus('載入論點與財報基準中…（第一次要抓股價、跑模擬，約需幾秒）');
  document.getElementById('thesis-body').classList.add('hidden');
  _thesisFetch(0);
}

function onThesisSelect(id) {
  _thesis.thesisId = id;
  _thesis.checked = new Set();
  _thesis.overrides = {};
  _thesisFetch(0);
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
  _thesisFetch(150);
}

function thesisPreset(kind) {
  const scenarios = (_thesis.data && _thesis.data.scenarios) || [];
  _thesis.checked = new Set();
  const used = new Set();
  if (kind !== 'base') {
    scenarios.forEach(s => {
      if (!s.checkable || s.stance !== kind) return;
      const group = _thesisExclusiveGroup(s);
      if (group && used.has(group)) return;   // 同一組只取第一個(節目最先提到的數字)
      if (group) used.add(group);
      _thesis.checked.add(s.idx);
    });
  }
  _thesis.overrides = {};
  _thesisFetch(0);
}

function thesisResetOverrides() {
  _thesis.overrides = {};
  _thesisFetch(0);
}

function thesisLeverInput(key, raw, scale) {
  const v = parseFloat(raw);
  if (Number.isNaN(v)) { delete _thesis.overrides[key]; }
  else { _thesis.overrides[key] = v / scale; }
  const label = document.getElementById('thesis-lever-val-' + key);
  if (label && !Number.isNaN(v)) label.textContent = scale === 100 ? v.toFixed(1) + '%' : v.toFixed(key === 'eps' ? 2 : 1);
  _thesisFetch(350);
}

function _thesisFetch(delay) {
  clearTimeout(_thesis.timer);
  _thesis.timer = setTimeout(async () => {
    if (!_thesis.scoreId) return;
    const mySeq = ++_thesis.seq;
    const params = new URLSearchParams({ score_id: _thesis.scoreId });
    if (_thesis.thesisId) params.set('thesis_id', _thesis.thesisId);
    if (_thesis.checked.size) params.set('checked', [..._thesis.checked].join(','));
    Object.entries(_thesis.overrides).forEach(([k, v]) => params.set(k, v));
    try {
      const resp = await fetch(THESIS_API_URL + '?' + params.toString());
      const data = await resp.json();
      if (mySeq !== _thesis.seq) return;
      if (!resp.ok) { _thesisSetStatus('載入失敗：' + (data.error || resp.statusText)); return; }
      _thesis.data = data;
      if (!_thesis.thesisId && data.thesis_id) _thesis.thesisId = data.thesis_id;
      _thesisRender(data);
    } catch (e) {
      if (mySeq !== _thesis.seq) return;
      _thesisSetStatus('載入失敗：' + e);
    }
  }, delay);
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

// ── 畫面 ──
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

  if (!d.theses || !d.theses.length) {
    _thesisSetStatus(d.reason || '這一集對這檔股票沒有可試算的論點。');
    document.getElementById('thesis-body').classList.add('hidden');
    return;
  }

  const current = d.theses.find(t => t.id === d.thesis_id) || d.theses[0];
  const dirChip = current.direction === 'bullish'
    ? '<span class="inline-block mr-2 px-2 py-0.5 rounded-full text-xs font-bold text-[#ba1a1a] bg-[#ba1a1a]/10">看多</span>'
    : current.direction === 'bearish'
      ? '<span class="inline-block mr-2 px-2 py-0.5 rounded-full text-xs font-bold text-[#1e8e3e] bg-[#1e8e3e]/10">看空</span>' : '';
  document.getElementById('thesis-text').innerHTML = dirChip + _escHtml(current.thesis) +
    (current.timeframe ? `<span class="text-xs text-outline ml-2">（時間：${_escHtml(current.timeframe)}）</span>` : '');

  document.getElementById('thesis-scenarios').innerHTML = _thesisScenariosHtml(d);
  // 這句論點沒有支持或反對的情境時，對應的快速按鈕停用(不然按了看起來沒反應)
  ['support', 'against'].forEach(kind => {
    const btn = document.getElementById('thesis-preset-' + kind);
    if (!btn) return;
    const n = (d.scenarios || []).filter(s => s.checkable && (kind === 'support' ? s.stance === 'support' : s.stance !== 'support')).length;
    btn.disabled = n === 0;
    btn.title = n === 0 ? (kind === 'support' ? '這句論點沒有支持的情境' : '這句論點沒有拆出反方情境') : '';
  });
  document.getElementById('thesis-body').classList.remove('hidden');

  if (!d.available) {
    _thesisSetStatus('這檔股票目前無法試算：' + (d.reason || '資料不足'));
    document.getElementById('thesis-result').innerHTML =
      `<p class="text-sm text-outline">${_escHtml(d.reason || '資料不足')}。上方的情境仍可閱讀，股價走勢請參考上面的歷史股價模擬。</p>`;
    document.getElementById('thesis-levers').innerHTML = '';
    document.getElementById('thesis-waterfall').classList.add('hidden');
    document.getElementById('thesis-reset').classList.add('hidden');
    document.getElementById('thesis-baseline').textContent = '';
    return;
  }
  _thesisSetStatus(d.no_driver_reason ? '這句論點沒有拆出具體情境：' + d.no_driver_reason + '（仍可直接調整下方參數）' : '');
  document.getElementById('thesis-result').innerHTML = _thesisResultHtml(d);
  const wf = document.getElementById('thesis-waterfall');
  wf.innerHTML = _thesisWaterfallHtml(d);
  wf.classList.toggle('hidden', !wf.innerHTML);
  document.getElementById('thesis-levers').innerHTML = _thesisLeversHtml(d);
  document.getElementById('thesis-reset').classList.toggle('hidden', !(d.overridden && d.overridden.length));
  document.getElementById('thesis-baseline').innerHTML = _thesisBaselineHtml(d);
}

function _thesisScenariosHtml(d) {
  const all = d.scenarios || [];
  if (!all.length) return '<p class="text-sm text-outline">這句論點沒有拆出情境。</p>';
  const checked = new Set(d.checked || []);
  const item = s => {
    const figs = [];
    if (s.figures.eps) figs.push(`EPS ${_tNum(s.figures.eps, 2)}${s.figures.year ? '｜' + s.figures.year : ''}`);
    if (s.figures.pe) figs.push(`本益比 ${_tNum(s.figures.pe, 1)} 倍`);
    if (s.figures.target_low) figs.push(`目標價 ${_tNum(s.figures.target_low, 0)}${s.figures.target_high && s.figures.target_high !== s.figures.target_low ? '～' + _tNum(s.figures.target_high, 0) : ''}`);
    const box = s.checkable
      ? `<input type="checkbox" id="thesis-cb-${s.idx}" class="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-[#113236]" ${checked.has(s.idx) ? 'checked' : ''} onchange="thesisToggle(${s.idx}, this.checked)">`
      : '<span class="mt-1 h-4 w-4 shrink-0"></span>';
    return `<li class="flex gap-3 items-start py-2 border-t border-outline-variant/15 first:border-t-0">
      ${box}
      <label for="thesis-cb-${s.idx}" class="min-w-0 space-y-1 ${s.checkable ? 'cursor-pointer' : ''}">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span class="font-bold text-sm text-tertiary-container">${_escHtml(s.label)}</span>
          ${figs.map(f => `<span class="text-xs font-bold px-2 py-0.5 rounded bg-surface-container-lowest border border-outline-variant/40">${_escHtml(f)}</span>`).join('')}
          ${s.effects.length ? `<span class="text-xs text-outline">${_escHtml(s.effects.join('、'))}</span>` : ''}
          ${s.provisional ? '<span class="text-xs font-bold text-[#a16207]">幅度暫定</span>' : ''}
        </div>
        ${s.chain.length ? `<div class="text-[13px] text-on-surface-variant">${s.chain.map(_escHtml).join(' → ')}</div>` : ''}
        <div class="text-xs text-outline">原文：「${_escHtml(s.quote)}」</div>
      </label>
    </li>`;
  };
  const group = (title, note, list) => list.length ? `
    <div class="bg-surface-container-lowest rounded-xl px-5 py-3">
      <div class="flex items-baseline justify-between gap-2 pb-1">
        <h4 class="text-sm font-bold text-tertiary-container">${title}</h4>
        <span class="text-xs text-outline">${note}</span>
      </div>
      <ul>${list.map(item).join('')}</ul>
    </div>` : '';
  const support = all.filter(s => s.checkable && s.stance === 'support');
  const against = all.filter(s => s.checkable && s.stance !== 'support');
  const info = all.filter(s => !s.checkable);
  return group('支持節目論點的情境', `${support.length} 個`, support)
    + group('反對節目論點的情境', `${against.length} 個`, against)
    + group('只供參考（不參與試算）', '技術面、法人目標價、沒有數字的 EPS 上修', info);
}

function _thesisResultHtml(d) {
  const p = d.projection;
  const b = d.baseline;
  const multipleLabel = p.route === 'ps' ? '股價營收比' : '本益比';
  const pct = d.bootstrap_percentile;
  let pctText = '';
  if (pct != null) {
    const band = pct < 25 ? '比多數歷史走勢保守' : pct <= 75 ? '落在歷史上常見的範圍' : pct <= 90 ? '需要比多數歷史走勢更好' : '高於九成的歷史走勢';
    pctText = `
      <div class="space-y-1 pt-2 border-t border-outline-variant/15">
        <div class="flex justify-between text-xs text-outline"><span>這個報酬在歷史股價模擬（12 個月）中的位置</span><span class="font-bold text-tertiary-container">第 ${Math.round(pct)} 百分位</span></div>
        <div class="relative h-2 rounded-full bg-surface-container">
          <div class="absolute inset-y-0 rounded-full bg-[#113236]/15" style="left:25%;right:25%"></div>
          <div class="absolute -top-1 h-4 w-1 rounded bg-[#113236]" style="left:calc(${Math.min(99, Math.max(0, pct))}% - 2px)"></div>
        </div>
        <div class="text-xs text-outline">${band}（深色區塊是歷史上一半走勢落在的範圍）</div>
      </div>`;
  }
  const targets = (d.analyst_targets || []).map(t =>
    `${_tNum(t.low, 0)}${t.high && t.high !== t.low ? '～' + _tNum(t.high, 0) : ''}`);
  return `
    <div class="flex items-end justify-between gap-3 flex-wrap">
      <div>
        <div class="text-xs text-outline font-bold uppercase tracking-widest">12 個月後目標價</div>
        <div class="font-['Epilogue'] text-3xl font-bold text-tertiary-container">${_tNum(p.target_price, 2)}</div>
      </div>
      <div class="text-right">
        <div class="text-xs text-outline font-bold uppercase tracking-widest">相對發布日股價 ${_tNum(b.price, 2)}</div>
        <div class="font-['Epilogue'] text-2xl font-bold ${_tReturnColor(p.implied_return)}">${_tPct(p.implied_return, true)}</div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      ${p.eps != null ? `<span class="text-outline">預估 EPS</span><span class="text-right font-bold">${_tNum(p.eps, 2)}<span class="text-xs text-outline font-normal">（近四季 ${_tNum(b.eps, 2)}）</span></span>` : ''}
      <span class="text-outline">${multipleLabel}</span><span class="text-right font-bold">${_tNum(p.multiple, 1)} 倍</span>
      <span class="text-outline">預估營收</span><span class="text-right font-bold">${_tMoney(p.revenue, d.ticker)}<span class="text-xs text-outline font-normal">（近四季 ${_tMoney(b.revenue, d.ticker)}）</span></span>
    </div>
    ${pctText}
    ${targets.length ? `<div class="text-xs text-outline pt-2 border-t border-outline-variant/15">節目提到的法人目標價（僅供對照）：${targets.join('、')}</div>` : ''}
    ${(d.notes || []).length ? `<ul class="text-xs text-outline space-y-0.5 pt-2 border-t border-outline-variant/15">${d.notes.map(n => `<li>· ${_escHtml(n)}</li>`).join('')}</ul>` : ''}`;
}

// 依計算路線決定要給哪些滑桿：完整(營收、毛利率、費用率)、缺毛利(營業利益率)、只有淨利(淨利率)、虧損(P/S)
function _thesisLeversHtml(d) {
  const L = d.levers || {};
  const A = d.levers_auto || {};
  const route = d.baseline.route;
  const defs = [{ key: 'revenue_growth', label: '營收成長（12 個月）', min: -50, max: 150, step: 0.5, scale: 100 }];
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
  const sliders = defs.map(def => {
    const v = L[def.key];
    if (v == null) return '';
    const shown = v * def.scale;
    const auto = A[def.key];
    const autoText = auto == null ? '' : (def.scale === 100 ? (auto * 100).toFixed(1) + '%' : auto.toFixed(1));
    return `<div class="space-y-1">
      <div class="flex justify-between items-baseline text-sm">
        <label for="thesis-lever-${def.key}" class="font-bold text-outline">${def.label}${overridden.has(def.key) ? ' <span class="text-xs text-[#a16207]">已手動調整</span>' : ''}</label>
        <span id="thesis-lever-val-${def.key}" class="font-['Epilogue'] font-bold text-tertiary-container">${def.scale === 100 ? shown.toFixed(1) + '%' : shown.toFixed(1)}</span>
      </div>
      <input id="thesis-lever-${def.key}" type="range" min="${def.min}" max="${def.max}" step="${def.step}" value="${shown}"
        oninput="thesisLeverInput('${def.key}', this.value, ${def.scale})" class="w-full h-1.5 cursor-pointer">
      <div class="text-xs text-outline">情境預設：${autoText}</div>
    </div>`;
  }).join('');
  const epsBox = route === 'ps' ? '' : `<div class="space-y-1 pt-2 border-t border-outline-variant/15">
      <label for="thesis-lever-eps" class="text-sm font-bold text-outline">直接指定 EPS（選填）</label>
      <input id="thesis-lever-eps" type="number" step="0.01" min="0" placeholder="留空則由營收與利潤率推算"
        value="${L.eps_override != null ? L.eps_override : ''}"
        oninput="thesisLeverInput('eps', this.value, 1)"
        class="w-full px-3 py-2 bg-surface-container border-none rounded-DEFAULT text-sm focus:ring-2 focus:ring-secondary">
      <div class="text-xs text-outline">填了就跳過營收與利潤率，直接用 EPS × ${route === 'ps' ? '股價營收比' : '本益比'}。</div>
    </div>`;
  return sliders + epsBox;
}

function _thesisBaselineHtml(d) {
  const b = d.baseline;
  const h = b.history || {};
  const parts = [`基準：近四季（至 ${_escHtml(b.ttm_end || '—')}）EPS ${_tNum(b.eps, 2)}`];
  if (b.pe != null) parts.push(`本益比 ${_tNum(b.pe, 1)} 倍${h.pe ? `（過去 5 年中位數 ${_tNum(h.pe.p50, 1)} 倍）` : ''}`);
  if (b.gross_margin != null) parts.push(`毛利率 ${_tPct(b.gross_margin)}`);
  if (b.operating_margin != null) parts.push(`營業利益率 ${_tPct(b.operating_margin)}`);
  if (h.revenue_growth) parts.push(`過去 5 年營收年增率中位數 ${_tPct(h.revenue_growth.p50)}`);
  let text = parts.join('，') + '。';
  if (b.reason) text += ` ${_escHtml(b.reason)}。`;
  text += ' 情境的預設幅度取自這家公司自己過去 5 年的分布；標示「幅度暫定」的情境還沒有校準。';
  return text;
}

// 瀑布圖：發布日股價 → 公司一般趨勢 → 每個勾選的情境 → 手動調整 → 目標價。
// 橫條都從 0 起算(不截斷座標軸，避免小變動看起來很大)；漲紅跌綠沿用台股慣例。
function _thesisWaterfallHtml(d) {
  const steps = d.waterfall;
  const price = d.baseline.price;
  if (!steps || !steps.length || !price) return '';
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

  // 寬螢幕：名稱｜長條｜數字 三欄；手機：名稱與數字一行、長條另起一行佔滿寬度(不然長條會被擠到看不出比例)
  const row = (label, sub, left, width, color, valueText, deltaText, deltaColor) => `
    <div class="grid grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-1">
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
    let sub = s.kind === 'trend' ? '都不勾時：歷史中位數的營收成長' : s.kind === 'manual' ? '滑桿或直接指定 EPS' : '';
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
  rows.push(row('目標價', '12 個月後', 0, pos(prev), 'bg-[#113236]', fmt(prev)));

  return `
    <div class="flex items-baseline justify-between gap-2">
      <h4 class="text-sm font-bold text-tertiary-container uppercase tracking-widest">目標價怎麼來的</h4>
      <span class="text-xs text-outline">每一步相對前一步的變化</span>
    </div>
    <div class="space-y-1">${rows.join('')}</div>
    <p class="text-xs text-outline">依勾選順序逐一加入情境計算，各步加總等於目標價與發布日股價的差距。</p>`;
}
