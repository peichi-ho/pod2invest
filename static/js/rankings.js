// ── Sector filter ─────────────────────────────────────────────
let _currentSector = null;

function toggleRankingsFilter() {
  const dd      = document.getElementById('rankings-filter-dropdown');
  const chevron = document.getElementById('rankings-filter-chevron');
  const isHidden = dd.classList.toggle('hidden');
  chevron.textContent = isHidden ? 'expand_more' : 'expand_less';
}

function _closeRankingsFilter() {
  const dd      = document.getElementById('rankings-filter-dropdown');
  const chevron = document.getElementById('rankings-filter-chevron');
  if (dd) dd.classList.add('hidden');
  if (chevron) chevron.textContent = 'expand_more';
}

function applySectorFilter(sector) {
  _currentSector = sector || null;
  _closeRankingsFilter();
  document.getElementById('rankings-filter-label').textContent = sector || '全部';
  document.getElementById('rankings-subtitle').textContent =
    sector ? `${sector} · 依準確率排名` : '全部節目 · 依準確率排名';
  document.querySelectorAll('.sector-btn').forEach(btn => {
    const isAll   = btn.id === 'sector-btn-all';
    const active  = (!sector && isAll);
    btn.classList.toggle('bg-on-primary-container', active);
    btn.classList.toggle('text-white', active);
    btn.classList.toggle('bg-surface-container-high', !active && !isAll);
    btn.classList.toggle('text-tertiary-container', !active);
  });
  loadRankings();
}

document.addEventListener('click', (e) => {
  const container = document.getElementById('rankings-filter-container');
  if (container && !container.contains(e.target)) _closeRankingsFilter();
});

// ── Rankings ──────────────────────────────────────────────────
const _rankColors = ['#d97f12','#286671','#1c1c16','#472500','#2c4c50','#113236','#3a4c2c','#6b2c6b','#1a3a5c','#4c3a1c'];
const _rankIcons  = ['workspace_premium','podcasts','show_chart','account_balance','public','ssid_chart','psychology','monitoring','bar_chart','mic'];
let _rankData = [];

function openRankedPodcasterByName(name) {
  const idx = _rankData.findIndex(p => p.podcaster === name);
  if (idx !== -1) {
    openRankedPodcaster(idx);
  } else {
    showPodcaster(name, '--', '--', _rankColors[0], _rankIcons[0]);
  }
}

function openRankedPodcaster(idx) {
  const p = _rankData[idx];
  if (!p) return;
  showPodcaster(p.podcaster, p.episodes + ' 集', '--', _rankColors[idx % _rankColors.length], _rankIcons[idx % _rankIcons.length]);
}

async function loadRankings() {
  const podium   = document.getElementById('rankings-podium');
  const fullList = document.getElementById('rankings-full-list');
  if (!podium || !fullList) return;

  const podiumSection = podium.closest('section');
  if (_currentSector) {
    if (podiumSection) podiumSection.classList.add('hidden');
    podium.innerHTML = '';
  } else {
    if (podiumSection) podiumSection.classList.remove('hidden');
    podium.innerHTML = '<div class="col-span-3 flex items-center justify-center text-outline text-sm py-12">載入中...</div>';
  }
  fullList.innerHTML = '';

  try {
    const sectorParam = _currentSector ? `?sector=${encodeURIComponent(_currentSector)}` : '';
    // 跟 loadPodcastImages() 一起等，不然 podcastAvatar() 畫的時候 _podcastImages
    // 可能還沒回來（兩支都是開機時平行打的），頭貼會先顯示成預設圖示、之後不會重畫。
    const [podRes, accRes] = await Promise.all([
      fetch('/api/summaries/podcasters/?limit=50'),
      fetch(`/api/summaries/accuracy-ranking/${sectorParam}`),
      loadPodcastImages(),
    ]);

    const podData = podRes.ok ? await podRes.json() : [];
    const accData = accRes.ok ? await accRes.json() : [];

    const epMap = {};
    for (const p of podData) epMap[p.podcaster] = p.episodes;

    let data;
    if (_currentSector) {
      data = accData.map(a => ({ podcaster: a.podcaster, episodes: epMap[a.podcaster] ?? '--', accuracy: a.accuracy, total: a.total }));
    } else {
      const accMap = {};
      for (const a of accData) accMap[a.podcaster] = a;
      const withAcc    = podData.filter(p => accMap[p.podcaster]);
      const withoutAcc = podData.filter(p => !accMap[p.podcaster]);
      withAcc.sort((a, b) => {
        const diff = accMap[b.podcaster].accuracy - accMap[a.podcaster].accuracy;
        return diff !== 0 ? diff : b.episodes - a.episodes;
      });
      data = [
        ...withAcc.map(p => ({ podcaster: p.podcaster, episodes: p.episodes, accuracy: accMap[p.podcaster].accuracy, total: accMap[p.podcaster].total })),
        ...withoutAcc.map(p => ({ podcaster: p.podcaster, episodes: p.episodes, accuracy: null, total: 0 })),
      ];
    }

    _rankData = data;
    const _accDisp = (p) => (p.accuracy !== null && p.accuracy !== undefined) ? `${p.accuracy}%・${p.total} 筆` : '待驗證';

    if (!data.length) {
      podium.innerHTML   = `<div class="col-span-3 text-outline text-sm text-center py-12">${_currentSector ? '此類別暫無回測資料' : '暫無節目資料'}</div>`;
      fullList.innerHTML = '';
      return;
    }

    const top3 = data.slice(0, 3);
    const displayOrder = top3.length >= 3 ? [1, 0, 2] : top3.length === 2 ? [null, 0, 1] : [null, 0, null];

    podium.innerHTML = displayOrder.map(origIdx => {
      if (origIdx === null) return '<div></div>';
      const p       = top3[origIdx];
      const rank    = origIdx + 1;
      const rankStr = String(rank).padStart(2, '0');
      const bg      = _rankColors[origIdx % _rankColors.length];
      const icon    = _rankIcons[origIdx  % _rankIcons.length];
      const isFirst = rank === 1;
      const accDisp = _accDisp(p);
      const epStr   = p.episodes !== '--' ? `${p.episodes} 集` : '--';

      if (isFirst) {
        return `
          <div class="flex flex-col items-center gap-3 pb-4 pt-8 bg-tertiary-container rounded-lg shadow-xl relative">
            <div class="absolute -top-3 left-1/2 -translate-x-1/2">
              <span class="material-symbols-outlined text-on-primary-container text-2xl" style="font-variation-settings:'FILL' 1">emoji_events</span>
            </div>
            <div class="w-16 h-16 rounded-full overflow-hidden ring-2 ring-on-primary-container">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div class="text-center px-2">
              <div class="font-['Epilogue'] font-black text-6xl text-white/20 mb-1">${rankStr}</div>
              <h4 class="font-['Epilogue'] font-bold text-[28px] text-white leading-tight w-full truncate">${p.podcaster}</h4>
              <p class="text-xl text-on-tertiary-container mt-1">${epStr}</p>
              <p class="text-xl font-bold text-on-tertiary-container/80 mt-0.5">準確率 ${accDisp}</p>
            </div>
            <button onclick="openRankedPodcaster(${origIdx})" class="px-4 py-1.5 bg-on-primary-container text-white rounded-full font-label text-xl font-bold uppercase tracking-widest hover:opacity-90 transition-all">More</button>
          </div>`;
      } else {
        return `
          <div class="flex flex-col items-center gap-3 pb-4 pt-6 bg-surface-container-low rounded-lg border border-outline-variant/10">
            <div class="w-14 h-14 rounded-full overflow-hidden">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div class="text-center px-2">
              <div class="font-['Epilogue'] font-black text-6xl text-outline/20 mb-1">${rankStr}</div>
              <h4 class="font-['Epilogue'] font-bold text-[28px] text-tertiary-container leading-tight w-full truncate">${p.podcaster}</h4>
              <p class="text-xl text-outline mt-1">${epStr}</p>
              <p class="text-xl font-bold text-secondary mt-0.5">準確率 ${accDisp}</p>
            </div>
            <button onclick="openRankedPodcaster(${origIdx})" class="px-4 py-1.5 border border-secondary text-secondary rounded-full font-label text-xl font-bold uppercase tracking-widest hover:bg-secondary hover:text-white transition-all">More</button>
          </div>`;
      }
    }).join('');

    const listStart = _currentSector ? 0 : 3;
    const rest = data.slice(listStart);
    fullList.innerHTML = rest.map((p, i) => {
      const rank    = i + listStart + 1;
      const dataIdx = i + listStart;
      const rankStr = String(rank).padStart(2, '0');
      const bg      = _rankColors[dataIdx % _rankColors.length];
      const icon    = _rankIcons[dataIdx  % _rankIcons.length];
      const accDisp = _accDisp(p);
      const epStr   = p.episodes !== '--' ? `${p.episodes} 集` : '--';
      return `
        <div class="flex items-center justify-between p-4 bg-surface-container-low rounded-lg hover:bg-surface-container-highest transition-all">
          <div class="flex items-center gap-4">
            <span class="text-5xl font-black text-outline/30 italic w-16">${rankStr}</span>
            <div class="w-12 h-12 rounded-full overflow-hidden">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div>
              <h4 class="font-bold text-tertiary-container text-xl">${p.podcaster}</h4>
              <p class="text-base text-outline font-medium">${epStr} · 準確率 ${accDisp}</p>
            </div>
          </div>
          <button onclick="openRankedPodcaster(${dataIdx})" class="px-5 py-2 border border-secondary text-secondary rounded-full font-label text-base font-bold uppercase tracking-widest hover:bg-secondary hover:text-white transition-all">More</button>
        </div>`;
    }).join('') || '<p class="text-outline text-sm">暫無更多資料</p>';

  } catch (e) {
    podium.innerHTML   = '<div class="col-span-3 text-outline text-sm text-center py-12">載入失敗</div>';
    fullList.innerHTML = '';
  }
}

// ── Podcaster page ────────────────────────────────────────────
async function showPodcaster(name, episodeCount, accuracy, bgColor, icon) {
  document.getElementById('podcaster-name').textContent = name;
  document.getElementById('podcaster-episode-count').textContent = episodeCount;
  document.getElementById('podcaster-accuracy').textContent = accuracy;
  // 用真正的節目封面圖（有的話），沒有才退回背景色+icon——不要固定只顯示 icon。
  document.getElementById('podcaster-avatar-inner').innerHTML = podcastAvatar(name, bgColor, icon);

  const epList   = document.getElementById('podcaster-episodes-list');
  const viewList = document.getElementById('podcaster-views-list');
  epList.innerHTML   = '<p class="text-outline text-sm py-6 text-center">載入中...</p>';
  viewList.innerHTML = '<p class="text-outline text-sm py-6 text-center">載入中...</p>';

  switchPodcasterTab('episodes');
  showPage('podcaster');

  try {
    const res = await fetch(`/api/summaries/?podcaster=${encodeURIComponent(name)}&limit=200`);
    if (!res.ok) throw new Error('network');
    const summaries = await res.json();

    const seenFiles = new Set();
    const episodes  = [];
    for (const s of summaries) {
      const key = s.source_filename || String(s.id);
      if (!seenFiles.has(key)) { seenFiles.add(key); episodes.push(s); }
    }

    epList.innerHTML = episodes.length
      ? episodes.map(ep => {
          const title = (ep.source_filename || '').replace(/\.srt$/i, '') || ep.one_sentence_summary?.slice(0, 60) || '(未命名)';
          const date  = (ep.published_at || ep.created_at || '').slice(0, 10);
          return `
            <div onclick="openDeepDive(${ep.id})" class="flex items-center justify-between p-4 bg-surface-container-low rounded-lg hover:bg-surface-container-highest transition-all cursor-pointer">
              <div class="flex items-center gap-4">
                <div class="w-10 h-10 rounded-lg overflow-hidden flex-shrink-0">
                  ${podcastAvatar(name, bgColor, icon)}
                </div>
                <div>
                  <h4 class="font-bold text-tertiary-container text-sm leading-snug line-clamp-2">${title}</h4>
                  <p class="text-xs text-outline mt-0.5">${date}</p>
                </div>
              </div>
              <span class="material-symbols-outlined text-outline/50 flex-shrink-0">chevron_right</span>
            </div>`;
        }).join('')
      : '<p class="text-outline text-sm py-6 text-center">暫無集數資料</p>';

    const btRes = await fetch(`/api/summaries/backtesting/?podcaster=${encodeURIComponent(name)}`);
    const btRecords = btRes.ok ? await btRes.json() : [];

    const groups = { pass: [], fail: [], pending: [] };
    const seenTheses = new Set();
    for (const r of btRecords) {
      const thesis = r.thesis || '';
      if (!thesis || seenTheses.has(thesis)) continue;
      seenTheses.add(thesis);
      const key = r.result === 'pass' ? 'pass' : r.result === 'fail' ? 'fail' : 'pending';
      groups[key].push(r);
    }

    const accMap = await _ensureAccuracyCache();
    await _ensureFavoritesCache();
    const accEl  = document.getElementById('podcaster-accuracy');
    const acc    = accMap[name];
    if (acc && acc.total > 0) accEl.textContent = `平均準確率 ${acc.pct}%・${acc.total} 筆驗證`;
    else accEl.textContent = '尚無驗證觀點';

    const groupConfig = {
      pass:    { label: '正確',   icon: 'check_circle', colorClass: 'text-green-600', border: 'border-green-500',        bg: 'bg-green-50/50' },
      fail:    { label: '錯誤',   icon: 'cancel',       colorClass: 'text-error',     border: 'border-error',            bg: 'bg-error-container/10' },
      pending: { label: '待驗證', icon: 'pending',      colorClass: 'text-outline',   border: 'border-outline-variant',  bg: 'bg-surface-container/50' },
    };
    const dirLabel = { bullish: '看多', bearish: '看空', neutral: '觀察' };

    viewList.innerHTML = Object.entries(groups).map(([key, items]) => {
      const cfg = groupConfig[key];
      return `
        <div class="mb-6">
          <div class="flex items-center gap-2 mb-3">
            <span class="material-symbols-outlined text-lg ${cfg.colorClass}" style="font-variation-settings:'FILL' 1">${cfg.icon}</span>
            <h4 class="font-['Epilogue'] font-bold text-base text-tertiary-container">${cfg.label}</h4>
            <span class="text-xs text-outline font-medium">(${items.length})</span>
          </div>
          ${items.length === 0
            ? '<p class="text-xs text-outline pl-7">尚無資料</p>'
            : items.map(v => `
                <div class="p-3 mb-2 rounded-lg border-l-4 ${cfg.border} ${cfg.bg} cursor-pointer hover:opacity-80 transition-opacity"
                     onclick="openDeepDive(${v.summary_id}, false, ${v.id})">
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-[10px] font-bold text-outline uppercase tracking-wider">${renderAssetNameStar(v.ticker, v.asset || '')}${v.direction ? ' · ' + (dirLabel[v.direction] || v.direction) : ''}</span>
                    <span class="text-[10px] text-outline">${v.end_time ? '截止 ' + v.end_time : ''}</span>
                  </div>
                  <p class="text-sm font-medium text-on-surface">${v.thesis || ''}</p>
                </div>`).join('')}
        </div>`;
    }).join('');

  } catch (e) {
    epList.innerHTML   = '<p class="text-outline text-sm py-6 text-center text-red-400">載入失敗，請稍後再試</p>';
    viewList.innerHTML = '<p class="text-outline text-sm py-6 text-center text-red-400">載入失敗，請稍後再試</p>';
  }
}

function switchPodcasterTab(tab) {
  document.getElementById('tab-episodes').classList.toggle('hidden', tab !== 'episodes');
  document.getElementById('tab-views').classList.toggle('hidden', tab !== 'views');
  const on  = 'px-6 py-2.5 bg-tertiary-container text-white rounded-full font-label text-sm font-bold tracking-wide shadow-md transition-all';
  const off = 'px-6 py-2.5 bg-surface-container-low text-secondary rounded-full font-label text-sm font-bold tracking-wide border border-secondary/30 transition-all';
  document.getElementById('btn-all-episodes').className = tab === 'episodes' ? on : off;
  document.getElementById('btn-all-views').className    = tab === 'views'    ? on : off;
}
