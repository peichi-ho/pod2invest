// ── 論點情境試算(計算機頁面的第二個模擬器) ─────────────────────────────────
// 上方選集 → 切到「論點情境試算」→ ① 勾選情境(可以都不勾) → ② 試算期間與參數(選填) → ③ 按「開始試算」
// → 後端推算試算期間結束時的 EPS 與試算價格(依情境推算，不是預測，畫面不用「目標價」以免誤會)。結果只在按下「開始試算」後顯示，之後再改條件會提示重新試算，
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
  _thesisFilterEpisodes(m === 'thesis');
  if (m === 'thesis' && _thesisPendingScore && String(_thesis.scoreId) !== String(_thesisPendingScore)) {
    loadThesisValuation(_thesisPendingScore);
  }
}

function _thesisFilterEpisodes(on) {
  // 論點情境試算模式時，上方選集清單只列「有可以勾選試算的情境」的集數，避免選到空的；
  // 切回股價模擬就恢復完整清單。深連結補進來、還不知道有沒有情境的集(沒有 has_calc 欄位)照樣列出。
  const sel = document.getElementById('in-episode');
  if (!sel || typeof episodes === 'undefined' || !episodes.length) return;
  const list = on ? episodes.filter(e => e.has_calc !== false) : episodes;
  const current = sel.value;
  sel.innerHTML = list.map(e =>
    `<option value="${e.score_id}">${e.published_at || '未知日期'}　${e.asset_name}(${e.ticker})　${e.podcaster || ''}</option>`
  ).join('');
  if (!list.length) return;
  if (list.some(e => String(e.score_id) === String(current))) {
    sel.value = current;
  } else {
    sel.value = list[0].score_id;
    if (typeof onEpisodeChange === 'function') onEpisodeChange();
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
  // 新上架、還沒拆過情境的節目，後端會當場用 AI 拆(約 20～40 秒)，等太久時說明原因
  clearTimeout(_thesis.slowTimer);
  _thesis.slowTimer = setTimeout(() => {
    if (String(_thesis.scoreId) === String(scoreId) && !_thesis.data)
      _thesisSetStatus('這一集還沒拆解過情境，正在用 AI 拆解（約 20～40 秒，之後再打開就會直接顯示）…');
  }, 4000);
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

// 情境依「對公司是利多還是利空」分成看多／看空兩組(不再用「支持／反對節目論點」，
// 看空的論點裡 EPS↓ 會被歸在「支持」，容易誤以為是利多)
function _thesisSide(s) {
  return s.effect === 'down' ? 'down' : 'up';
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
      clearTimeout(_thesis.slowTimer);
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

// ── 說明泡泡：標題旁的 i 圖示，滑過去(手機點一下)顯示說明，畫面上不放一大段小字(做法同個股頁 assets.js) ──
const _THESIS_NOTES = {
  intro: () => ({
    title: '論點情境試算',
    body: `把節目的論點拆成財務情境，勾選你相信的情境，推算試算期間結束時的 EPS 與試算價格。以節目發布日${_thesis.asOf ? '（' + _thesis.asOf + '）' : ''}的股價、與當時已公告的財報為基準。試算價格是依勾選的情境推算，不是預測。`,
  }),
  choose: () => ({
    title: '選擇情境',
    body: '勾選你相信會發生的情境，可以都不勾（代表公司延續目前的成長）。',
    points: [
      ['看多情境', '對公司是利多的變化。', 'up'],
      ['看空情境', '假設節目提到的風險發生，不代表一定會發生。', 'down'],
    ],
    footer: '每個情境下面的數字，是單獨勾它時參數會調多少。',
  }),
  bear: () => ({ title: '看空情境', body: '節目提到的風險或反方因素。勾選代表「假設這個風險發生」，不代表節目認為一定會發生。' }),
  info: () => ({
    title: '節目還提到',
    body: '這些內容只供參考，不參與試算：技術面與籌碼面、節目或法人提到的價位、總經（利率、資金）、法規風險、沒有數字或只有單季數字的 EPS 預估。',
    footer: '總經、法規風險如果你覺得會影響估值，可以在「手動調整參數」自己調本益比。',
  }),
  months: () => ({
    title: '試算期間',
    body: `推算幾個月後的 EPS 與試算價格。${_thesis.monthsNote || '預設跟這句論點的回測期間一樣。'}`,
    footer: '可以改成 1～36 個月；不滿一年時，營收依期間換算、利潤率的變化依比例反映。',
  }),
  levers: () => ({
    title: '手動調整參數',
    body: '不調整就依勾選的情境自動設定。滑桿下方的「依情境」是情境算出的預設值；改了之後可以按「回到情境預設值」。直接指定 EPS 會跳過營收與利潤率，直接用 EPS × 本益比。',
  }),
  assumptions: () => ({
    title: '計算方式與假設',
    body: '試算價格 = 預估 EPS × 本益比；預估 EPS = 近四季 EPS × 預估獲利 ÷ 近四季獲利。',
    points: [
      ['都不勾', '營收照目前的年增率成長，毛利率、費用率、本益比維持現值。', ''],
      ['勾一個情境', '只調它影響的數字：這家公司過去 5 年的一般波動幅度 × 這類情境的回測校準比例（回測不足的類別用一般比例），最少營收 1 個百分點、利潤率 0.5 個百分點。這是參考值，不是這個事件一定會造成的效果。', ''],
      ['同一個方向不疊加', '例如同時勾需求增加和新產品，營收只調一次；方向相反的會互相抵銷。', ''],
      ['本益比預設不變', '除非勾了估值類的情境；總經、法規只顯示，可以自己調本益比。', ''],
      ['試算期間不滿一年', '營收依期間換算，利潤率的變化依比例反映，本益比不縮放。', ''],
      ['短期情境', '單月、單季或一次性的變化，但仍以一整年的幅度調整，可能偏重。', ''],
      ['財務風險', 'EPS 打 93 折，依回測（樣本 25 句），屬於暫定。', ''],
    ],
    footer: '回測只涵蓋 2025 年起的一段多頭行情；看多、看空依情境對公司的整體效果分組。',
  }),
  waterfall: () => ({
    title: '試算價格怎麼來的',
    body: '從發布日股價開始，先加上「延續目前成長」，再加上每個勾選的情境、手動調整，最後就是試算價格。',
    footer: '每個情境的金額是所有加入順序的平均貢獻：情境之間會互相放大，平均之後跟勾選順序無關。',
  }),
  baseline: () => ({
    title: '試算基準',
    body: '節目發布日當時，已經公告的近四季財報與股價。所有情境都從這些數字往上或往下調整。',
  }),
  short: () => ({ title: '短期情境', body: '節目講的是單月、單季或一次性的變化，但試算仍以一整年的幅度調整，可能偏重。' }),
};

function _tInfo(key, cls = '') {
  return `<span class="material-symbols-outlined text-base leading-none text-outline hover:text-secondary cursor-help align-middle ${cls}"
    role="button" tabindex="0" aria-label="說明" onmouseenter="showThesisNote(event, '${key}')" onmouseleave="hideThesisNote()"
    onclick="event.preventDefault(); event.stopPropagation(); showThesisNote(event, '${key}', true)">info</span>`;
}

function _thesisMountInfo(root) {
  (root || document).querySelectorAll('[data-thesis-info]').forEach(el => {
    if (el.dataset.mounted) return;
    el.dataset.mounted = '1';
    el.outerHTML = _tInfo(el.dataset.thesisInfo);
  });
}

function _thesisNoteHtml(key) {
  const n = _THESIS_NOTES[key] ? _THESIS_NOTES[key]() : null;
  if (!n) return '';
  const tones = { up: 'text-[#ba1a1a] bg-[#ba1a1a]/10', down: 'text-[#1e8e3e] bg-[#1e8e3e]/10' };
  const points = n.points ? `<ul class="mt-2 space-y-1.5">${n.points.map(([k, v, tone]) =>
    `<li class="rounded-md px-2.5 py-1.5 ${tones[tone] ? tones[tone].split(' ')[1] : 'bg-surface-container'}"><div class="font-bold ${tones[tone] ? tones[tone].split(' ')[0] : 'text-tertiary-container'}">${k}</div><div>${v}</div></li>`).join('')}</ul>` : '';
  const footer = n.footer ? `<div class="mt-2 flex items-start gap-1 text-xs text-outline"><span class="material-symbols-outlined text-sm leading-none">lightbulb</span><span>${n.footer}</span></div>` : '';
  return `<div class="flex items-center gap-1 font-bold text-base text-tertiary-container mb-1"><span class="material-symbols-outlined text-lg leading-none">info</span>${n.title}</div><div>${n.body}</div>${points}${footer}`;
}

function showThesisNote(event, key, sticky) {
  let note = document.getElementById('thesis-note');
  if (!note) {
    note = document.createElement('div');
    note.id = 'thesis-note';
    note.className = 'hidden fixed z-50 w-80 max-w-[calc(100vw-16px)] text-sm text-left font-normal bg-surface-container-high rounded-lg p-4 text-on-surface-variant leading-relaxed shadow-lg';
    note.innerHTML = '<div id="thesis-note-arrow" class="absolute -top-1 -translate-x-1/2 w-2 h-2 bg-surface-container-high rotate-45"></div><div id="thesis-note-content"></div>';
    document.body.appendChild(note);
    document.addEventListener('click', () => hideThesisNote(true));
    window.addEventListener('scroll', () => hideThesisNote(true), { passive: true });
  }
  document.getElementById('thesis-note-content').innerHTML = _thesisNoteHtml(key);
  note.dataset.sticky = sticky ? '1' : '';
  note.classList.remove('hidden');
  const rect = event.currentTarget.getBoundingClientRect();
  const margin = 8;
  const w = note.offsetWidth;
  const cx = rect.left + rect.width / 2;
  const left = Math.max(margin, Math.min(cx - w / 2, window.innerWidth - w - margin));
  const below = rect.bottom + 8 + note.offsetHeight < window.innerHeight;
  note.style.left = `${left}px`;
  note.style.top = below ? `${rect.bottom + 8}px` : `${Math.max(margin, rect.top - 8 - note.offsetHeight)}px`;
  const arrow = document.getElementById('thesis-note-arrow');
  arrow.style.left = `${Math.max(12, Math.min(cx - left, w - 12))}px`;
  arrow.classList.toggle('-top-1', below);
  arrow.classList.toggle('-bottom-1', !below);
}

function hideThesisNote(force) {
  const note = document.getElementById('thesis-note');
  if (note && (force || !note.dataset.sticky)) note.classList.add('hidden');
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
  _thesis.asOf = d.as_of || '';

  const sel = document.getElementById('thesis-select');
  if (d.theses && d.theses.length > 1) {
    sel.innerHTML = d.theses.map((t, i) =>
      `<option value="${t.id}" ${t.id === d.thesis_id ? 'selected' : ''}>論點 ${i + 1}：${_escHtml(t.thesis.slice(0, 36))}${t.thesis.length > 36 ? '…' : ''}${t.n_checkable ? '' : '（無可試算情境）'}</option>`).join('');
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
    ? '<span class="inline-block mr-2 px-2.5 py-0.5 rounded-full text-xs font-bold text-[#ba1a1a] bg-[#ba1a1a]/10 align-middle">看多論點</span>'
    : current.direction === 'bearish'
      ? '<span class="inline-block mr-2 px-2.5 py-0.5 rounded-full text-xs font-bold text-[#1e8e3e] bg-[#1e8e3e]/10 align-middle">看空論點</span>' : '';
  textEl.innerHTML = dirChip + _escHtml(current.thesis) +
    (current.timeframe ? `<span class="text-xs text-outline ml-2">（時間：${_escHtml(current.timeframe)}）</span>` : '');
  textEl.classList.remove('hidden');

  document.getElementById('thesis-scenarios').innerHTML = _thesisScenariosHtml(d);
  document.getElementById('thesis-info').innerHTML = _thesisInfoHtml(d);
  document.getElementById('thesis-body').classList.remove('hidden');

  if (!d.available) {
    _thesisSetStatus('這檔股票目前無法試算：' + (d.reason || '資料不足') + '。上方的情境仍可閱讀。');
    document.getElementById('thesis-run').disabled = true;
    document.getElementById('thesis-levers').innerHTML = '';
    document.getElementById('thesis-results').classList.add('hidden');
    return;
  }
  document.getElementById('thesis-run').disabled = false;
  const nCheckable = (d.scenarios || []).filter(s => s.checkable).length;
  _thesisSetStatus(nCheckable ? '' : '這句論點沒有可以勾選試算的情境'
    + (d.no_driver_reason ? `（${d.no_driver_reason}）` : '（只有技術面、價位等參考資訊，或說法太空泛）')
    + '，只能看「延續目前成長」的結果，或在下方手動調整參數。');

  const monthsInput = document.getElementById('thesis-months');
  if (document.activeElement !== monthsInput) monthsInput.value = d.horizon_months;
  _thesis.monthsNote = `預設 ${d.horizon_default} 個月，依${d.horizon_source}。`;

  document.getElementById('thesis-levers').innerHTML = _thesisLeversHtml(d);
  document.getElementById('thesis-reset').classList.toggle('hidden', !(d.overridden && d.overridden.length));
  _thesisRunHint();
}

function _thesisScenarioItem(s, checked) {
  // 一列 = 勾選框｜情境名稱＋標籤｜勾了會調多少(重點)｜節目原文(淡色、最多兩行)
  const tags = [];
  if (s.figures.eps) tags.push(`EPS ${_tNum(s.figures.eps, 2)}${s.figures.year ? '（' + s.figures.year + '）' : ''}${s.figures_source === 'thesis' ? '・取自論點摘要' : ''}`);
  if (s.figures.eps_partial) tags.push(`單季／半年 EPS ${_tNum(s.figures.eps_partial, 2)}`);
  if (s.figures.pe) tags.push(`本益比 ${_tNum(s.figures.pe, 1)} 倍`);
  if (s.figures.target_low) tags.push(`${s.source === 'analyst' ? '法人' : '節目觀點'} ${_tNum(s.figures.target_low, 0)}${s.figures.target_high && s.figures.target_high !== s.figures.target_low ? '～' + _tNum(s.figures.target_high, 0) : ''}`);
  const tagHtml = tags.map(t => `<span class="text-[11px] font-bold px-1.5 py-0.5 rounded bg-surface-container text-on-surface-variant">${_escHtml(t)}</span>`).join('');
  const shortHtml = s.duration === 'short'
    ? `<span class="inline-flex items-center gap-0.5 text-[11px] font-bold px-1.5 py-0.5 rounded bg-[#a16207]/10 text-[#a16207]">短期${_tInfo('short', '!text-sm text-[#a16207]')}</span>` : '';
  const impacts = s.adjust && s.adjust.length ? s.adjust : s.effects;
  const impactHtml = impacts.map(t => `<span class="text-xs font-bold ${s.checkable ? 'text-tertiary-container' : 'text-outline'}">${_escHtml(t)}</span>`)
    .join('<span class="text-outline/50 mx-1">・</span>');
  const box = s.checkable
    ? `<input type="checkbox" id="thesis-cb-${s.idx}" class="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-[#113236]" ${checked ? 'checked' : ''} onchange="thesisToggle(${s.idx}, this.checked)">`
    : '<span class="mt-0.5 h-4 w-4 shrink-0"></span>';
  return `<li class="rounded transition-colors ${checked ? 'bg-tertiary-container/5' : 'hover:bg-surface-container/50'}">
    <label for="thesis-cb-${s.idx}" class="flex gap-3 items-start px-3 py-2.5 ${s.checkable ? 'cursor-pointer' : ''}">
      ${box}
      <div class="min-w-0 flex-1 space-y-1">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span class="font-bold text-sm text-on-surface">${_escHtml(s.label)}</span>${shortHtml}${tagHtml}
        </div>
        ${impactHtml ? `<div class="flex flex-wrap items-center">${impactHtml}</div>` : ''}
        <p class="text-xs leading-relaxed text-outline line-clamp-2" title="${_escHtml(s.quote)}"><span class="font-bold mr-1">節目原文</span>${_escHtml(s.quote)}</p>
      </div>
    </label>
  </li>`;
}

function _thesisScenariosHtml(d) {
  const all = d.scenarios || [];
  const checked = new Set([..._thesis.checked]);
  // 看多、看空用卡片左側色條＋小圖示區分(台股慣例：看多紅、看空綠)，字級跟其他區塊一致
  const card = (kind, list) => {
    const up = kind === 'up';
    const tone = up ? 'border-[#ba1a1a] text-[#ba1a1a]' : 'border-[#1e8e3e] text-[#1e8e3e]';
    return `<div class="bg-surface-container-lowest rounded border-l-4 ${tone.split(' ')[0]} px-3 py-3 min-w-0">
      <div class="flex items-center gap-1.5 px-3 pb-1 ${tone.split(' ')[1]}">
        <span class="material-symbols-outlined text-lg leading-none">${up ? 'trending_up' : 'trending_down'}</span>
        <h5 class="text-sm font-bold">${up ? '看多情境' : '看空情境'}</h5>
        ${up ? '' : _tInfo('bear', '!text-sm')}
      </div>
      ${list.length ? `<ul class="space-y-0.5">${list.map(s => _thesisScenarioItem(s, checked.has(s.idx))).join('')}</ul>`
                    : `<p class="text-xs text-outline px-3 py-1.5">這句論點沒有${up ? '看多' : '看空'}的情境</p>`}
    </div>`;
  };
  return card('up', all.filter(s => s.checkable && _thesisSide(s) === 'up'))
    + card('down', all.filter(s => s.checkable && _thesisSide(s) === 'down'));
}

function _thesisInfoHtml(d) {
  // 只供參考的情境(技術面、目標價位、總經、法規風險、沒有數字的 EPS)收在折疊區
  const info = (d.scenarios || []).filter(s => !s.checkable);
  if (!info.length) return '';
  return `<details class="bg-surface-container-lowest rounded px-6 py-3">
    <summary class="text-sm font-bold text-tertiary-container cursor-pointer select-none flex items-center gap-1.5">
      節目還提到（只供參考）<span class="text-[11px] font-bold px-1.5 py-0.5 rounded bg-surface-container text-outline">${info.length}</span>${_tInfo('info', '!text-sm')}
    </summary>
    <ul class="mt-2 -mx-3 space-y-0.5">${info.map(s => _thesisScenarioItem(s, false)).join('')}</ul>
  </details>`;
}

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

  // 計算過程：每個數字怎麼來的。跟預設(都不勾、沒有手動調整)不同的參數用強調色標出來，讓使用者看得出改了哪裡
  const CH = 'text-secondary font-bold';
  const hl = (text, on) => on ? `<span class="${CH}">${text}</span>` : text;
  const diff = (a, c) => a != null && c != null && Math.abs(a - c) > 1e-9;
  const baseGrowth = b.revenue_growth != null ? b.revenue_growth
    : ((b.history || {}).revenue_growth ? b.history.revenue_growth.p50 : 0);
  const growthChanged = diff(L.revenue_growth, baseGrowth);
  const marginLabels = { gross_margin: '毛利率', opex_ratio: '營業費用率', operating_margin: '營業利益率', net_margin: '淨利率' };
  const marginChanges = Object.entries(marginLabels)
    .filter(([k]) => diff(L[k], b[k]))
    .map(([k, name]) => `${name} ${_tPct(b[k])} → ${_tPct(L[k])}`);
  const riskChanged = (L.eps_multiplier || 1) !== 1;
  const baseMultiple = p.route === 'ps' ? b.ps : b.pe;
  const multipleChanged = diff(p.multiple, baseMultiple);

  const steps = [];
  steps.push(['預估營收', `近四季 ${_tMoney(b.revenue, d.ticker)} ×（1 + 年增率 ${hl(_tPct(L.revenue_growth), growthChanged)}）<sup>${m}/12</sup>`
    + (growthChanged ? `<span class="text-outline">（預設 ${_tPct(baseGrowth)}）</span>` : ''), hl(_tMoney(p.revenue, d.ticker), growthChanged)]);
  if (p.route !== 'ps') {
    if (L.eps_override) {
      steps.push(['預估 EPS', hl('直接使用指定的 EPS', true), hl(_tNum(p.eps, 2), true)]);
    } else {
      const profitName = p.route === 'net' ? '淨利' : '營業利益';
      const epsChanged = marginChanges.length > 0 || riskChanged;
      steps.push(['預估 EPS', `近四季${b.eps_note ? '本業' : ''} ${_tNum(b.eps, 2)} × ${profitName}倍數 ${_tNum(p.profit_factor, 3)}`
        + (riskChanged ? ` × ${hl('財務風險 ' + _tNum(L.eps_multiplier, 2), true)}` : '')
        + (marginChanges.length ? `<br>${hl(marginChanges.join('、'), true)}` : ''), hl(_tNum(p.eps, 2), epsChanged)]);
    }
    steps.push([multipleLabel, multipleChanged
      ? hl(`現值 ${_tNum(baseMultiple, 1)} 倍 → ${_tNum(p.multiple, 1)} 倍（依情境或手動調整）`, true)
      : `發布日股價 ${_tNum(b.price, 2)} ÷ 近四季${b.eps_note ? '本業' : ''} EPS ${_tNum(b.eps, 2)}`, hl(`${_tNum(p.multiple, 1)} 倍`, multipleChanged)]);
    steps.push(['試算價格', `預估 EPS ${_tNum(p.eps, 2)} × ${multipleLabel} ${_tNum(p.multiple, 1)}`, _tNum(p.target_price, 2)]);
  } else {
    steps.push([multipleLabel, multipleChanged ? hl(`現值 ${_tNum(b.ps, 1)} 倍 → ${_tNum(p.multiple, 1)} 倍`, true) : `現值 ${_tNum(b.ps, 1)} 倍`,
      hl(`${_tNum(p.multiple, 1)} 倍`, multipleChanged)]);
    steps.push(['試算價格', `預估營收 × ${multipleLabel} ÷ 股數`, _tNum(p.target_price, 2)]);
  }
  const anyChanged = growthChanged || marginChanges.length || riskChanged || multipleChanged || !!L.eps_override;
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
  const growthNote = (b.growth_note ? `<li class="text-[#a16207] font-bold">· ${_escHtml(b.growth_note)}</li>` : '')
    + (b.eps_note ? `<li class="text-[#a16207] font-bold">· ${_escHtml(b.eps_note)}</li>` : '');
  const targets = (d.analyst_targets || []).map(t =>
    `${_tNum(t.low, 0)}${t.high && t.high !== t.low ? '～' + _tNum(t.high, 0) : ''}（${t.source === 'analyst' ? '法人' : '節目觀點'}）`);
  const notes = (d.notes || []).map(n => `<li>· ${_escHtml(n)}</li>`).join('');

  return `
    <div class="flex items-end justify-between gap-3 flex-wrap">
      <div>
        <div class="text-xs text-outline font-bold uppercase tracking-widest">${m} 個月後的試算價格（每股）</div>
        <div class="font-['Epilogue'] text-3xl font-bold text-tertiary-container">${_tNum(p.target_price, 2)}</div>
      </div>
      <div class="text-right">
        <div class="text-xs text-outline font-bold uppercase tracking-widest">相對發布日股價 ${_tNum(b.price, 2)}</div>
        <div class="font-['Epilogue'] text-2xl font-bold ${_tReturnColor(p.implied_return)}">${_tPct(p.implied_return, true)}</div>
      </div>
    </div>
    <div>
      <div class="flex items-baseline justify-between gap-2 mb-1">
        <div class="text-xs text-outline font-bold uppercase tracking-widest">計算過程</div>
        ${anyChanged ? '<div class="text-xs text-secondary font-bold">● 跟預設（都不勾）不同的數字</div>' : ''}
      </div>
      ${stepRows}
    </div>
    ${(marginNote || growthNote || notes) ? `<ul class="text-xs text-outline space-y-0.5 pt-2 border-t border-outline-variant/15">${growthNote}${marginNote}${notes}</ul>` : ''}
    ${targets.length ? `<div class="text-xs text-outline pt-2 border-t border-outline-variant/15">節目提到的價位（僅供對照）：${targets.join('、')}</div>` : ''}`;
}

function _thesisBaselineHtml(d) {
  const b = d.baseline;
  const h = b.history || {};
  const tile = (label, value, sub) => `<div class="bg-surface-container rounded-lg px-4 py-3 min-w-0">
      <div class="text-xs text-outline font-bold">${label}</div>
      <div class="text-lg font-bold text-tertiary-container font-['Epilogue'] tabular-nums">${value}</div>
      ${sub ? `<div class="text-xs text-outline mt-0.5">${sub}</div>` : ''}
    </div>`;
  const tiles = [
    tile('財報截至', _escHtml(b.ttm_end || '—'), '近四季'),
    tile(b.eps_note ? '近四季 EPS（本業）' : '近四季 EPS', _tNum(b.eps, 2), b.eps_note ? `財報 ${_tNum(b.eps_reported, 2)}` : ''),
  ];
  if (b.pe != null) tiles.push(tile('本益比', `${_tNum(b.pe, 1)} 倍`, h.pe ? `5 年中位數 ${_tNum(h.pe.p50, 1)} 倍` : ''));
  if (b.gross_margin != null) tiles.push(tile('毛利率', _tPct(b.gross_margin), ''));
  if (b.operating_margin != null) tiles.push(tile('營業利益率', _tPct(b.operating_margin), ''));
  if (b.revenue_growth != null) tiles.push(tile('營收年增率', _tPct(b.revenue_growth), '目前'));
  return `<div class="flex items-center gap-1.5"><h5 class="text-sm font-bold text-tertiary-container">試算基準（發布日 ${_escHtml(d.as_of || '')}）</h5>${_tInfo('baseline')}</div>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">${tiles.join('')}</div>
    ${b.reason ? `<p class="text-xs text-[#a16207] font-bold">${_escHtml(b.reason)}</p>` : ''}`;
}

function _thesisWaterfallHtml(d) {
  const steps = d.waterfall;
  const price = d.baseline.price;
  if (!steps || !steps.length || !price) return '<p class="text-sm text-outline">這組條件算不出試算價格，無法拆解。</p>';
  const values = [price, ...steps.map(s => s.value)];
  const hi = Math.max(...values) * 1.05;
  const pos = v => Math.max(0, Math.min(100, v / hi * 100));

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
  // 情境的金額可能只有幾元(例如股價 250 元、營收多 1 個百分點)：小金額多顯示小數，不要四捨五入成 0
  const fmtDelta = v => _tNum(v, v >= 100 ? 0 : v >= 10 ? 1 : 2);
  const rows = [row('發布日股價', d.as_of, 0, pos(price), 'bg-[#113236]/40', fmt(price))];
  let prev = price;
  steps.forEach(s => {
    const up = s.delta >= 0;
    const zero = Math.abs(s.delta) < 0.0005 * price;   // 不到股價的 0.05% 才當成 0
    let sub = s.kind === 'trend' ? '營收照目前年增率成長，其他維持現值' : s.kind === 'manual' ? '滑桿或直接指定 EPS' : '';
    if (s.kind === 'scenario' && zero) sub = '在這組條件下幾乎不影響試算價格';
    const left = pos(Math.min(prev, s.value));
    const width = pos(Math.max(prev, s.value)) - left;
    const color = zero ? 'bg-outline/40' : up ? 'bg-[#ba1a1a]/70' : 'bg-[#1e8e3e]/70';
    const delta = zero ? '0' : (up ? '+' : '−') + fmtDelta(Math.abs(s.delta));
    rows.push(row(s.label, sub, left, width, color, '', delta, zero ? 'text-outline' : up ? 'text-[#ba1a1a]' : 'text-[#1e8e3e]'));
    prev = s.value;
  });
  rows.push(row('試算價格', `${d.projection.months} 個月後`, 0, pos(prev), 'bg-[#113236]', fmt(prev)));

  return `
    <div class="flex items-baseline justify-between gap-2">
      <h5 class="text-xs text-outline font-bold uppercase tracking-widest flex items-center gap-1">試算價格怎麼來的${_tInfo('waterfall')}</h5>
      <span class="text-xs text-outline">每一步相對前一步的變化</span>
    </div>
    <div class="space-y-1">${rows.join('')}</div>`;
}

_thesisMountInfo();

// calculator.js 載入時已經呼叫過 setMode('manual')，那時這個檔案還沒載入，這裡補套用一次初始模式
onCalcModeChanged(typeof mode !== 'undefined' ? mode : 'manual');
