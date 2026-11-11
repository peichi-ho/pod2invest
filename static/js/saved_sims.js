// ── 試算紀錄：儲存、列出、重現計算機的「股價模擬」與「論點情境試算」 ─────────────────
// 存的是「條件」(params)，打開時用同樣條件重新試算才能重現；另外存當時的結果快照(result)，
// 清單直接顯示，重算結果不同(資料更新、或拔靴模擬本身的隨機性)時也能對照。
// 後端：/api/accounts/simulations/(apps/accounts/views.py SavedSimulationsAPIView)，只開放登入後使用。
// 依賴 calculator.js(_escHtml、setMode、onEpisodeChange、runSimulation、lastResult、lastSimParams、episodes…)
// 與 thesis_calc.js(_thesis、_thesisFetch、thesisRun、showCalcModule)。

const SAVED_SIMS_URL = '/api/accounts/simulations/';
const _SAVED_MODULE_LABEL = { sim: '股價模擬', thesis: '論點情境試算' };

function _savedSimsLoggedIn() {
  if (typeof window.IS_AUTHENTICATED !== 'undefined' && !window.IS_AUTHENTICATED) {
    if (typeof showLoginPrompt === 'function') showLoginPrompt();
    return false;
  }
  return true;
}

function _savedPct(x) {
  if (x == null) return '—';
  return (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%';
}

function _savedNum(x, d = 2) {
  if (x == null) return '—';
  return Number(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function _savedTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── 目前這次試算的條件、結果快照、預設名稱 ──
function _currentSimSnapshot() {
  if (!lastResult || !lastSimParams) return { error: '請先按「開始模擬」，跑出結果後再儲存。' };
  const r = lastResult;
  const p = { ...lastSimParams };
  p.invested = Number(document.getElementById('in-invested').value) || 0;
  const ep = p.score_id ? (episodes || []).find(e => String(e.score_id) === String(p.score_id)) : null;
  const assetName = r.asset_name || (ep && ep.asset_name) || '';
  const result = {
    ticker: r.ticker, asset_name: assetName, start_price: r.start_price, months: r.months || p.months,
    median_return: r.median_return, conservative_return: r.conservative_return, aggressive_return: r.aggressive_return,
    episode: ep ? { published_at: ep.published_at, podcaster: ep.podcaster } : null,
  };
  const who = assetName && assetName !== r.ticker ? assetName : r.ticker;
  const name = `${who} · ${_macroWordLabel(p.macro)} · 風險${_riskWordLabel(p.risk)} · ${p.months} 個月`;
  return { module: 'sim', params: p, result, name };
}

function _currentThesisSnapshot() {
  const d = _thesis.shown;
  if (!d || !_thesis.shownParams) return { error: '請先按「開始試算」，跑出結果後再儲存。' };
  if (_thesis.stale) return { error: '條件已經改變，請先按「開始試算」更新結果，再儲存。' };
  const p = JSON.parse(JSON.stringify(_thesis.shownParams));
  const ep = (episodes || []).find(e => String(e.score_id) === String(p.score_id));
  const thesis = (d.theses || []).find(t => t.id === d.thesis_id);
  const result = {
    ticker: d.ticker, asset_name: d.asset_name, as_of: d.as_of, price: d.baseline && d.baseline.price,
    months: d.projection.months, target_price: d.projection.target_price, implied_return: d.projection.implied_return,
    thesis: thesis ? thesis.thesis.slice(0, 80) : '',
    episode: ep ? { published_at: ep.published_at, podcaster: ep.podcaster } : { published_at: d.as_of, podcaster: '' },
  };
  const labels = p.checked.map(c => c.label);
  let summary = !labels.length ? '延續目前成長'
    : labels.length <= 2 ? labels.join('＋') : `${labels.length} 個情境`;
  if (Object.keys(p.overrides || {}).length) summary += '＋手動調整';
  const name = `${d.asset_name || d.ticker} · ${summary} · ${d.projection.months} 個月`;
  return { module: 'thesis', params: p, result, name };
}

// ── 共用彈窗 ──
function _savedModal(id, inner) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.className = 'fixed inset-0 z-50 hidden items-center justify-center bg-black/30 px-4';
    el.addEventListener('click', e => { if (e.target === el) _savedModalClose(id); });
    document.body.appendChild(el);
  }
  el.innerHTML = inner;
  el.classList.remove('hidden');
  el.classList.add('flex');
  return el;
}

function _savedModalClose(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.add('hidden'); el.classList.remove('flex'); }
}

// ── 儲存 ──
let _savedPending = null;

function openSaveSimDialog(module) {
  if (!_savedSimsLoggedIn()) return;
  const snap = module === 'thesis' ? _currentThesisSnapshot() : _currentSimSnapshot();
  if (snap.error) { alert(snap.error); return; }
  _savedPending = snap;
  _savedModal('saved-sim-save', `
    <div class="bg-surface-container-lowest rounded-xl w-full max-w-md p-6 space-y-4 shadow-xl">
      <h3 class="font-['Epilogue'] font-bold text-lg text-tertiary-container">儲存試算紀錄</h3>
      <div class="space-y-1.5">
        <label for="saved-sim-name" class="text-sm font-bold text-outline">名稱</label>
        <input id="saved-sim-name" type="text" maxlength="80" value="${_escHtml(snap.name)}"
          class="w-full px-4 py-2.5 bg-surface-container border-none rounded-DEFAULT focus:ring-2 focus:ring-secondary text-sm"
          onkeydown="if (event.key === 'Enter') saveCurrentSim()">
        <p class="text-xs text-outline">${_SAVED_MODULE_LABEL[module]}｜之後可以在「試算紀錄」用同樣的條件重新試算</p>
      </div>
      <p id="saved-sim-error" class="hidden text-sm text-error font-bold"></p>
      <div class="flex justify-end gap-2">
        <button type="button" class="px-5 py-2 rounded-full text-sm font-bold text-on-surface-variant hover:bg-surface-container" onclick="_savedModalClose('saved-sim-save')">取消</button>
        <button type="button" id="saved-sim-save-btn" class="px-5 py-2 rounded-full text-sm font-bold bg-tertiary-container text-white hover:opacity-90" onclick="saveCurrentSim()">儲存</button>
      </div>
    </div>`);
  const input = document.getElementById('saved-sim-name');
  input.focus();
  input.select();
}

async function saveCurrentSim() {
  if (!_savedPending) return;
  const name = document.getElementById('saved-sim-name').value.trim();
  const err = document.getElementById('saved-sim-error');
  const btn = document.getElementById('saved-sim-save-btn');
  if (!name) { err.textContent = '請輸入名稱'; err.classList.remove('hidden'); return; }
  btn.disabled = true;
  try {
    const resp = await fetch(SAVED_SIMS_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module: _savedPending.module, name, params: _savedPending.params, result: _savedPending.result }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401) { _savedModalClose('saved-sim-save'); _savedSimsLoggedIn(); return; }
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    _savedModalClose('saved-sim-save');
    _savedToast(`已儲存「${name}」`);
  } catch (e) {
    err.textContent = '儲存失敗：' + e.message;
    err.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

function _savedToast(text) {
  let el = document.getElementById('saved-sim-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'saved-sim-toast';
    el.className = 'fixed left-1/2 -translate-x-1/2 bottom-28 z-50 px-5 py-3 rounded-full bg-tertiary-container text-white text-sm font-bold shadow-lg hidden';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 2500);
}

// ── 清單 ──
let _savedItems = [];

function _savedItemMeta(it) {
  const r = it.result || {};
  const ep = r.episode && r.episode.published_at
    ? `${r.episode.podcaster ? r.episode.podcaster + ' ' : ''}${r.episode.published_at}` : '手動輸入';
  const value = it.module === 'thesis'
    ? `試算價格 ${_savedNum(r.target_price)}（${_savedPct(r.implied_return)}）`
    : `中位數報酬 ${_savedPct(r.median_return)}`;
  return `${_SAVED_MODULE_LABEL[it.module]}｜${ep}｜${value}`;
}

function savedSimRowHtml(it, compact) {
  return `<li class="rounded-DEFAULT bg-surface-container-lowest px-4 py-3 flex items-start gap-3">
    <span class="material-symbols-outlined text-xl leading-none mt-0.5 ${it.module === 'thesis' ? 'text-secondary' : 'text-tertiary-container'}">${it.module === 'thesis' ? 'account_tree' : 'show_chart'}</span>
    <div class="min-w-0 flex-1">
      <div class="font-bold text-sm text-on-surface truncate" title="${_escHtml(it.name)}">${_escHtml(it.name)}</div>
      <div class="text-xs text-outline mt-0.5">${_escHtml(_savedItemMeta(it))}</div>
      <div class="text-[11px] text-outline/80 mt-0.5">儲存於 ${_savedTime(it.created_at)}</div>
    </div>
    <div class="flex items-center gap-1 shrink-0">
      <button type="button" class="px-3 py-1.5 rounded-full text-xs font-bold bg-tertiary-container text-white hover:opacity-90" onclick="openSavedSim(${it.id})">開啟</button>
      ${compact ? '' : `<button type="button" class="p-1.5 rounded-full text-outline hover:bg-surface-container" title="重新命名" onclick="renameSavedSim(${it.id})"><span class="material-symbols-outlined text-lg leading-none">edit</span></button>`}
      <button type="button" class="p-1.5 rounded-full text-outline hover:bg-surface-container" title="刪除" onclick="deleteSavedSim(${it.id})"><span class="material-symbols-outlined text-lg leading-none">delete</span></button>
    </div>
  </li>`;
}

async function fetchSavedSims(limit) {
  const resp = await fetch(SAVED_SIMS_URL + (limit ? `?limit=${limit}` : ''));
  if (resp.status === 401) return { items: [], total: 0, unauth: true };
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  (data.items || []).forEach(it => { if (!_savedItems.some(x => x.id === it.id)) _savedItems.push(it); });
  return data;
}

async function openSavedSimsList() {
  if (!_savedSimsLoggedIn()) return;
  _savedModal('saved-sim-list', `
    <div class="bg-surface-container-low rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-xl">
      <div class="flex items-center justify-between px-6 pt-5 pb-3">
        <h3 class="font-['Epilogue'] font-bold text-lg text-tertiary-container">試算紀錄</h3>
        <button type="button" class="p-1.5 rounded-full text-outline hover:bg-surface-container" onclick="_savedModalClose('saved-sim-list')"><span class="material-symbols-outlined">close</span></button>
      </div>
      <div id="saved-sim-list-body" class="px-6 pb-6 overflow-y-auto"><p class="text-sm text-outline">載入中…</p></div>
    </div>`);
  await _renderSavedSimsList();
}

async function _renderSavedSimsList() {
  const body = document.getElementById('saved-sim-list-body');
  if (!body) return;
  try {
    _savedItems = [];
    const data = await fetchSavedSims();
    _savedItems = data.items || [];
    body.innerHTML = _savedItems.length
      ? `<p class="text-xs text-outline mb-3">${data.total} / ${data.max} 筆。開啟會用當時的條件重新試算。</p>
         <ul class="space-y-2">${_savedItems.map(it => savedSimRowHtml(it, false)).join('')}</ul>`
      : '<p class="text-sm text-outline py-6 text-center">還沒有試算紀錄。在試算結果旁按「儲存試算」就會出現在這裡。</p>';
  } catch (e) {
    body.innerHTML = `<p class="text-sm text-error">載入失敗：${_escHtml(e.message)}</p>`;
  }
}

async function renameSavedSim(id) {
  const it = _savedItems.find(x => x.id === id);
  if (!it) return;
  const name = prompt('新的名稱', it.name);
  if (name == null || !name.trim() || name.trim() === it.name) return;
  const resp = await fetch(`${SAVED_SIMS_URL}${id}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) { alert('改名失敗：' + (data.error || resp.statusText)); return; }
  it.name = data.name;
  await _renderSavedSimsList();
  if (typeof loadProfileSavedSims === 'function') loadProfileSavedSims();
}

async function deleteSavedSim(id) {
  const it = _savedItems.find(x => x.id === id);
  if (!confirm(`刪除「${it ? it.name : '這筆紀錄'}」？`)) return;
  const resp = await fetch(`${SAVED_SIMS_URL}${id}/`, { method: 'DELETE' });
  if (!resp.ok) { alert('刪除失敗'); return; }
  _savedItems = _savedItems.filter(x => x.id !== id);
  await _renderSavedSimsList();
  if (typeof loadProfileSavedSims === 'function') loadProfileSavedSims();
}

// ── 開啟：用當時的條件重新試算 ──
async function openSavedSim(id) {
  const it = _savedItems.find(x => x.id === id);
  if (!it) return;
  _savedModalClose('saved-sim-list');
  const calcPage = document.getElementById('page-calculator');
  if (typeof showPage === 'function' && calcPage && !calcPage.classList.contains('active')) showPage('calculator');
  if (!episodes || !episodes.length) {
    try { await loadEpisodes(); } catch (e) { /* 載入失敗時下面會提示 */ }
  }
  if (it.module === 'thesis') await _restoreThesis(it);
  else await _restoreSim(it);
}

// 選集清單只列還沒到期的論點：存檔後那一集過期就不在清單裡，補一個選項進去才選得到
function _ensureEpisodeOption(scoreId, it) {
  const sel = document.getElementById('in-episode');
  if ([...sel.options].some(o => String(o.value) === String(scoreId))) return true;
  const r = it.result || {};
  const opt = document.createElement('option');
  opt.value = scoreId;
  opt.textContent = `${(r.episode && r.episode.published_at) || '—'}　${r.asset_name || r.ticker || ''}(${r.ticker || ''})　${(r.episode && r.episode.podcaster) || ''}（試算紀錄）`;
  sel.insertBefore(opt, sel.firstChild);
  return false;
}

async function _restoreSim(it) {
  const p = it.params || {};
  setMode(p.mode === 'episode' ? 'episode' : 'manual');
  if (typeof showCalcModule === 'function') showCalcModule('sim');
  const notes = [];
  if (p.mode === 'episode' && p.score_id) {
    if (!_ensureEpisodeOption(p.score_id, it)) notes.push('這一集已經不在選集清單裡（論點已到期），仍用當時的資料重新模擬。');
    document.getElementById('in-episode').value = p.score_id;
    if (episodes.some(e => String(e.score_id) === String(p.score_id))) onEpisodeChange();
  } else {
    document.getElementById('in-ticker').value = p.ticker || '';
  }
  const m = Number(p.months) || 12;
  document.getElementById('in-months').value = m;
  document.getElementById('slider-months').value = Math.min(36, m);
  document.getElementById('lbl-months').textContent = m;
  setScoreButton('risk', Number(p.risk) || 0);
  setScoreButton('macro', Number(p.macro) || 0);
  if (p.nsim) document.getElementById('in-nsim').value = p.nsim;
  const inv = document.getElementById('in-invested');
  inv.value = p.invested ? p.invested : '';
  if (typeof syncInvestedFromInput === 'function') syncInvestedFromInput();
  await runSimulation();
  const r = it.result || {};
  if (lastResult) {
    const diff = r.median_return != null && Math.abs((lastResult.median_return || 0) - r.median_return) > 0.005;
    notes.unshift(`已開啟試算紀錄「${it.name}」。當時中位數報酬 ${_savedPct(r.median_return)}`
      + (diff ? `，這次重新模擬是 ${_savedPct(lastResult.median_return)}（拔靴模擬每次隨機抽樣，結果會有些差異；股價資料更新也會影響）。` : '，這次結果相同。'));
  }
  _savedShowNote('result-panel', notes);
}

async function _restoreThesis(it) {
  const p = it.params || {};
  setMode('episode');
  // 先切到論點情境試算(選集清單會換成只列可試算的)，再選回那一集
  showCalcModule('thesis');
  const notes = [];
  if (!_ensureEpisodeOption(p.score_id, it)) notes.push('這一集已經不在選集清單裡（論點已到期），仍用當時的條件重新試算。');
  document.getElementById('in-episode').value = p.score_id;
  if (episodes.some(e => String(e.score_id) === String(p.score_id))) onEpisodeChange();
  // 直接設定論點情境試算的狀態再試算(不等 showCalcModule 觸發的那次載入；序號較新的請求會蓋掉它)
  _thesis.scoreId = p.score_id;
  _thesis.thesisId = p.thesis_id;
  _thesis.months = p.months || null;
  _thesis.overrides = { ...(p.overrides || {}) };
  _thesis.checked = new Set();
  _thesis.shown = null;
  _thesis.stale = false;
  const d = await _thesisFetch(0, true);
  if (!d) return;
  if (p.thesis_id && d.thesis_id !== p.thesis_id) notes.push('當時的論點已經更新或不存在，改用這一集的第一句論點。');
  let missing = 0;
  (p.checked || []).forEach(c => {
    const s = (d.scenarios || []).find(x => x.checkable && x.type === c.type && x.quote === c.quote);
    if (s) _thesis.checked.add(s.idx); else missing += 1;
  });
  if (missing) notes.push(`有 ${missing} 個當時勾選的情境已經重新拆解或不存在，沒辦法還原，其他條件照當時設定。`);
  await thesisRun();
  const r = it.result || {};
  const now = _thesis.shown && _thesis.shown.projection;
  if (now) {
    const diff = r.target_price != null && Math.abs((now.target_price || 0) - r.target_price) > 0.005;
    notes.unshift(`已開啟試算紀錄「${it.name}」。當時試算價格 ${_savedNum(r.target_price)}`
      + (diff ? `，現在重新試算是 ${_savedNum(now.target_price)}（情境、財報或校準方式更新後，結果會不同）。` : '，現在重新試算結果相同。'));
  }
  _savedShowNote('thesis-results', notes);
}

function _savedShowNote(containerId, notes) {
  const box = document.getElementById(containerId);
  if (!box || !notes.length) return;
  let el = document.getElementById(containerId + '-saved-note');
  if (!el) {
    el = document.createElement('div');
    el.id = containerId + '-saved-note';
    el.className = 'rounded-DEFAULT bg-secondary/10 text-secondary text-sm px-5 py-3 space-y-1';
    box.insertBefore(el, box.firstChild);
  }
  el.innerHTML = notes.map(n => `<p>${_escHtml(n)}</p>`).join('');
  el.classList.remove('hidden');
}

// 重新試算、換一集時，把「已開啟試算紀錄」的說明收起來，避免跟新的結果混在一起
function hideSavedSimNotes() {
  ['result-panel', 'thesis-results'].forEach(id => {
    const el = document.getElementById(id + '-saved-note');
    if (el) el.classList.add('hidden');
  });
}
