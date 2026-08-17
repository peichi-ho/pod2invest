// ── Continue Listening ────────────────────────────────────────
let _discCurrentId = null;
let _hotTagNames = [];  // shared with AI question cards

// ── Weekly Signals ──────────────────────────────────────────────
let _signalDir = 'bullish';
let _signalData = null;
let _signalRecordsByAssetWeek = {};
let _signalWeeks = [];
let _signalWeekIndex = 0;

function renderContinueListening(s) {
  const el = document.getElementById('continue-listening-card');
  if (!s) { el.innerHTML = '<p class="text-outline text-sm">暫無資料</p>'; return; }
  const st = cardStyle(s.podcaster || s.source_filename);
  const title = (s.source_filename || '').replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 40);
  _discCurrentId = s.id;
  el.innerHTML = `
    <div class="bg-surface-container-lowest p-6 rounded-lg shadow-[0px_20px_40px_rgba(28,28,22,0.04)] flex flex-col md:flex-row gap-6 items-center">
      <div class="w-full md:w-32 h-32 rounded-lg overflow-hidden flex-shrink-0 cursor-pointer" onclick="openDeepDive(${s.id})">
        ${podcastAvatar(s.podcaster, st.bg, st.icon)}
      </div>
      <div class="flex-grow space-y-3 w-full">
        <div class="flex justify-between items-start">
          <div class="cursor-pointer" onclick="openDeepDive(${s.id})">
            <p class="text-secondary font-label text-xs font-bold uppercase tracking-widest">${s.podcaster || '未知節目'}</p>
            <h3 class="text-xl font-bold text-tertiary-container mt-1 font-['Epilogue'] hover:text-secondary transition-colors">${title}</h3>
          </div>
          <button id="disc-play-btn" onclick="discTogglePlay(${s.id})" class="w-12 h-12 rounded-full bg-on-primary-container text-white flex items-center justify-center shadow-lg hover:scale-105 transition-transform flex-shrink-0 ml-4">
            <span id="disc-play-icon" class="material-symbols-outlined" style="font-variation-settings:'FILL' 1">play_arrow</span>
          </button>
        </div>
        <div class="space-y-2">
          <div id="disc-progress-bar" onclick="discSeek(event)" class="h-1.5 w-full bg-surface-container rounded-full overflow-hidden cursor-pointer">
            <div id="disc-progress-fill" class="h-full bg-on-primary-container rounded-full transition-none" style="width:0%"></div>
          </div>
          <div class="flex justify-between text-[10px] font-bold text-outline uppercase tracking-wider">
            <span id="disc-current-time">0:00</span><span id="disc-duration">--:--</span>
          </div>
        </div>
      </div>
    </div>`;
}

async function discTogglePlay(summaryId) {
  const audio = document.getElementById('disc-audio');
  const icon  = document.getElementById('disc-play-icon');
  if (audio._loadedId === summaryId) {
    if (audio.paused) { audio.play(); icon.textContent = 'pause'; }
    else { audio.pause(); icon.textContent = 'play_arrow'; }
    return;
  }
  icon.textContent = 'hourglass_empty';
  try {
    const res = await fetch(`/api/summaries/${summaryId}/`);
    const data = await res.json();
    if (!data.audio_url) { icon.textContent = 'play_arrow'; alert('此集沒有音檔'); return; }
    audio.src = data.audio_url;
    audio._loadedId = summaryId;
    audio.play();
    icon.textContent = 'pause';
  } catch(e) { icon.textContent = 'play_arrow'; }
}

function discSeek(e) {
  const audio = document.getElementById('disc-audio');
  if (!audio.duration) return;
  const bar  = document.getElementById('disc-progress-bar');
  const rect = bar.getBoundingClientRect();
  audio.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * audio.duration;
}

let _discDragging = false;
document.addEventListener('mousedown', e => {
  const bar = document.getElementById('disc-progress-bar');
  if (bar && bar.contains(e.target)) { _discDragging = true; discSeek(e); }
});
document.addEventListener('mousemove', e => { if (_discDragging) discSeek(e); });
document.addEventListener('mouseup', () => { _discDragging = false; });
document.addEventListener('touchstart', e => {
  const bar = document.getElementById('disc-progress-bar');
  if (bar && bar.contains(e.target)) { _discDragging = true; discSeek(e.touches[0]); }
}, { passive: true });
document.addEventListener('touchmove', e => { if (_discDragging) discSeek(e.touches[0]); }, { passive: true });
document.addEventListener('touchend', () => { _discDragging = false; });

// ── Recommended For You ───────────────────────────────────────
function renderRecommended(list) {
  const el = document.getElementById('recommended-cards');
  if (!list.length) { el.innerHTML = '<p class="text-outline text-sm">暫無資料</p>'; return; }
  el.innerHTML = list.map(s => {
    const st  = cardStyle(s.podcaster || s.source_filename);
    const title = (s.source_filename || '').replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 40);
    const tag = (s.tags || [])[0] || s.podcaster || 'Podcast';
    return `
      <div onclick="openDeepDive(${s.id})" class="flex-shrink-0 w-52 bg-surface-container-lowest rounded-lg overflow-hidden cursor-pointer group hover:shadow-lg transition-shadow border border-outline-variant/10">
        <div class="h-36 w-full overflow-hidden">
          ${podcastAvatar(s.podcaster, st.bg, st.icon)}
        </div>
        <div class="p-4 space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-[10px] font-bold text-secondary uppercase tracking-widest truncate max-w-[60%]">${tag}</span>
            <span class="text-[10px] font-bold text-on-primary-container">New</span>
          </div>
          <h4 class="font-['Epilogue'] font-bold text-sm text-tertiary-container leading-snug group-hover:text-secondary transition-colors line-clamp-2">${title}</h4>
          <p class="text-[10px] text-outline truncate">${s.podcaster || ''}</p>
        </div>
      </div>`;
  }).join('');
}

// ── Preference-based scoring ──────────────────────────────────
const _PREF_TAG_MAP = {
  markets: { TW: ['台股'], US: ['美股'], Crypto: ['加密貨幣', '區塊鏈 / 加密貨幣'] },
  style:   { Technical: ['投資策略'], Fundamental: ['公司分析', '產業分析'], Macro: ['總體經濟', '政策與地緣政治'], Passive: ['投資策略'] },
  goal:    { Growth: ['個股'], Dividend: ['ETF'], Passive: ['ETF'] },
  capital: { Low: ['ETF'], Medium: ['個股', 'ETF'], High: ['債券'] },
};

function _prefScore(summary, prefs) {
  if (!prefs) return 0;
  const tags = new Set(summary.tags || []);
  let score = 0;

  if (prefs.markets && prefs.markets.length > 0 && !prefs.markets.includes('General')) {
    const wanted = prefs.markets.flatMap(m => _PREF_TAG_MAP.markets[m] || []);
    if (wanted.some(t => tags.has(t))) score += 3;
  }
  if (prefs.style) {
    const wanted = _PREF_TAG_MAP.style[prefs.style] || [];
    if (wanted.some(t => tags.has(t))) score += 2;
  }
  if (prefs.goal) {
    const wanted = _PREF_TAG_MAP.goal[prefs.goal] || [];
    if (wanted.some(t => tags.has(t))) score += 2;
  }
  if (prefs.capital) {
    const wanted = _PREF_TAG_MAP.capital[prefs.capital] || [];
    if (wanted.some(t => tags.has(t))) score += 1;
  }
  return score;
}

// ── Data loading ──────────────────────────────────────────────
let _userPrefs = null;

async function loadDiscoverData() {
  try {
    const [summaryRes, prefRes] = await Promise.all([
      fetch('/api/summaries/?limit=100'),
      fetch('/api/accounts/preferences/'),
    ]);
    const all = await summaryRes.json();
    _userPrefs = prefRes.ok ? await prefRes.json() : null;

    _discoverCache = dedupeByEpisode(all);

    // sort by preference score if user has completed onboarding
    const hasPrefs = _userPrefs && Object.keys(_userPrefs).length > 0;
    const sorted = hasPrefs
      ? [..._discoverCache].sort((a, b) => _prefScore(b, _userPrefs) - _prefScore(a, _userPrefs))
      : _discoverCache;

    renderContinueListening(sorted[0] || null);
    renderRecommended(sorted.slice(0, 8));
  } catch(e) { console.error('loadDiscoverData failed', e); }
}

// ── Weekly Signals ─────────────────────────────────────────────
let currentSummaryId = null;

function toggleSignalDetail(chipBtn, asset, week, podcaster, event) {
  if (event) event.stopPropagation();
  const card = chipBtn.closest('.p-4');
  const detail = card.querySelector('.signal-detail');
  const key = `${asset}||${week}`;
  const allRecords = _signalRecordsByAssetWeek[key] || [];
  const seen = new Set();
  const records = allRecords.filter(r => {
    if (r.podcaster !== podcaster) return false;
    const k = (r.thesis || '').trim();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  const isActive = chipBtn.dataset.active === '1';
  card.querySelectorAll('.podcaster-chip').forEach(c => {
    c.dataset.active = '';
    c.classList.remove('bg-secondary', 'text-white', 'border-secondary');
    c.classList.add('bg-surface-container-low', 'text-on-surface-variant', 'border-outline-variant/30');
  });
  if (isActive) { detail.classList.add('hidden'); detail.innerHTML = ''; return; }

  chipBtn.dataset.active = '1';
  chipBtn.classList.remove('bg-surface-container-low', 'text-on-surface-variant', 'border-outline-variant/30');
  chipBtn.classList.add('bg-secondary', 'text-white', 'border-secondary');

  const resultCfg = {
    pass:    { label: '✓ 正確',   color: '#0f6e56', bg: '#e1f5ee' },
    fail:    { label: '✗ 錯誤',   color: '#993c1d', bg: '#faece7' },
    pending: { label: '⏳ 待驗證', color: '#717879', bg: '#f0eee5' },
  };
  const dirLabel = { bullish: '看多', bearish: '看空', neutral: '觀察' };
  const acc = (_accuracyCache || {})[podcaster];

  detail.innerHTML = records.map(r => {
    const cfg = resultCfg[r.result] || resultCfg.pending;
    const episodeName = (r.source_filename || '').replace(/\.srt$/i, '');
    const dir = dirLabel[r.direction] || '';
    return `
      <div class="bg-surface-container-lowest rounded-xl border border-outline-variant/20 p-4">
        <div class="flex justify-between items-center mb-2">
          <span class="text-[10px] font-bold px-2 py-0.5 rounded-full" style="background:${cfg.bg};color:${cfg.color}">${cfg.label}</span>
          <span class="text-[10px] font-semibold text-outline">${dir}</span>
        </div>
        <p class="text-sm font-bold text-tertiary-container font-['Epilogue'] leading-snug mb-2">${r.thesis || ''}</p>
        <div class="text-[11px] text-outline mb-0.5 truncate" title="${episodeName}">${episodeName}</div>
        ${acc ? `<div class="text-[11px] font-bold text-secondary mt-1">⚡ ${acc.pct}% 準確率</div>` : ''}
        <button onclick="openDeepDive(${r.summary_id}, false, ${r.id})" class="mt-3 w-full py-2 bg-on-primary-container text-white rounded-full font-label text-[11px] font-bold uppercase tracking-widest">Read Thesis</button>
      </div>`;
  }).join('') || '<p class="text-outline text-xs py-2">此 Podcaster 無驗證句</p>';
  detail.classList.remove('hidden');
}

function toggleSingleSource(btn) {
  const rest = btn.previousElementSibling;
  const chevron = btn.querySelector('.single-chevron');
  const label = btn.querySelector('.single-more-label');
  const isHidden = rest.classList.toggle('hidden');
  chevron.style.transform = isHidden ? '' : 'rotate(180deg)';
  if (label) {
    const count = rest.querySelectorAll(':scope > div').length;
    label.textContent = isHidden ? `顯示更多（+${count}）` : '收起';
  }
}

function setSignalDir(dir) {
  _signalDir = dir;
  ['bullish','bearish'].forEach(d => {
    const btn = document.getElementById(`sig-tab-${d}`);
    if (!btn) return;
    const active = d === dir;
    btn.className = `px-4 py-2 rounded-full text-xs font-bold ${active ? 'bg-tertiary-container text-white' : 'bg-surface-container text-outline'}`;
  });
  if (_signalData) renderSignalCards(_signalData);
}

function shiftSignalWeek(delta) {
  const next = _signalWeekIndex + delta;
  if (next < 0 || next >= _signalWeeks.length) return;
  _signalWeekIndex = next;
  if (_signalData) renderSignalCards(_signalData);
}

function renderSignalCards(records) {
  const grid = document.getElementById('insights-grid');
  const accMap = _accuracyCache || {};

  function toWeekMon(dateStr) {
    const d = new Date(dateStr);
    const day = d.getUTCDay();
    const diff = day === 0 ? -6 : 1 - day;
    const mon = new Date(d);
    mon.setUTCDate(d.getUTCDate() + diff);
    return mon.toISOString().slice(0, 10);
  }

  _signalRecordsByAssetWeek = {};
  for (const r of records) {
    if (!r.start_time) continue;
    const week = toWeekMon(r.start_time);
    const key = `${r.asset}||${week}`;
    if (!_signalRecordsByAssetWeek[key]) _signalRecordsByAssetWeek[key] = [];
    if (!_signalRecordsByAssetWeek[key].find(x => x.id === r.id))
      _signalRecordsByAssetWeek[key].push(r);
  }

  const weekMap = {};
  for (const r of records) {
    if (!r.start_time) continue;
    const week = toWeekMon(r.start_time);
    if (!weekMap[week]) weekMap[week] = {};
    const asset = r.asset || '';
    if (!weekMap[week][asset]) weekMap[week][asset] = { podcasters: {}, dir: r.direction, ticker: r.ticker };
    const p = r.podcaster || '未知';
    if (!weekMap[week][asset].podcasters[p]) weekMap[week][asset].podcasters[p] = 0;
    weekMap[week][asset].podcasters[p]++;
  }

  const weeks = Object.keys(weekMap).sort().reverse();
  if (!weeks.length) { grid.innerHTML = '<p class="text-outline text-sm">尚無資料</p>'; return; }

  _signalWeeks = weeks;
  _signalWeekIndex = Math.min(_signalWeekIndex, weeks.length - 1);

  const prevBtn = document.getElementById('sig-prev-week');
  const nextBtn = document.getElementById('sig-next-week');
  if (prevBtn) prevBtn.disabled = _signalWeekIndex >= weeks.length - 1;
  if (nextBtn) nextBtn.disabled = _signalWeekIndex <= 0;

  const latestWeek = weeks[_signalWeekIndex];
  const assets = weekMap[latestWeek];

  const weekSun = new Date(latestWeek);
  weekSun.setUTCDate(weekSun.getUTCDate() + 6);
  const weekLabel = `${latestWeek} ~ ${weekSun.toISOString().slice(0,10)}`;
  const el = document.getElementById('signals-week-label');
  if (el) el.textContent = weekLabel;

  const dirFilter = _signalDir;
  const filtered = Object.entries(assets).filter(([, d]) => d.dir === dirFilter);

  const allPodcasters = new Set();
  let consensusCount = 0;
  for (const [, d] of filtered) {
    Object.keys(d.podcasters).forEach(p => allPodcasters.add(p));
    if (Object.keys(d.podcasters).length >= 2) consensusCount++;
  }
  const s1 = document.getElementById('stat-total'); if (s1) s1.textContent = filtered.length;
  const s2 = document.getElementById('stat-consensus'); if (s2) s2.textContent = consensusCount;
  const s3 = document.getElementById('stat-podcasters'); if (s3) s3.textContent = allPodcasters.size;

  const sorted = filtered.sort((a, b) => Object.keys(b[1].podcasters).length - Object.keys(a[1].podcasters).length);
  const strongItems = sorted.filter(([, d]) => Object.keys(d.podcasters).length >= 3);
  const weakItems   = sorted.filter(([, d]) => Object.keys(d.podcasters).length === 2);
  const singleItems = sorted.filter(([, d]) => Object.keys(d.podcasters).length === 1);

  function renderSingleCard(asset, data) {
    const podcasters = Object.keys(data.podcasters);
    const p = podcasters[0];
    const pct = accMap[p]?.pct;
    const accBarColor = pct >= 70 ? '#286671' : pct >= 55 ? '#d97f12' : '#c1c8c9';
    const key = `${asset}||${latestWeek}`;
    const allRec = _signalRecordsByAssetWeek[key] || [];
    const seen = new Set();
    const recs = allRec.filter(r => {
      if (r.podcaster !== p) return false;
      const k = (r.thesis || '').trim(); if (seen.has(k)) return false; seen.add(k); return true;
    });
    const resultCfg = {
      pass:    { label: '✓ 正確',   color: '#0f6e56', bg: '#e1f5ee' },
      fail:    { label: '✗ 錯誤',   color: '#993c1d', bg: '#faece7' },
      pending: { label: '⏳ 待驗證', color: '#717879', bg: '#f0eee5' },
    };
    const thesisHtml = recs.map(r => {
      const cfg = resultCfg[r.result] || resultCfg.pending;
      const ep = (r.source_filename || '').replace(/\.srt$/i, '');
      return `<div class="bg-surface-container-lowest rounded-md border border-outline-variant/20 p-3 mt-2">
        <div class="flex justify-between items-center mb-1.5">
          <span class="text-[10px] font-bold px-2 py-0.5 rounded-full" style="background:${cfg.bg};color:${cfg.color}">${cfg.label}</span>
        </div>
        <p class="text-sm font-bold text-tertiary-container font-['Epilogue'] leading-snug mb-1.5">${r.thesis || ''}</p>
        <div class="text-[11px] text-outline truncate mb-2" title="${ep}">${ep}</div>
        <button onclick="openDeepDive(${r.summary_id}, false, ${r.id})" class="w-full py-1.5 bg-on-primary-container text-white rounded-full font-label text-[11px] font-bold uppercase tracking-widest">Read Thesis</button>
      </div>`;
    }).join('');
    return `
      <div class="bg-white rounded-2xl overflow-hidden" style="border-left:3px solid #c1c8c9;border-top:0.5px solid #e5e3d9;border-right:0.5px solid #e5e3d9;border-bottom:0.5px solid #e5e3d9;border-radius:0 1rem 1rem 0;">
        <div class="p-4">
          <div class="flex justify-between items-start mb-2">
            <div class="font-['Epilogue'] font-bold text-[17px] text-on-surface leading-tight">${renderAssetNameStar(data.ticker, asset)}</div>
            <span class="text-[11px] px-2.5 py-1 rounded-full bg-surface-container text-outline font-semibold">單一來源</span>
          </div>
          <div class="flex items-center gap-2 mb-3">
            <span class="text-[12px] font-bold text-on-surface-variant">${p}</span>
            ${pct != null ? `<span class="text-[11px] font-semibold text-secondary">⚡ ${pct}% 準確率</span>` : ''}
          </div>
          ${pct != null ? `<div class="h-1 bg-surface-container rounded-full mb-3"><div class="h-full rounded-full" style="width:${pct}%;background:${accBarColor};"></div></div>` : ''}
          ${thesisHtml}
        </div>
      </div>`;
  }

  function renderCard(asset, data) {
    const podcasters = Object.keys(data.podcasters);
    const count = podcasters.length;
    const isStrong = count >= 3;
    const borderColor = isStrong ? '#286671' : '#d97f12';
    const badgeClass = isStrong
      ? 'bg-secondary-container/40 text-secondary font-bold'
      : 'bg-[#fff3e0] text-[#854f0b] font-bold';
    const badgeLabel = isStrong ? '強共識' : '弱共識';
    const accs = podcasters.map(p => accMap[p]?.pct).filter(v => v != null);
    const avgAcc = accs.length ? Math.round(accs.reduce((a, b) => a + b, 0) / accs.length) : null;
    const accBarWidth = avgAcc != null ? avgAcc : 0;
    const accBarColor = avgAcc >= 70 ? '#286671' : avgAcc >= 55 ? '#d97f12' : '#c1c8c9';
    const assetKey = asset.replace(/'/g, "\\'");
    const chips = podcasters.map(p => {
      const pct = accMap[p]?.pct;
      return `<button onclick="toggleSignalDetail(this,'${assetKey}','${latestWeek}','${p.replace(/'/g,"\\'")}',event)"
        class="podcaster-chip text-left text-[11px] font-semibold px-2.5 py-1.5 rounded-xl border border-outline-variant/30 bg-surface-container-low text-on-surface-variant hover:bg-surface-container transition-colors" data-podcaster="${p}">
        <span class="font-bold">${p}</span>${pct != null ? `<span class="ml-1 opacity-70">${pct}%</span>` : ''}
      </button>`;
    }).join('');
    return `
      <div class="bg-white rounded-2xl overflow-hidden" style="border-left:3px solid ${borderColor};border-top:0.5px solid #e5e3d9;border-right:0.5px solid #e5e3d9;border-bottom:0.5px solid #e5e3d9;border-radius:0 1rem 1rem 0;">
        <div class="p-4">
          <div class="flex justify-between items-start mb-2">
            <div class="font-['Epilogue'] font-bold text-[17px] text-on-surface leading-tight">${renderAssetNameStar(data.ticker, asset)}</div>
            <span class="text-[11px] px-2.5 py-1 rounded-full ${badgeClass}">${badgeLabel}（${count}人）</span>
          </div>
          <div class="flex flex-wrap gap-1.5 mb-3">
            <span class="bg-surface-container text-secondary text-[11px] font-semibold px-2.5 py-1 rounded-full">${count} 人看多</span>
            ${avgAcc != null ? `<span class="bg-surface-container text-secondary text-[11px] font-semibold px-2.5 py-1 rounded-full">平均準確率 ${avgAcc}%</span>` : ''}
          </div>
          <div class="flex flex-wrap gap-1.5 mb-3">${chips}</div>
          <div class="signal-detail hidden mt-2 space-y-2"></div>
          ${avgAcc != null ? `
          <div class="mt-3">
            <div class="flex justify-between text-[10px] font-bold text-outline mb-1"><span>Podcaster 平均準確率</span><span>${avgAcc}%</span></div>
            <div class="h-1 bg-surface-container rounded-full"><div class="h-full rounded-full" style="width:${accBarWidth}%;background:${accBarColor};"></div></div>
          </div>` : ''}
        </div>
      </div>`;
  }

  let html = '';
  if (strongItems.length) {
    html += `<div class="text-[11px] font-bold text-outline uppercase tracking-widest mb-2 mt-1">強共識（3人以上）</div>`;
    html += strongItems.map(([a, d]) => renderCard(a, d)).join('');
  }
  if (weakItems.length) {
    html += `<div class="text-[11px] font-bold text-outline uppercase tracking-widest mb-2 mt-3">弱共識（2人）</div>`;
    html += weakItems.map(([a, d]) => renderCard(a, d)).join('');
  }
  if (singleItems.length) {
    const SHOW = 3;
    const visibleHtml = singleItems.slice(0, SHOW).map(([a, d]) => renderSingleCard(a, d)).join('');
    const restHtml    = singleItems.slice(SHOW).map(([a, d]) => renderSingleCard(a, d)).join('');
    const hasMore = singleItems.length > SHOW;
    const sectionLabel = `<div class="text-[11px] font-bold text-outline uppercase tracking-widest mb-2 ${strongItems.length || weakItems.length ? 'mt-3' : 'mt-1'}">單一來源</div>`;
    html += `
      <div class="mt-1">
        ${sectionLabel}
        <div class="space-y-3">${visibleHtml}</div>
        ${hasMore ? `
        <div class="single-source-rest hidden space-y-3 mt-3">${restHtml}</div>
        <button onclick="toggleSingleSource(this)" class="mt-3 w-full flex items-center justify-center gap-1.5 py-2.5 rounded-xl border border-outline-variant/30 text-xs font-bold text-outline hover:bg-surface-container transition-colors">
          <span class="single-more-label">顯示更多（+${singleItems.length - SHOW}）</span>
          <span class="material-symbols-outlined text-sm single-chevron" style="transition:transform 0.2s">expand_more</span>
        </button>` : ''}
      </div>`;
  }
  if (!html) html = `<p class="text-outline text-sm">本週暫無${dirFilter === 'bullish' ? '看多' : '看空'}訊號</p>`;
  grid.innerHTML = html;
}

async function loadWeeklySignals() {
  const grid = document.getElementById('insights-grid');
  try {
    const beforeDate = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    const res = await fetch(`/api/summaries/backtesting/?limit=600&before=${beforeDate}`);
    if (!res.ok) throw new Error('network');
    const records = await res.json();
    await _ensureAccuracyCache();
    await _ensureFavoritesCache();
    _signalData = records;
    renderSignalCards(records);
  } catch (e) {
    console.error('loadWeeklySignals failed', e);
    grid.innerHTML = '<p class="text-outline text-sm">載入失敗</p>';
  }
}

// ── Hot tags ──────────────────────────────────────────────────
let _activeTag = null;
const _tagActiveCls = 'px-5 py-2 bg-tertiary-container text-surface rounded-full font-label text-sm font-semibold whitespace-nowrap transition-colors';
const _tagNormalCls = 'px-5 py-2 bg-secondary/10 text-secondary rounded-full font-label text-sm font-semibold whitespace-nowrap border border-secondary/20 hover:bg-secondary/20 transition-colors';

function _updateTagButtons() {
  const container = document.getElementById('discover-tags');
  if (!container) return;
  container.querySelectorAll('button[data-tag]').forEach(btn => {
    const isActive = btn.dataset.tag === (_activeTag === null ? '__all__' : _activeTag);
    btn.className = isActive ? _tagActiveCls : _tagNormalCls;
  });
}

async function filterDiscoverByTag(tagName) {
  _activeTag = tagName;
  _updateTagButtons();

  const resultsSection     = document.getElementById('tag-results-section');
  const continueSec        = document.getElementById('continue-listening-section');
  const insightsSec        = document.getElementById('verified-insights-section');
  const recommendedSection = document.getElementById('recommended-section');

  if (!tagName) {
    resultsSection.classList.add('hidden');
    continueSec.classList.remove('hidden');
    insightsSec.classList.remove('hidden');
    recommendedSection.classList.remove('hidden');
    return;
  }

  resultsSection.classList.remove('hidden');
  continueSec.classList.add('hidden');
  insightsSec.classList.add('hidden');
  recommendedSection.classList.add('hidden');
  document.getElementById('tag-results-title').textContent = `「${tagName}」相關節目`;

  const list = document.getElementById('tag-results-list');
  list.innerHTML = '<p class="text-outline text-sm">載入中...</p>';

  let sources = null;
  try {
    const res = await fetch(`/api/knowledge-graph/node-episodes/?node=${encodeURIComponent(tagName)}&days=30`);
    if (res.ok) {
      const data = await res.json();
      sources = new Set(data.sources || []);
    }
  } catch (_) {}

  let filtered = [];
  if (sources && sources.size > 0) {
    try {
      const fetches = [...sources].map(name =>
        fetch(`/api/summaries/?source_filename=${encodeURIComponent(name)}&mode=${ddCurrentMode || 'pro'}`)
          .then(r => r.ok ? r.json() : [])
          .then(data => Array.isArray(data) ? data : [])
          .catch(() => [])
      );
      const results = await Promise.all(fetches);
      filtered = results.flat();
    } catch (_) {}
  }

  if (!filtered.length) {
    const kw = tagName.toLowerCase();
    filtered = _discoverCache.filter(s => {
      const ent = s.entities;
      const entityVals = (ent && typeof ent === 'object')
        ? Object.values(ent).flatMap(v => Array.isArray(v) ? v.map(String) : [String(v)])
        : [];
      const texts = [...(s.tags || []), s.one_sentence_summary || '', s.podcaster || '', ...entityVals];
      return texts.some(t => t.toLowerCase().includes(kw));
    });
  }

  if (!filtered.length) {
    list.innerHTML = '<p class="text-outline text-sm">找不到相關節目，試試其他標籤</p>';
    return;
  }
  list.innerHTML = filtered.map(s => {
    const st    = cardStyle(s.podcaster || s.source_filename);
    const title = (s.source_filename || '').replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 60) || '(未命名)';
    const tagStr = (s.tags || []).slice(0, 3).join(' · ');
    return `
      <div onclick="openDeepDive(${s.id})" class="flex gap-4 p-4 rounded-lg bg-surface-container-lowest hover:bg-surface-container-low transition-colors cursor-pointer border border-transparent hover:border-outline-variant/20">
        <div class="w-16 h-16 rounded-lg flex-shrink-0 flex items-center justify-center" style="background:${st.bg}">
          <span class="material-symbols-outlined text-white/60 text-2xl" style="font-variation-settings:'FILL' 1">${st.icon}</span>
        </div>
        <div class="flex flex-col justify-center min-w-0">
          <span class="text-[10px] font-bold text-secondary uppercase tracking-widest truncate">${s.podcaster || ''}</span>
          <h4 class="font-['Epilogue'] font-bold text-sm text-tertiary-container leading-snug line-clamp-2">${title}</h4>
          <p class="text-xs text-outline mt-0.5 truncate">${tagStr}</p>
        </div>
      </div>`;
  }).join('');
}

async function loadHotTags() {
  const container = document.getElementById('discover-tags');
  if (!container) return;
  const allBtn = `<button data-tag="__all__" onclick="filterDiscoverByTag(null)" class="${_tagActiveCls}">All</button>`;

  function _renderTagBtns(names) {
    const btns = names.map(n =>
      `<button data-tag="${n}" onclick="filterDiscoverByTag('${n.replace(/'/g, "\\'")}') " class="${_tagNormalCls}">${n}</button>`
    ).join('');
    container.innerHTML = allBtn + btns;
  }

  try {
    const res = await fetch('/api/knowledge-graph/hot-nodes/?days=30&limit=12');
    if (res.ok) {
      const data = await res.json();
      const nodes = data.nodes || [];
      if (nodes.length) {
        _hotTagNames = nodes.map(n => n.name);
        _renderTagBtns(_hotTagNames);
        return;
      }
    }
  } catch { /* fall through */ }

  container.innerHTML = allBtn;
}

// ── Search ────────────────────────────────────────────────────
let _searchTimer = null;
let _podcasterListCache = null;
let _searchSeq = 0;  // 過期回應防護：使用者打字夠快時，前一次查詢可能比這次晚回來，
                      // 不擋掉的話下拉選單會被過期結果蓋掉（跟 assets.js 的 _assetsFetchSeq 同一招）。

// 節目清單就 13 檔（見 rankings.js 的 podcasters/ API），快取起來避免每次打字都重打一次 API。
async function _ensurePodcasterList() {
  if (_podcasterListCache) return _podcasterListCache;
  try {
    const res = await fetch('/api/summaries/podcasters/?limit=200');
    _podcasterListCache = res.ok ? await res.json() : [];
  } catch (e) { _podcasterListCache = []; }
  return _podcasterListCache;
}

function onSearchInput() {
  const q  = (document.getElementById('discover-search')?.value || '').trim();
  const dd = document.getElementById('search-dropdown');
  clearTimeout(_searchTimer);
  if (!q) { dd.classList.add('hidden'); dd.innerHTML = ''; return; }
  _searchTimer = setTimeout(() => _runSearch(q), 300);
}

function onSearchKeydown(e) {
  if (e.key === 'Escape') {
    document.getElementById('search-dropdown').classList.add('hidden');
    document.getElementById('discover-search').value = '';
  }
}

function _closeSearch() {
  document.getElementById('search-dropdown').classList.add('hidden');
  document.getElementById('discover-search').value = '';
}

// 點下拉裡的「相關標的」列：跳到 ASSETS 分頁並直接開該標的詳情頁，
// 跟 assets.js 自己下拉選單點擊後的行為（openAssetDetailBySymbol）共用同一支函式。
// 故意不呼叫 _closeSearch()——保留搜尋框內容跟下拉選單，這樣從 Assets 詳情頁按返回
// （見 assets.js 的 closeAssetDetail）跳回本頁時，剛剛的搜尋結果還在，不會憑空消失。
function _goToAssetFromSearch(symbol, category, name) {
  _backOverride = 'discover';
  showPage('assets');
  openAssetDetailBySymbol(symbol, category, name);
}

// 標的比對：同時查台股個股／ETF 兩個分類，其中一個分類的資料來源掛掉時
// （例如 ETF 專用的 Supabase 連線異常）不要讓整個搜尋失敗，該分類回空陣列就好。
async function _fetchAssetMatches(q) {
  const fetches = [
    { category: 'tw_stock' },
    { category: 'tw_etf' },
  ].map(({ category }) =>
    fetch(`/api/assets/rankings/?category=${category}&q=${encodeURIComponent(q)}&sort=volume&direction=desc&limit=3`)
      .then(r => r.ok ? r.json() : null)
      .then(data => (data && Array.isArray(data.data) ? data.data : []).map(r => ({ ...r, category })))
      .catch(() => [])
  );
  const results = await Promise.all(fetches);
  return results.flat().slice(0, 5);
}

// 跟 assets.js 的 _runAssetSearch() 下拉選項用同一套排版/配色，讓使用者一眼認出
// 這是「標的」而不是「單集」（單集那排是另一種顏色的卡片，見 _episodeRowHtml）。
function _assetMatchRowHtml(r) {
  const name = r.name || r.symbol;
  const pct = r.change_pct;
  const isUp = pct != null && pct > 0;
  const isDown = pct != null && pct < 0;
  const pctColor = pct == null ? 'text-outline' : (isUp ? 'text-[#ba1a1a]' : isDown ? 'text-[#1e8e3e]' : 'text-outline');
  const pctLabel = pct == null ? '—' : `${isUp ? '▲' : isDown ? '▼' : ''} ${Math.abs(pct).toFixed(2)}%`;
  return `
    <div onclick="_goToAssetFromSearch('${r.symbol}', '${r.category}', '${name.replace(/'/g, "\\'")}')"
      class="flex items-center justify-between gap-3 px-4 py-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
      <div class="flex items-baseline gap-2 min-w-0">
        <span class="text-base font-bold text-tertiary-container truncate">${name}</span>
        <span class="text-sm text-outline flex-shrink-0">${r.symbol}</span>
      </div>
      <div class="flex items-baseline gap-2 flex-shrink-0">
        <span class="text-base font-bold text-on-surface">${r.close != null ? r.close.toFixed(2) : '—'}</span>
        <span class="text-sm font-semibold ${pctColor} whitespace-nowrap">${pctLabel}</span>
      </div>
    </div>`;
}

// 整個節目（不是單集）那一列：點下去開節目頁（跟 Rankings 點進節目一樣的頁面）。
// 故意不呼叫 _closeSearch()——節目頁的返回鍵本來就是走 goBack()／_pageHistory，
// 保留搜尋框內容跟下拉選單，按返回時才能看到剛剛的搜尋結果，不是空的。
function _showMatchRowHtml(podcaster) {
  const st = cardStyle(podcaster.podcaster);
  const safeName = podcaster.podcaster.replace(/'/g, "\\'");
  return `
    <div onclick="openRankedPodcasterByName('${safeName}')"
      class="flex items-center gap-3 p-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 transition-colors">
      <div class="w-12 h-12 rounded-full overflow-hidden flex-shrink-0">
        ${podcastAvatar(podcaster.podcaster, st.bg, st.icon)}
      </div>
      <div class="min-w-0 flex-1">
        <p class="text-[10px] font-bold text-secondary uppercase tracking-widest">節目</p>
        <p class="text-base font-bold text-tertiary-container leading-snug truncate">${podcaster.podcaster}</p>
        <p class="text-xs text-outline">共 ${podcaster.episodes} 集 · 查看全部集數</p>
      </div>
      <span class="material-symbols-outlined text-outline/40 text-base flex-shrink-0">chevron_right</span>
    </div>`;
}

// 搜尋結果快取，讓下面的列點下去時可以查回 match_terms（命中字串），
// 帶去 deep dive 頁面直接定位到對應段落——不直接把陣列塞進 inline onclick
// 屬性字串（JSON 裡有引號，跟 HTML 屬性的引號會互咬，容易寫壞）。
let _lastEpisodeResults = [];

function _openSearchEpisode(id) {
  const hit = _lastEpisodeResults.find(s => s.id === id);
  // 不呼叫 _closeSearch()：deep dive 的返回鍵是走 goBack()／_pageHistory，保留搜尋框
  // 內容跟下拉選單，按返回時才能看到剛剛的搜尋結果，不是空的。
  openDeepDive(id, false, null, (hit && hit.match_terms) || null);
}

function _episodeRowHtml(s) {
  const title  = (s.source_filename || '').replace(/\.srt$/i, '') || (s.one_sentence_summary || '').slice(0, 60) || '(未命名)';
  const tagStr = (s.tags || []).slice(0, 3).join(' · ');
  const st     = cardStyle(s.podcaster || s.source_filename || '');
  return `
    <div onclick="_openSearchEpisode(${s.id})"
      class="flex items-center gap-3 p-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
      <div class="w-10 h-10 rounded-lg overflow-hidden flex-shrink-0">
        ${podcastAvatar(s.podcaster, st.bg, st.icon)}
      </div>
      <div class="min-w-0 flex-1">
        <p class="text-[10px] font-bold text-secondary uppercase tracking-widest truncate">${s.podcaster || ''}</p>
        <p class="text-sm font-semibold text-tertiary-container leading-snug line-clamp-1">${title}</p>
        <p class="text-xs text-outline truncate">${tagStr}</p>
      </div>
      <span class="material-symbols-outlined text-outline/40 text-base flex-shrink-0">chevron_right</span>
    </div>`;
}

function _searchSectionLabel(text) {
  return `<div class="px-4 pt-3 pb-1 text-[10px] font-bold text-outline uppercase tracking-widest">${text}</div>`;
}

async function _runSearch(q) {
  const seq = ++_searchSeq;
  const dd = document.getElementById('search-dropdown');
  dd.innerHTML = '<p class="text-outline text-sm p-4">搜尋中...</p>';
  dd.classList.remove('hidden');

  try {
    const podcasters = await _ensurePodcasterList();
    if (seq !== _searchSeq) return; // 使用者在等待期間又打了新字，這次查詢已經過期

    const qLower = q.toLowerCase();
    // 搜尋字串太短（1 個字）容易誤判成節目名稱，只在夠長時才當作「找節目」處理。
    const showMatch = q.length >= 2
      ? podcasters.find(p => p.podcaster && p.podcaster.toLowerCase().includes(qLower))
      : null;

    const [assetMatches, episodeResults] = await Promise.all([
      _fetchAssetMatches(q),
      showMatch
        ? fetch(`/api/summaries/?podcaster=${encodeURIComponent(showMatch.podcaster)}&limit=50`)
            .then(r => r.ok ? r.json() : [])
            .then(list => dedupeByEpisode(list).slice(0, 5))
        : fetch(`/api/summaries/search/?q=${encodeURIComponent(q)}&limit=8`).then(r => r.ok ? r.json() : []),
    ]);
    if (seq !== _searchSeq) return; // 回應到的時候已經不是最新一次查詢了，結果不能再用

    if (!assetMatches.length && !showMatch && !episodeResults.length) {
      dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">找不到相關節目或公司</p>';
      return;
    }

    _lastEpisodeResults = episodeResults;

    let html = '';
    if (assetMatches.length) {
      html += _searchSectionLabel('相關標的');
      html += assetMatches.map(_assetMatchRowHtml).join('');
    }
    if (showMatch) {
      html += _searchSectionLabel('節目');
      html += _showMatchRowHtml(showMatch);
    }
    if (episodeResults.length) {
      html += _searchSectionLabel(showMatch ? '最新集數' : '相關集數');
      html += episodeResults.map(_episodeRowHtml).join('');
    }
    dd.innerHTML = html;
  } catch (e) {
    if (seq !== _searchSeq) return;
    dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">搜尋失敗，請稍後再試</p>';
  }
}

document.addEventListener('click', (e) => {
  const dd  = document.getElementById('search-dropdown');
  const inp = document.getElementById('discover-search');
  if (dd && inp && !dd.contains(e.target) && e.target !== inp) dd.classList.add('hidden');
});
