// ── Continue Listening ────────────────────────────────────────
let _discCurrentId = null;
let _hotTagNames = [];  // shared with AI question cards

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

// ── Verified Insights ─────────────────────────────────────────
let currentSummaryId = null;

async function loadVerifiedInsights() {
  const grid = document.getElementById('insights-grid');
  try {
    const res = await fetch('/api/summaries/backtesting/?limit=4');
    if (!res.ok) throw new Error('network');
    const records = await res.json();
    if (!records.length) { grid.innerHTML = '<p class="text-outline text-sm">尚無回測資料</p>'; return; }

    const accMap = await _ensureAccuracyCache();
    const dirLabel  = { bullish: '看多', bearish: '看空', neutral: '觀察' };
    const resultCfg = {
      pass:    { label: '✓ 正確',   border: 'border-green-500',        badge: 'bg-green-100 text-green-700' },
      fail:    { label: '✗ 錯誤',   border: 'border-error',            badge: 'bg-red-100 text-red-700' },
      pending: { label: '⏳ 待驗證', border: 'border-outline-variant', badge: 'bg-surface-container text-outline' },
    };

    // 後端已依「最新 + 待驗證優先」排序並每節目取一筆，直接使用
    grid.innerHTML = records.map(r => {
      const cfg = resultCfg[r.result] || resultCfg.pending;
      const episodeName = (r.source_filename || '').replace(/\.srt$/i, '') || '未知集數';
      const dir = dirLabel[r.direction || 'neutral'] || '觀察';
      const acc = accMap[r.podcaster];
      const accBadge = (acc && acc.total > 0)
        ? `<span class="text-[10px] font-bold text-secondary ml-3 flex-shrink-0 whitespace-nowrap">⚡ ${acc.pct}% 準確率</span>`
        : `<span class="text-[10px] font-bold text-outline ml-3 flex-shrink-0 whitespace-nowrap">尚無歷史紀錄</span>`;
      return `
        <div class="bg-surface-container-low p-6 rounded-lg border-l-4 ${cfg.border}">
          <div class="flex justify-between items-start mb-3">
            <div class="px-3 py-1 bg-surface-container-lowest rounded-full text-[10px] font-bold text-tertiary-container uppercase tracking-widest">${r.asset || ''}</div>
            <div class="flex gap-1.5 items-center">
              <span class="text-xs font-bold px-2 py-0.5 rounded-full bg-surface-container text-outline">${dir}</span>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${cfg.badge}">${cfg.label}</span>
            </div>
          </div>
          <h3 class="text-base font-bold text-tertiary-container mb-3 font-['Epilogue'] leading-snug">${r.thesis || ''}</h3>
          <div class="flex items-center justify-between mb-4">
            <div class="min-w-0">
              <p class="text-[11px] text-outline truncate">${episodeName}</p>
              <p class="text-sm text-on-surface-variant font-body mt-1.5">來自 <span class="font-semibold">${r.podcaster || '未知節目'}</span></p>
            </div>
            ${accBadge}
          </div>
          <button onclick="openDeepDive(${r.summary_id})" class="w-full py-2 bg-on-primary-container text-white rounded-full font-label text-xs font-bold uppercase tracking-widest">Read Thesis</button>
        </div>`;
    }).join('');
  } catch (e) {
    console.error('loadVerifiedInsights failed', e);
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

async function _runSearch(q) {
  const dd = document.getElementById('search-dropdown');
  dd.innerHTML = '<p class="text-outline text-sm p-4">搜尋中...</p>';
  dd.classList.remove('hidden');
  try {
    const res = await fetch(`/api/summaries/search/?q=${encodeURIComponent(q)}&limit=8`);
    if (!res.ok) throw new Error();
    const results = await res.json();
    if (!results.length) {
      dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">找不到相關節目或公司</p>';
      return;
    }
    dd.innerHTML = results.map(s => {
      const title  = (s.source_filename || '').replace(/\.srt$/i, '') || (s.one_sentence_summary || '').slice(0, 60) || '(未命名)';
      const tagStr = (s.tags || []).slice(0, 3).join(' · ');
      const st     = cardStyle(s.podcaster || s.source_filename || '');
      return `
        <div onclick="openDeepDive(${s.id}); _closeSearch();"
          class="flex items-center gap-3 p-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
          <div class="w-10 h-10 rounded-lg flex-shrink-0 flex items-center justify-center" style="background:${st.bg}">
            <span class="material-symbols-outlined text-white/60 text-lg" style="font-variation-settings:'FILL' 1">${st.icon}</span>
          </div>
          <div class="min-w-0 flex-1">
            <p class="text-[10px] font-bold text-secondary uppercase tracking-widest truncate">${s.podcaster || ''}</p>
            <p class="text-sm font-semibold text-tertiary-container leading-snug line-clamp-1">${title}</p>
            <p class="text-xs text-outline truncate">${tagStr}</p>
          </div>
          <span class="material-symbols-outlined text-outline/40 text-base flex-shrink-0">chevron_right</span>
        </div>`;
    }).join('');
  } catch (e) {
    dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">搜尋失敗，請稍後再試</p>';
  }
}

document.addEventListener('click', (e) => {
  const dd  = document.getElementById('search-dropdown');
  const inp = document.getElementById('discover-search');
  if (dd && inp && !dd.contains(e.target) && e.target !== inp) dd.classList.add('hidden');
});
