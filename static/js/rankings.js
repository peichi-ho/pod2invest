// ── Sector filter ─────────────────────────────────────────────
let _currentSector = null;

// 排名頁至少要累積這麼多筆已驗證的預測才參與排名，不然「2 筆全中 = 100%」會直接擠進前三。
// 只套用在排名頁：探索頁／節目頁的準確率標籤（app.js 的 _ensureAccuracyCache）仍顯示完整資料。
const MIN_RANK_SAMPLE = 10;
const _RANK_SUBTITLE_SUFFIX = `依準確率排名（至少 ${MIN_RANK_SAMPLE} 筆驗證）`;

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

// 搜尋、單集頁、Profile 等「不是從排名卡片點進來」的入口：就算排名頁還停在某個產業篩選，
// 也不帶篩選進節目頁（第二個參數 false），節目頁看到的是完整的集數與觀點。
function openRankedPodcasterByName(name) {
  const idx = _rankData.findIndex(p => p.podcaster === name);
  if (idx !== -1) {
    openRankedPodcaster(idx, false);
  } else {
    showPodcaster(name, '--', '--', _rankColors[0], _rankIcons[0]);
  }
}

// 從排名卡片點進節目頁。排名頁目前有套用產業篩選時，把篩選條件（跟這個節目在該產業的
// 準確率／樣本數）一起帶進去，節目頁才能只顯示該產業底下提到的觀點，而不是跟沒篩選時一樣。
// 其他入口（搜尋、單集頁、Profile）走 openRankedPodcasterByName()，不會帶篩選。
function openRankedPodcaster(idx, carrySectorFilter = true) {
  const p = _rankData[idx];
  if (!p) return;
  const sectorFilter = (carrySectorFilter && _currentSector)
    ? { sector: _currentSector, accuracy: p.accuracy, total: p.total }
    : null;
  showPodcaster(
    p.podcaster,
    p.episodes !== '--' ? p.episodes + ' 集' : '--',
    '--',
    _rankColors[idx % _rankColors.length],
    _rankIcons[idx % _rankIcons.length],
    sectorFilter
  );
}

// 整張排名卡片都可以點進節目頁；卡片裡的收藏按鈕自己有 stopPropagation，不會連帶觸發。
// 鍵盤操作：只在卡片本身取得焦點時才處理 Enter／空白鍵，避免卡片內的收藏按鈕按 Enter 時
// 事件冒泡上來又開一次節目頁。
function _rankCardKey(event, idx) {
  if (event.target !== event.currentTarget) return;
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    openRankedPodcaster(idx);
  }
}

// 「N 筆 · M 集」：樣本數（筆）放在最前面，使用者先看到這個準確率是建立在多少筆已驗證的
// 觀點上，再看節目總集數。還沒有任何驗證紀錄（accuracy 為 null）時沒有「筆」可以顯示。
function _rankSampleEpisodesText(p) {
  const parts = [];
  if (p.accuracy !== null && p.accuracy !== undefined) parts.push(`${p.total} 筆`);
  if (p.episodes !== '--') parts.push(`${p.episodes} 集`);
  return parts.join(' · ') || '--';
}

function _rankAccuracyText(p) {
  return (p.accuracy !== null && p.accuracy !== undefined) ? `準確率 ${p.accuracy}%` : '準確率 待驗證';
}

// 卡片右下角的「更多 →」：只是視覺提示（整張卡片本身就可以點），所以用 span 不用 button，
// 不然按鈕自己的 onclick 加上冒泡到卡片的 onclick 會連續開兩次節目頁。
function _rankMoreHint(cls) {
  return `<span class="inline-flex items-center gap-1 whitespace-nowrap ${cls}">更多<span class="material-symbols-outlined text-[1.25em] leading-none">arrow_forward</span></span>`;
}

async function loadRankings() {
  const podium   = document.getElementById('rankings-podium');
  const fullList = document.getElementById('rankings-full-list');
  if (!podium || !fullList) return;

  // 放在這裡而不是 applySectorFilter：第一次進頁面（沒按過篩選）也要看到門檻說明。
  const subtitleEl = document.getElementById('rankings-subtitle');
  if (subtitleEl) subtitleEl.textContent = `${_currentSector || '全部節目'} · ${_RANK_SUBTITLE_SUFFIX}`;

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
      _ensurePodcastFavoritesCache(),
    ]);

    const podData = podRes.ok ? await podRes.json() : [];
    const accData = accRes.ok ? await accRes.json() : [];

    const epMap = {};
    for (const p of podData) epMap[p.podcaster] = p.episodes;

    let data;
    if (_currentSector) {
      data = accData
        .filter(a => a.total >= MIN_RANK_SAMPLE)
        .map(a => ({ podcaster: a.podcaster, episodes: epMap[a.podcaster] ?? '--', accuracy: a.accuracy, total: a.total }));
    } else {
      // 樣本不足的節目不進準確率排名，跟「還沒有驗證紀錄」的節目一樣排在後面、顯示「準確率 待驗證」。
      const accMap = {};
      for (const a of accData) if (a.total >= MIN_RANK_SAMPLE) accMap[a.podcaster] = a;
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

    if (!data.length) {
      // 篩選後空白有兩種原因，要講清楚：完全沒資料，或有資料但沒有任何節目累積到門檻。
      const emptyMsg = !_currentSector ? '暫無節目資料'
        : accData.length ? `此類別目前沒有節目累積到 ${MIN_RANK_SAMPLE} 筆驗證，暫無排名`
        : '此類別暫無回測資料';
      // 套用篩選時前三名區塊是隱藏的（見上面 podiumSection.classList.add('hidden')），訊息寫進去
      // 使用者看不到、只會看到一片空白，所以篩選模式改寫進一定看得到的清單區。
      if (_currentSector) {
        fullList.innerHTML = `<div class="text-outline text-sm text-center py-12">${emptyMsg}</div>`;
      } else {
        podium.innerHTML   = `<div class="col-span-3 text-outline text-sm text-center py-12">${emptyMsg}</div>`;
        fullList.innerHTML = '';
      }
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
      const sampleEp = _rankSampleEpisodesText(p);
      const accText  = _rankAccuracyText(p);

      if (isFirst) {
        return `
          <div role="button" tabindex="0" onclick="openRankedPodcaster(${origIdx})" onkeydown="_rankCardKey(event, ${origIdx})"
            class="flex flex-col items-center gap-4 pb-5 pt-8 bg-tertiary-container rounded-lg shadow-xl relative cursor-pointer hover:opacity-95 transition-opacity">
            <div class="absolute -top-3 left-1/2 -translate-x-1/2">
              <span class="material-symbols-outlined text-on-primary-container text-2xl" style="font-variation-settings:'FILL' 1">emoji_events</span>
            </div>
            <div class="w-16 h-16 rounded-full overflow-hidden ring-2 ring-on-primary-container">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div class="text-center px-2">
              <div class="font-['Epilogue'] font-black text-6xl text-white/20 mb-1">${rankStr}</div>
              <h4 class="font-['Epilogue'] font-bold text-[28px] text-white leading-tight w-full truncate">${p.podcaster}</h4>
              <p class="text-base text-on-tertiary-container mt-2.5">${sampleEp}</p>
              <p class="text-lg font-bold text-on-tertiary-container/80 mt-1">${accText}</p>
            </div>
            <div class="flex items-center gap-1.5">
              ${_rankMoreHint('px-4 py-1.5 bg-on-primary-container text-white rounded-full font-label text-sm font-bold tracking-widest')}
              ${renderPodcastFollowButton(p.podcaster, 'w-8 h-8')}
            </div>
          </div>`;
      } else {
        return `
          <div role="button" tabindex="0" onclick="openRankedPodcaster(${origIdx})" onkeydown="_rankCardKey(event, ${origIdx})"
            class="flex flex-col items-center gap-4 pb-5 pt-6 bg-surface-container-low rounded-lg border border-outline-variant/10 cursor-pointer hover:bg-surface-container-highest transition-colors">
            <div class="w-14 h-14 rounded-full overflow-hidden">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div class="text-center px-2">
              <div class="font-['Epilogue'] font-black text-6xl text-outline/20 mb-1">${rankStr}</div>
              <h4 class="font-['Epilogue'] font-bold text-[28px] text-tertiary-container leading-tight w-full truncate">${p.podcaster}</h4>
              <p class="text-base text-outline mt-2.5">${sampleEp}</p>
              <p class="text-lg font-bold text-secondary mt-1">${accText}</p>
            </div>
            <div class="flex items-center gap-1.5">
              ${_rankMoreHint('px-4 py-1.5 border border-secondary text-secondary rounded-full font-label text-sm font-bold tracking-widest')}
              ${renderPodcastFollowButton(p.podcaster, 'w-8 h-8')}
            </div>
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
      return `
        <div role="button" tabindex="0" onclick="openRankedPodcaster(${dataIdx})" onkeydown="_rankCardKey(event, ${dataIdx})"
          class="flex items-center justify-between p-4 bg-surface-container-low rounded-lg hover:bg-surface-container-highest transition-all cursor-pointer">
          <div class="flex items-center gap-4 min-w-0">
            <span class="text-5xl font-black text-outline/30 italic w-16 flex-shrink-0">${rankStr}</span>
            <div class="w-12 h-12 rounded-full overflow-hidden flex-shrink-0">
              ${podcastAvatar(p.podcaster, bg, icon)}
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-1.5 min-w-0">
                <h4 class="font-bold text-tertiary-container text-xl truncate">${p.podcaster}</h4>
                ${renderPodcastFollowButton(p.podcaster, 'w-8 h-8')}
              </div>
              <p class="text-base text-outline font-medium">${_rankSampleEpisodesText(p)} · ${_rankAccuracyText(p)}</p>
            </div>
          </div>
          <div class="flex items-center flex-shrink-0">
            ${_rankMoreHint('px-5 py-2 border border-secondary text-secondary rounded-full font-label text-base font-bold tracking-widest')}
          </div>
        </div>`;
    }).join('') || '<p class="text-outline text-sm">暫無更多資料</p>';

  } catch (e) {
    podium.innerHTML   = '<div class="col-span-3 text-outline text-sm text-center py-12">載入失敗</div>';
    fullList.innerHTML = '';
  }
}

// ── Podcaster page ────────────────────────────────────────────
// sectorFilter：從 Rankings 套用產業篩選後點進來時才有值，格式 { sector, accuracy, total }
// （accuracy／total 是這個節目在該產業的準確率與樣本數，跟排名頁上顯示的是同一組數字）。
// 有值的話「所有觀點」只列出該產業底下提到的觀點，並且預設直接停在這個分頁；沒值（搜尋、
// 單集頁、Profile 等其他入口）維持原本行為：全部觀點、預設停在「所有集數」。
async function showPodcaster(name, episodeCount, accuracy, bgColor, icon, sectorFilter = null) {
  document.getElementById('podcaster-name').textContent = name;
  document.getElementById('podcaster-episode-count').textContent = episodeCount;
  document.getElementById('podcaster-accuracy').textContent = accuracy;
  // 用真正的節目封面圖（有的話），沒有才退回背景色+icon——不要固定只顯示 icon。
  document.getElementById('podcaster-avatar-inner').innerHTML = podcastAvatar(name, bgColor, icon);
  _ensurePodcastFavoritesCache().then(() => {
    document.getElementById('podcaster-favorite-slot').innerHTML = renderPodcastFollowButton(name, 'w-9 h-9');
  });

  const sectorChip = document.getElementById('podcaster-sector-chip');
  sectorChip.classList.toggle('hidden', !sectorFilter);
  if (sectorFilter) document.getElementById('podcaster-sector-label').textContent = `產業篩選：${sectorFilter.sector}`;

  const epList   = document.getElementById('podcaster-episodes-list');
  const viewList = document.getElementById('podcaster-views-list');
  epList.innerHTML   = '<p class="text-outline text-sm py-6 text-center">載入中...</p>';
  viewList.innerHTML = '<p class="text-outline text-sm py-6 text-center">載入中...</p>';

  switchPodcasterTab(sectorFilter ? 'views' : 'episodes');
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

    const sectorParam = sectorFilter ? `&sector=${encodeURIComponent(sectorFilter.sector)}` : '';
    const btRes = await fetch(`/api/summaries/backtesting/?podcaster=${encodeURIComponent(name)}${sectorParam}`);
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
    if (sectorFilter) {
      // 套用產業篩選時顯示該產業的準確率，不是節目整體的，才跟排名頁上看到的數字一致
      if (sectorFilter.accuracy != null && sectorFilter.total > 0) accEl.textContent = `${sectorFilter.sector} 準確率 ${sectorFilter.accuracy}%・${sectorFilter.total} 筆驗證`;
      else accEl.textContent = `${sectorFilter.sector} 尚無驗證觀點`;
    } else {
      const acc = accMap[name];
      if (acc && acc.total > 0) accEl.textContent = `平均準確率 ${acc.pct}%・${acc.total} 筆驗證`;
      else accEl.textContent = '尚無驗證觀點';
    }

    const groupConfig = {
      pass:    { label: '正確',   icon: 'check_circle', colorClass: 'text-green-600', border: 'border-green-500',        bg: 'bg-green-50/50' },
      fail:    { label: '錯誤',   icon: 'cancel',       colorClass: 'text-error',     border: 'border-error',            bg: 'bg-error-container/10' },
      pending: { label: '待驗證', icon: 'pending',      colorClass: 'text-outline',   border: 'border-outline-variant',  bg: 'bg-surface-container/50' },
    };
    const dirLabel = { bullish: '看多', bearish: '看空', neutral: '觀察' };

    if (sectorFilter && !seenTheses.size) {
      viewList.innerHTML = `<p class="text-outline text-sm py-6 text-center">此節目在「${escapeHtml(sectorFilter.sector)}」類別下沒有提及的觀點</p>`;
      return;
    }

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
