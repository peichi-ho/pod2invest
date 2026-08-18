// ── Deep Dive state ───────────────────────────────────────────
let ddSummaryData = {};
let ddCurrentMode = 'novice';
let _ddAutoPlay   = false;
let _ddTargetBacktestId = null;
let _ddTargetArgTerms   = null;  // 從搜尋結果點進來時帶的命中字串，見 renderDdModeContent()

function openDeepDive(summaryId, autoPlay = false, targetBacktestId = null, targetArgTerms = null) {
  currentSummaryId = summaryId;
  ddSummaryData    = {};
  ddCurrentMode    = (_userPrefs && _userPrefs.level === 'Expert') ? 'pro' : 'novice';
  _ddAutoPlay      = autoPlay;
  _ddTargetBacktestId = targetBacktestId;
  _ddTargetArgTerms   = targetArgTerms;
  showPage('deep-dive');
  loadDeepDive(summaryId);
  loadMindmap(summaryId);
}

// ── 名詞解釋（glossary）────────────────────────────────────────
// glossary_matches 的 start/end 是後端內部合併字串的位置，跟前端各欄位分開
// 渲染的方式對不上，所以不用座標，改用 surface 文字本身去比對、包起來。
function _escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function _buildGlossaryIndex(matches) {
  const defs = {};
  const surfaceToId = {};
  (matches || []).forEach(m => {
    if (!m || !m.surface || m.term_id == null) return;
    if (!(m.term_id in defs)) {
      defs[m.term_id] = { canonical: m.canonical || m.surface, definition: m.short_definition || '' };
    }
    if (!(m.surface in surfaceToId)) {
      surfaceToId[m.surface] = m.term_id;
    }
  });
  const surfaces = Object.keys(surfaceToId).sort((a, b) => b.length - a.length);
  return { defs, surfaceToId, surfaces };
}

function annotateGlossaryText(text, glossaryIndex) {
  if (!text) return '';
  const { surfaces, surfaceToId } = glossaryIndex;
  if (!surfaces.length) return escapeHtml(text);
  const pattern = new RegExp('(' + surfaces.map(_escapeRegex).join('|') + ')', 'g');
  return text.split(pattern).map((part, i) => {
    if (i % 2 === 1 && surfaceToId.hasOwnProperty(part)) {
      return `<span class="glossary-term border-b border-dotted border-secondary text-secondary cursor-pointer" onclick="showGlossaryTerm(event, ${surfaceToId[part]})">${escapeHtml(part)}</span>`;
    }
    return escapeHtml(part);
  }).join('');
}

let _glossaryPopoverEl = null;
function showGlossaryTerm(evt, termId) {
  evt.stopPropagation();
  hideGlossaryTerm();
  const info = window._glossaryDefs && window._glossaryDefs[termId];
  if (!info) return;
  const pop = document.createElement('div');
  pop.className = 'glossary-popover fixed z-50 max-w-xs p-3 rounded-lg bg-inverse-surface text-inverse-on-surface text-sm shadow-lg';
  pop.innerHTML = `<div class="font-bold mb-1">${escapeHtml(info.canonical)}</div><div>${escapeHtml(info.definition)}</div>`;
  document.body.appendChild(pop);
  const rect = evt.currentTarget.getBoundingClientRect();
  const maxLeft = window.innerWidth - pop.offsetWidth - 12;
  pop.style.top  = (rect.bottom + 6) + 'px';
  pop.style.left = Math.max(12, Math.min(rect.left, maxLeft)) + 'px';
  _glossaryPopoverEl = pop;
  setTimeout(() => document.addEventListener('click', _dismissGlossaryPopover), 0);
}

function hideGlossaryTerm() {
  if (_glossaryPopoverEl) { _glossaryPopoverEl.remove(); _glossaryPopoverEl = null; }
  document.removeEventListener('click', _dismissGlossaryPopover);
}

function _dismissGlossaryPopover(e) {
  if (_glossaryPopoverEl && !_glossaryPopoverEl.contains(e.target)) hideGlossaryTerm();
}

// ── Mode rendering ────────────────────────────────────────────
function renderDdModeContent(mode) {
  const s = ddSummaryData[mode];
  if (!s) return;

  // 名詞解釋只在小白模式顯示，老鳥模式維持原本純文字（不畫底線、不彈解釋）
  const glossaryIndex = mode === 'novice'
    ? _buildGlossaryIndex(s.glossary_matches)
    : { defs: {}, surfaceToId: {}, surfaces: [] };
  window._glossaryDefs = glossaryIndex.defs;

  const tw = s.investment_takeaways || {};
  let twHtml = '';
  if ((tw.bullish || []).length) {
    twHtml += `<div class="p-6 rounded-lg bg-on-primary-container/5 border-l-4 border-on-primary-container">
      <div class="flex items-center gap-2 mb-3 text-on-primary-container">
        <span class="material-symbols-outlined font-bold">trending_up</span>
        <span class="font-label font-bold uppercase tracking-wider text-xs">Bullish Thesis</span>
      </div>
      <ul class="space-y-2">${(tw.bullish).map(item => `<li class="flex gap-2 text-on-surface font-body font-medium"><span class="text-on-primary-container shrink-0">•</span><span>${item}</span></li>`).join('')}</ul>
    </div>`;
  }
  if ((tw.bearish || []).length) {
    twHtml += `<div class="p-6 rounded-lg bg-tertiary-container/10 border-l-4 border-tertiary-container">
      <div class="flex items-center gap-2 mb-3 text-tertiary-container">
        <span class="material-symbols-outlined font-bold">trending_down</span>
        <span class="font-label font-bold uppercase tracking-wider text-xs">Bearish Thesis</span>
      </div>
      <ul class="space-y-2">${(tw.bearish).map(item => `<li class="flex gap-2 text-on-surface font-body font-medium"><span class="text-tertiary-container shrink-0">•</span><span>${item}</span></li>`).join('')}</ul>
    </div>`;
  }
  if ((tw.watchlist || []).length) {
    twHtml += `<div class="p-6 rounded-lg bg-secondary/5 border-l-4 border-secondary">
      <div class="flex items-center gap-2 mb-3 text-secondary">
        <span class="material-symbols-outlined font-bold">visibility</span>
        <span class="font-label font-bold uppercase tracking-wider text-xs">Watchlist</span>
      </div>
      <ul class="space-y-2">${(tw.watchlist).map(item => `<li class="flex gap-2 text-on-surface font-body font-medium"><span class="text-secondary shrink-0">•</span><span>${item}</span></li>`).join('')}</ul>
    </div>`;
  }
  document.getElementById('dd-takeaways').innerHTML = twHtml || '<p class="text-outline text-sm">無資料</p>';

  const args = s.arguments || [];
  window._ddArgs = args;
  let argHtml = '';
  args.forEach((arg, i) => {
    const isLast      = i === args.length - 1;
    const timestamp   = (arg.evidence_timestamps && arg.evidence_timestamps[0]) || '--:--';
    const fullSummary = arg.summary || '';
    const { preview, hasMore } = splitThesisPreview(fullSummary);
    const previewHtml = annotateGlossaryText(preview, glossaryIndex);
    const fullHtml    = annotateGlossaryText(fullSummary, glossaryIndex);

    const keyData = arg.key_data || [];
    let keyDataHtml = '';
    if (keyData.length) {
      keyDataHtml = `<div class="flex flex-wrap gap-3 mb-3">${keyData.map(kd => `
        <div class="min-w-[160px] px-3 py-2.5 rounded-lg bg-surface-container-low border border-outline-variant/20 text-center" style="width:max-content">
          <div class="text-secondary font-bold text-base leading-snug whitespace-nowrap mb-1">${escapeHtml(kd.value || '')}</div>
          <div class="flex items-center justify-center gap-1.5 text-outline text-[11px] font-bold">
            <span class="material-symbols-outlined text-sm">query_stats</span>
            <span>${escapeHtml(kd.label || '')}</span>
          </div>
        </div>`).join('')}</div>`;
    }

    argHtml += `
      <div class="${isLast ? '' : 'border-b border-outline-variant/30'} py-8 px-2 rounded-lg" data-arg-index="${i}">
        <div class="flex flex-col md:flex-row gap-5">
          <div class="md:w-28 flex-shrink-0">
            <button onclick="playAtTimestamp('${timestamp}')" class="bg-secondary-container text-tertiary-container font-bold px-4 py-2 rounded-full text-sm hover:scale-95 transition-transform flex items-center gap-2">
              <span class="material-symbols-outlined text-sm" style="font-variation-settings: 'FILL' 1;">play_arrow</span>
              ${timestamp}
            </button>
          </div>
          <div class="flex-1 space-y-3">
            <div class="flex items-center gap-3 flex-wrap">
              <h3 class="font-['Epilogue'] text-xl font-bold text-on-surface">${arg.topic || ''}</h3>
            </div>
            ${keyDataHtml}
            <div class="thesis-preview text-on-surface-variant leading-relaxed text-base">${previewHtml}</div>
            ${hasMore ? `
            <div class="thesis-full hidden text-on-surface-variant leading-relaxed text-base"><p>${fullHtml}</p></div>` : ''}
            <div class="flex items-center gap-4 flex-wrap mt-1">
              ${hasMore ? `
              <button onclick="toggleThesis(this)" class="flex items-center gap-1 text-secondary font-label font-bold text-xs uppercase tracking-wider hover:text-on-primary-container transition-colors">
                <span class="btn-label">Read More</span>
                <span class="material-symbols-outlined text-sm btn-icon">expand_more</span>
              </button>` : ''}
              <button onclick="toggleArgAiChat(this, ${i})" class="arg-ai-btn flex items-center gap-1 font-label font-bold text-xs uppercase tracking-wider transition-colors text-secondary hover:text-on-primary-container">
                <span class="material-symbols-outlined text-sm">psychology</span>
                <span class="arg-ai-btn-label">深入問 AI</span>
              </button>
            </div>
            <div class="arg-ai-chat hidden mt-2"></div>
          </div>
        </div>
      </div>`;
  });
  document.getElementById('dd-arguments').innerHTML = argHtml || '<p class="text-outline text-sm">無資料</p>';

  // 從搜尋結果點進來、且目前這個 mode 剛好是命中的那份資料時，直接定位到命中的
  // Critical Thesis Points 卡片：展開（如果被收合）＋捲過去＋短暫高亮，不用使用者
  // 自己在整集內容裡找搜尋詞出現在哪裡（跟 dd-viewpoints 那邊 _ddTargetBacktestId
  // 的「直接跳到該觀點段落」是同一個概念）。這裡在前端重新比對而不是直接用後端算好的
  // index，是因為 novice/pro 兩個 mode 是不同筆資料、topic 順序不一定一樣，只有拿
  // 目前實際渲染出來的這份 args 重新找，才能保證跳的位置是對的。
  if (_ddTargetArgTerms && _ddTargetArgTerms.length) {
    const termsLower = _ddTargetArgTerms.map(t => t.toLowerCase());
    const idx = args.findIndex(arg => {
      const haystacks = [arg.topic || '', arg.summary || ''];
      (arg.key_data || []).forEach(kd => { haystacks.push(kd.label || ''); haystacks.push(kd.value || ''); });
      const blob = haystacks.join(' ').toLowerCase();
      return termsLower.some(t => blob.includes(t));
    });
    if (idx !== -1) {
      _ddTargetArgTerms = null; // 找到了，只跳這一次，避免使用者手動切 mode 時又跳一次
      const targetCard = document.querySelector(`#dd-arguments [data-arg-index="${idx}"]`);
      if (targetCard) {
        const full    = targetCard.querySelector('.thesis-full');
        const moreBtn = targetCard.querySelector('.btn-label')?.closest('button');
        // 命中內容如果被摺進「Read More」裡，直接展開，不要讓使用者還要自己點開才看得到
        if (full && full.classList.contains('hidden') && moreBtn) toggleThesis(moreBtn);
        requestAnimationFrame(() => {
          targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
          targetCard.classList.add('ring-2', 'ring-[#d97f12]', 'ring-offset-2');
          setTimeout(() => targetCard.classList.remove('ring-2', 'ring-[#d97f12]', 'ring-offset-2'), 2000);
        });
      }
    }
  }
}

function updateDdModeButtons() {
  ['novice', 'pro'].forEach(m => {
    const btn = document.getElementById(`btn-mode-${m}`);
    if (!btn) return;
    const hasData = !!ddSummaryData[m];
    const isActive = ddCurrentMode === m;
    btn.className = `px-5 py-1.5 rounded-full font-label text-xs font-bold tracking-wide transition-all ${
      isActive ? 'bg-tertiary-container text-white' :
      hasData  ? 'text-secondary' : 'text-outline/40 cursor-not-allowed'
    }`;
    btn.disabled = !hasData && !isActive;
  });
}

function switchDdMode(mode) {
  ddCurrentMode = mode;
  updateDdModeButtons();
  if (ddSummaryData[mode]) {
    renderDdModeContent(mode);
  } else {
    document.getElementById('dd-takeaways').innerHTML = '<p class="text-outline text-sm">此模式無資料</p>';
    document.getElementById('dd-arguments').innerHTML = '<p class="text-outline text-sm">此模式無資料</p>';
  }
}

// ── Load deep dive data ───────────────────────────────────────
async function loadDeepDive(summaryId) {
  try {
    const res = await fetch(`/api/summaries/${summaryId}/`);
    const s   = await res.json();

    const episodeTitle = s.source_filename?.replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 40) + '...';
    const showName     = s.podcaster || '未知節目';

    document.getElementById('dd-title').textContent = episodeTitle;
    const podcasterEl = document.getElementById('dd-podcaster');
    const podBtn = document.createElement('button');
    podBtn.className = 'font-semibold underline underline-offset-2 hover:text-secondary transition-colors';
    podBtn.textContent = showName;
    podBtn.addEventListener('click', () => openRankedPodcasterByName(showName));
    podcasterEl.textContent = 'Extracted from ';
    podcasterEl.appendChild(podBtn);
    if (s.published_at) {
      const dateSpan = document.createElement('span');
      dateSpan.className = 'text-outline';
      dateSpan.textContent = ' · ' + s.published_at.slice(0, 10);
      podcasterEl.appendChild(dateSpan);
    }
    document.getElementById('dd-one-sentence').textContent = '"' + s.one_sentence_summary + '"';

    const audioEl  = document.getElementById('dd-audio');
    const playerEl = document.getElementById('dd-audio-player');
    if (s.audio_url) {
      audioEl.src = s.audio_url;
      playerEl.classList.remove('hidden');
      document.getElementById('dd-progress-fill').style.width = '0%';
      document.getElementById('dd-progress-thumb').style.left = '0%';
      document.getElementById('dd-current-time').textContent  = '0:00';
      document.getElementById('dd-duration').textContent      = '--:--';
      document.getElementById('dd-play-icon').textContent     = 'play_arrow';
      if (_ddAutoPlay) { audioEl.play().catch(() => {}); document.getElementById('dd-play-icon').textContent = 'pause'; _ddAutoPlay = false; }
    } else {
      audioEl.removeAttribute('src');
      playerEl.classList.add('hidden');
    }

    // Viewpoint Verification
    document.getElementById('dd-viewpoints').innerHTML = '<p class="text-outline text-sm">載入中...</p>';
    fetch(`/api/summaries/${summaryId}/backtesting/`)
      .then(r => r.json())
      .then(async records => {
        await _ensureFavoritesCache();
        const statusMap = {
          pending: { label: '待驗證', icon: 'pending',      cls: 'text-outline',   border: 'border-outline-variant', bg: 'bg-surface-container/50' },
          pass:    { label: '正確',   icon: 'check_circle', cls: 'text-green-600', border: 'border-green-500',       bg: 'bg-green-50/50' },
          fail:    { label: '錯誤',   icon: 'cancel',       cls: 'text-error',     border: 'border-error',           bg: 'bg-error-container/10' },
        };
        const dirLabel = { bullish: '看多', bearish: '看空', neutral: '觀察' };
        let vpHtml = '';
        records.forEach(r => {
          const st      = statusMap[r.result] || statusMap.pending;
          const ticker  = r.ticker || r.asset || '';
          const endDate = r.end_time || '';
          const timestamp = (r.evidence_timestamps && r.evidence_timestamps[0]) || '';
          const playBtn = timestamp
            ? `<button onclick="playAtTimestamp('${timestamp}')" class="flex items-center gap-1 px-2.5 py-1 rounded-full bg-secondary-container text-tertiary-container text-xs font-bold hover:scale-95 transition-transform">
                 <span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1">play_arrow</span>${timestamp}
               </button>`
            : '';
          const calcBtn = (ticker && r.result === 'pending')
            ? `<button onclick="goToCalculatorWithStock('${ticker.replace(/'/g, "\\'")}', '${endDate}', ${s.episode_id != null ? s.episode_id : 'null'})"
                 class="flex items-center gap-1 px-3 py-1 rounded-full bg-tertiary-container/20 border border-tertiary-container/40 text-tertiary-container text-xs font-bold hover:bg-tertiary-container/30 transition-colors">
                 <span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1">calculate</span>試算
               </button>`
            : '';
          vpHtml += `
            <div class="p-6 rounded-lg ${st.bg} border-l-4 ${st.border} transition-shadow" data-backtest-id="${r.id}">
              <div class="flex items-start justify-between mb-2 flex-wrap gap-2">
                <div class="flex items-center gap-2 ${st.cls}">
                  <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">${st.icon}</span>
                  <span class="font-label font-bold uppercase tracking-wider text-xs">${st.label}</span>
                  ${playBtn}
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs font-bold text-outline uppercase">${renderAssetNameStar(r.ticker, r.asset || '')}${r.direction ? ' · ' + (dirLabel[r.direction] || r.direction) : ''}</span>
                  ${calcBtn}
                </div>
              </div>
              <p class="font-body text-on-surface font-medium mb-2">${r.thesis || ''}</p>
              <div class="flex gap-4 flex-wrap">
                ${r.timeframe_raw ? `<span class="text-[10px] font-bold text-outline uppercase tracking-widest">期間：${r.timeframe_raw}</span>` : ''}
                ${endDate ? `<span class="text-[10px] text-outline">驗證截止：${endDate}</span>` : ''}
              </div>
            </div>`;
        });
        document.getElementById('dd-viewpoints').innerHTML = vpHtml || '<p class="text-outline text-sm">此集無可回測觀點</p>';

        // 從 Discover/Rankings 點「Read Thesis」進來時，捲到並高亮對應的那張卡片
        if (_ddTargetBacktestId != null) {
          const targetCard = document.querySelector(`#dd-viewpoints [data-backtest-id="${_ddTargetBacktestId}"]`);
          if (targetCard) {
            targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
            targetCard.classList.add('ring-2', 'ring-[#d97f12]', 'ring-offset-2');
            setTimeout(() => targetCard.classList.remove('ring-2', 'ring-[#d97f12]', 'ring-offset-2'), 2000);
          }
          _ddTargetBacktestId = null;
        }

        // 本集提及的標的（去重），可以直接點名字/星星跳轉、收藏——不含 Critical Thesis
        // Points 那段自由文字裡的公司名（那邊沒有 ticker 可以解析）。
        const tickerSection = document.getElementById('dd-ticker-section');
        const tickerList = document.getElementById('dd-ticker-list');
        const seenTickers = new Set();
        const chipHtml = records.filter(r => {
          if (!resolveAssetCategory(r.ticker)) return false;
          const dedupeKey = r.ticker.toUpperCase();
          if (seenTickers.has(dedupeKey)) return false;
          seenTickers.add(dedupeKey);
          return true;
        }).map(r => `
          <div class="p-4 rounded-lg bg-surface-container-lowest border border-outline-variant/20 font-['Epilogue'] font-bold text-base text-tertiary-container">
            ${renderAssetNameStar(r.ticker, r.asset || r.ticker)}
          </div>`).join('');
        if (chipHtml) {
          tickerList.innerHTML = chipHtml;
          tickerSection.classList.remove('hidden');
        } else {
          tickerSection.classList.add('hidden');
        }
      })
      .catch(() => { document.getElementById('dd-viewpoints').innerHTML = '<p class="text-outline text-sm">載入失敗</p>'; });

    const preferredMode = (_userPrefs && _userPrefs.level === 'Expert') ? 'pro' : 'novice';
    if (s.mode === 'both') {
      ddSummaryData['novice'] = s;
      ddSummaryData['pro']    = s;
      ddCurrentMode = preferredMode;
    } else {
      ddSummaryData[s.mode] = s;
      ddCurrentMode = preferredMode;
      if (s.source_filename) {
        const otherMode = s.mode === 'novice' ? 'pro' : 'novice';
        fetch(`/api/summaries/?source_filename=${encodeURIComponent(s.source_filename)}&mode=${otherMode}`)
          .then(r => r.json())
          .then(list => { if (list.length) return fetch(`/api/summaries/${list[0].id}/`).then(r => r.json()); })
          .then(other => { if (other) { ddSummaryData[other.mode] = other; updateDdModeButtons(); } })
          .catch(() => {});
      }
    }
    switchDdMode(ddCurrentMode);

    document.getElementById('dd-ticker-section').classList.add('hidden');

    if (_discoverCache.length) renderRelated(_discoverCache, summaryId, s.tags);
    else fetch('/api/summaries/?limit=30').then(r => r.json())
      .then(all => { _discoverCache = dedupeByEpisode(all); renderRelated(_discoverCache, summaryId, s.tags); })
      .catch(() => {});

  } catch (e) {
    console.error('loadDeepDive failed', e);
  }
}

// ── Related ───────────────────────────────────────────────────
// 依「領域標籤」重疊數排序，不是單純取最新幾篇——重疊越多代表越相關（跟 discover.js
// 依偏好排序用的是同一套 tags 詞彙，如台股/總體經濟/個股/ETF 等）。完全沒有重疊的
// 集數會被排到最後，等於自動退回「最新優先」當保底，不會因為篩太嚴格而開天窗。
function _tagOverlapCount(tagsA, tagsB) {
  if (!tagsA || !tagsB || !tagsA.length || !tagsB.length) return 0;
  const setB = new Set(tagsB);
  return tagsA.reduce((n, t) => n + (setB.has(t) ? 1 : 0), 0);
}

function renderRelated(list, currentId, currentTags) {
  const el       = document.getElementById('dd-related-grid');
  const filtered = list
    .filter(s => s.id !== currentId)
    .map(s => ({ s, overlap: _tagOverlapCount(currentTags, s.tags) }))
    .sort((a, b) => b.overlap - a.overlap)
    .slice(0, 3)
    .map(x => x.s);
  if (!filtered.length) { el.innerHTML = '<p class="text-outline text-sm col-span-3">暫無資料</p>'; return; }
  el.innerHTML = filtered.map(s => {
    const st    = cardStyle(s.podcaster || s.source_filename);
    const title = (s.source_filename || '').replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 40);
    return `
      <div onclick="openDeepDive(${s.id})" class="bg-surface-container-lowest rounded-lg overflow-hidden group hover:shadow-xl transition-shadow border border-outline-variant/10 cursor-pointer">
        <div class="h-48 overflow-hidden">
          ${podcastAvatar(s.podcaster, st.bg, st.icon)}
        </div>
        <div class="p-6">
          <span class="font-label text-[10px] font-bold text-secondary uppercase tracking-widest mb-2 block">${s.podcaster || ''}</span>
          <h4 class="font-['Epilogue'] font-bold text-lg mb-2 line-clamp-2 group-hover:text-secondary transition-colors">${title}</h4>
          <p class="font-body text-sm text-on-surface-variant line-clamp-2">${s.one_sentence_summary || ''}</p>
        </div>
      </div>`;
  }).join('');
}

// ── Audio controls ────────────────────────────────────────────
function toggleAudioPlay() {
  const audio = document.getElementById('dd-audio');
  const icon  = document.getElementById('dd-play-icon');
  if (!audio.src) return;
  if (audio.paused) { audio.play(); icon.textContent = 'pause'; }
  else { audio.pause(); icon.textContent = 'play_arrow'; }
}

function seekAudio(event) {
  const audio = document.getElementById('dd-audio');
  if (!audio.src || !audio.duration) return;
  const bar   = document.getElementById('dd-progress-bar');
  const rect  = bar.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  audio.currentTime = ratio * audio.duration;
}

function playAtTimestamp(ts) {
  const audioEl = document.getElementById('dd-audio');
  if (!audioEl || !audioEl.src) { alert('此集無音檔連結'); return; }
  const parts   = ts.split(':').map(Number);
  let seconds   = 0;
  if (parts.length === 2) seconds = parts[0] * 60 + parts[1];
  else if (parts.length === 3) seconds = parts[0] * 3600 + parts[1] * 60 + parts[2];
  audioEl.currentTime = seconds;
  audioEl.play();
  document.getElementById('dd-play-icon').textContent = 'pause';
  document.getElementById('dd-audio-player').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Mind map ──────────────────────────────────────────────────
async function loadMindmap(summaryId) {
  const container = document.getElementById('mindmap-container');
  container.innerHTML = '<p class="text-outline text-sm p-4">心智圖產生中...</p>';
  try {
    const res  = await fetch(`/api/summaries/${summaryId}/mindmap/`);
    const data = await res.json();
    const mm   = data.mindmap;
    if (!mm || (!mm.central_topic && !(mm.arguments || []).length)) {
      container.innerHTML = '<p class="text-outline text-sm p-4">此集尚無心智圖資料</p>';
      return;
    }
    renderMindmap(mm, container);
  } catch (e) {
    container.innerHTML = '<p class="text-outline text-sm p-4">心智圖載入失敗</p>';
  }
}

function renderMindmap(data, container) {
  container.innerHTML = '';
  const marginLeft = 320, marginRight = 260, marginTop = 40, marginBottom = 40, treeWidth = 500;
  const numLeaves = (data.arguments || []).reduce((acc, arg) => {
    const s = Array.isArray(arg.summary) ? arg.summary : [arg.summary];
    return acc + Math.min(3, s.length);
  }, 0);
  const treeHeight = Math.max(300, numLeaves * 36);
  const totalW = treeWidth + marginLeft + marginRight;
  const totalH = treeHeight + marginTop + marginBottom;

  const treeData = {
    name: data.title || 'Mindmap',
    children: (data.arguments || []).map(arg => ({
      name: arg.topic,
      children: (Array.isArray(arg.summary) ? arg.summary : [arg.summary]).slice(0, 3).map(s => ({ name: s }))
    }))
  };

  const root = d3.hierarchy(treeData);
  d3.tree().size([treeHeight, treeWidth])(root);

  const svg = d3.select(container).append('svg')
    .attr('width', totalW).attr('height', totalH).style('display', 'block');
  const g = svg.append('g').attr('transform', `translate(${marginLeft},${marginTop})`);

  g.selectAll('.link').data(root.links()).enter().append('path')
    .attr('fill', 'none').attr('stroke', '#286671').attr('stroke-opacity', 0.4).attr('stroke-width', 1.5)
    .attr('d', d3.linkHorizontal().x(d => d.y).y(d => d.x));

  const node = g.selectAll('.node').data(root.descendants()).enter().append('g')
    .attr('transform', d => `translate(${d.y},${d.x})`);

  node.append('circle').attr('r', 5)
    .attr('fill', d => d.depth === 0 ? '#113236' : d.depth === 1 ? '#286671' : '#d97f12');

  node.append('text').attr('dy', '0.35em')
    .attr('x', d => d.children ? -12 : 12)
    .attr('text-anchor', d => d.children ? 'end' : 'start')
    .style('font-size', '14px').style('font-family', 'Manrope')
    .style('font-weight', d => d.depth <= 1 ? '600' : '400')
    .style('fill', d => d.depth === 0 ? '#113236' : '#1c1c16')
    .text(d => d.data.name || '');
}

// ── Thesis preview truncation ────────────────────────────────
// 找 ≥minLen 字後最近的句尾標點當切點；剩餘沒顯示的字數 ≤minRemainder 就直接併入
// preview 一起顯示（不出現按鈕），避免「按了 Show More 卻只多幾個字」的無意義互動。
function splitThesisPreview(text, minLen = 120, minRemainder = 30) {
  if (!text) return { preview: '', hasMore: false };
  if (text.length <= minLen) return { preview: text, hasMore: false };

  const sentences = text.match(/[^。！？!?]+[。！？!?]+/g);
  let cut = null;
  if (sentences) {
    let acc = 0;
    for (const s of sentences) {
      acc += s.length;
      if (acc >= minLen) { cut = acc; break; }
    }
  }

  let preview;
  if (cut === null) {
    // 找不到可用的句尾標點（或所有句子加起來都不到門檻），退回硬切
    cut = Math.min(minLen, text.length);
    preview = text.slice(0, cut) + '...';
  } else {
    preview = text.slice(0, cut);
  }

  if (text.length - cut <= minRemainder) return { preview: text, hasMore: false };
  return { preview, hasMore: true };
}

// ── Thesis expand/collapse ────────────────────────────────────
function toggleThesis(btn) {
  const item    = btn.closest('.flex-1');
  const preview = item.querySelector('.thesis-preview');
  const full    = item.querySelector('.thesis-full');
  const label   = btn.querySelector('.btn-label');
  const icon    = btn.querySelector('.btn-icon');
  const isOpen  = !full.classList.contains('hidden');
  if (isOpen) {
    full.classList.add('hidden');
    preview.classList.remove('hidden');
    label.textContent = 'Read More';
    icon.textContent  = 'expand_more';
    btn.classList.remove('text-on-primary-container');
    btn.classList.add('text-secondary');
  } else {
    preview.classList.add('hidden');
    full.classList.remove('hidden');
    label.textContent = 'Show Less';
    icon.textContent  = 'expand_less';
    btn.classList.remove('text-secondary');
    btn.classList.add('text-on-primary-container');
  }
}

// ── Argument inline AI chat ───────────────────────────────────

function toggleArgAiChat(btn, argIdx) {
  const arg      = (window._ddArgs || [])[argIdx];
  if (!arg) return;
  const container = btn.closest('.flex-1');
  const chatEl    = container.querySelector('.arg-ai-chat');
  const label     = btn.querySelector('.arg-ai-btn-label');
  const isOpen    = !chatEl.classList.contains('hidden');

  if (isOpen) {
    chatEl.classList.add('hidden');
    chatEl.innerHTML = '';
    chatEl.dataset.convId = '';
    btn.classList.remove('text-on-primary-container');
    btn.classList.add('text-secondary');
    label.textContent = '深入問 AI';
    return;
  }

  chatEl.classList.remove('hidden');
  btn.classList.remove('text-secondary');
  btn.classList.add('text-on-primary-container');
  label.textContent = '關閉 AI';

  const topic = arg.topic || '';
  const summary = arg.summary || '';
  chatEl.dataset.topic = topic;
  chatEl.dataset.summary = summary;
  chatEl.innerHTML = `
    <div class="rounded-2xl border border-secondary/20 bg-surface-container-lowest overflow-hidden">
      <div class="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20 bg-secondary-container/10">
        <span class="material-symbols-outlined text-secondary text-sm">psychology</span>
        <span class="font-label text-xs font-bold text-secondary uppercase tracking-widest">AI 助理・關於「${escapeHtml(topic)}」</span>
      </div>
      <div class="arg-chat-messages px-4 py-4 space-y-3 max-h-80 overflow-y-auto"></div>
      <div class="border-t border-outline-variant/20 px-3 py-3 flex gap-2 items-center">
        <input type="text"
          class="flex-1 bg-surface-container text-sm rounded-full px-4 py-2 border border-outline-variant/30 focus:outline-none focus:border-secondary/40 placeholder:text-outline/50"
          placeholder="輸入你的問題..."
          onkeydown="if(event.key==='Enter'&&!event.isComposing)_ddArgSend(this)">
        <button onclick="_ddArgSend(this.previousElementSibling)"
          class="w-9 h-9 rounded-full bg-secondary text-white flex items-center justify-center hover:opacity-90 active:scale-95 transition-all flex-shrink-0">
          <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">send</span>
        </button>
      </div>
    </div>`;

  _ddArgAddGreeting(chatEl.querySelector('.arg-chat-messages'));
}

function _ddArgSend(input) {
  const query = (input.value || '').trim();
  if (!query) return;
  input.value = '';
  const chatEl = input.closest('.arg-ai-chat');

  const isFirstTurn = !chatEl.dataset.convId;
  let sendQuery = query;
  if (isFirstTurn) {
    const topic   = chatEl.dataset.topic || '';
    const summary = chatEl.dataset.summary || '';
    const context = summary
      ? `主播在這集提到關於「${topic}」：${summary.slice(0, 200)}${summary.length > 200 ? '...' : ''}\n\n`
      : '';
    sendQuery = `${context}使用者問題：${query}`;
  }
  _ddArgSubmit(chatEl, sendQuery, query);
}

async function _ddArgSubmit(chatEl, query, displayQuery) {
  const msgBox  = chatEl.querySelector('.arg-chat-messages');
  const convId  = chatEl.dataset.convId || null;
  _ddArgAddUser(msgBox, displayQuery != null ? displayQuery : query);
  const loadEl = _ddArgAddLoading(msgBox);
  try {
    const body = { query };
    if (convId) body.conversation_id = parseInt(convId);
    const res  = await fetch('/api/ai/chat/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    loadEl.remove();
    if (data.ok) {
      chatEl.dataset.convId = data.conversation_id;
      _ddArgAddAI(msgBox, data.answer, data.follow_ups || [], chatEl);
    } else {
      _ddArgAddAI(msgBox, '發生錯誤：' + (data.error || '請稍後再試'), [], chatEl);
    }
  } catch {
    loadEl.remove();
    _ddArgAddAI(msgBox, '網路錯誤，請稍後再試。', [], chatEl);
  }
}

function _ddArgAddGreeting(msgBox) {
  const row = document.createElement('div');
  row.className = 'flex items-start gap-2';
  const icon = document.createElement('div');
  icon.className = 'w-6 h-6 rounded-full bg-secondary-container flex items-center justify-center flex-shrink-0 mt-1';
  icon.innerHTML = '<span class="material-symbols-outlined text-on-secondary-container" style="font-size:13px">psychology</span>';
  const bubble = document.createElement('div');
  bubble.className = 'bg-surface-container text-sm px-4 py-2.5 rounded-t-xl rounded-br-xl max-w-[90%] leading-relaxed text-on-surface border border-outline-variant/20';
  bubble.textContent = '有什麼想問的嗎？';
  row.appendChild(icon);
  row.appendChild(bubble);
  msgBox.appendChild(row);
}

function _ddArgAddUser(msgBox, text) {
  const div = document.createElement('div');
  div.className = 'flex justify-end';
  const bubble = document.createElement('div');
  bubble.className = 'bg-tertiary-container text-white text-sm px-4 py-2.5 rounded-t-xl rounded-bl-xl max-w-[85%] leading-relaxed';
  bubble.textContent = text;
  div.appendChild(bubble);
  msgBox.appendChild(div);
  msgBox.scrollTop = msgBox.scrollHeight;
}

function _ddArgAddLoading(msgBox) {
  const div = document.createElement('div');
  div.className = 'flex items-center gap-2';
  div.innerHTML = `<div class="w-6 h-6 rounded-full bg-secondary-container flex items-center justify-center flex-shrink-0"><span class="material-symbols-outlined text-on-secondary-container" style="font-size:13px">psychology</span></div><span class="text-xs text-outline animate-pulse">思考中...</span>`;
  msgBox.appendChild(div);
  msgBox.scrollTop = msgBox.scrollHeight;
  return div;
}

function _ddArgAddAI(msgBox, text, followUps, chatEl) {
  const wrapper = document.createElement('div');
  wrapper.className = 'flex flex-col gap-2';
  const row = document.createElement('div');
  row.className = 'flex items-start gap-2';
  const icon = document.createElement('div');
  icon.className = 'w-6 h-6 rounded-full bg-secondary-container flex items-center justify-center flex-shrink-0 mt-1';
  icon.innerHTML = '<span class="material-symbols-outlined text-on-secondary-container" style="font-size:13px">psychology</span>';
  const bubble = document.createElement('div');
  bubble.className = 'bg-surface-container text-sm px-4 py-2.5 rounded-t-xl rounded-br-xl max-w-[90%] leading-relaxed text-on-surface border border-outline-variant/20';
  bubble.innerHTML = typeof _renderMarkdown === 'function' ? _renderMarkdown(text) : escapeHtml(text);
  row.appendChild(icon);
  row.appendChild(bubble);
  wrapper.appendChild(row);
  if (followUps.length) {
    const chips = document.createElement('div');
    chips.className = 'flex flex-wrap gap-2 pl-8';
    followUps.forEach(q => {
      const btn = document.createElement('button');
      btn.className = 'px-3 py-1 rounded-full border border-secondary/30 bg-surface-container-lowest text-secondary font-label text-xs font-semibold hover:bg-secondary-container/30 transition-all text-left';
      btn.textContent = q;
      btn.onclick = () => _ddArgSubmit(chatEl, q);
      chips.appendChild(btn);
    });
    wrapper.appendChild(chips);
  }
  msgBox.appendChild(wrapper);
  msgBox.scrollTop = msgBox.scrollHeight;
}

