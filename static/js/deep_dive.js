// ── Deep Dive state ───────────────────────────────────────────
let ddSummaryData = {};
let ddCurrentMode = 'novice';
let _ddAutoPlay   = false;

function openDeepDive(summaryId, autoPlay = false) {
  currentSummaryId = summaryId;
  ddSummaryData    = {};
  ddCurrentMode    = (_userPrefs && _userPrefs.level === 'Expert') ? 'pro' : 'novice';
  _ddAutoPlay      = autoPlay;
  showPage('deep-dive');
  loadDeepDive(summaryId);
  loadMindmap(summaryId);
}

// ── Mode rendering ────────────────────────────────────────────
function renderDdModeContent(mode) {
  const s = ddSummaryData[mode];
  if (!s) return;

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
  let argHtml = '';
  args.forEach((arg, i) => {
    const isLast      = i === args.length - 1;
    const timestamp   = (arg.evidence_timestamps && arg.evidence_timestamps[0]) || '--:--';
    const fullSummary = arg.summary || '';
    const preview     = getTwoSentences(fullSummary);
    argHtml += `
      <div class="${isLast ? '' : 'border-b border-outline-variant/30'} py-8 px-2">
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
            <div class="thesis-preview text-on-surface-variant leading-relaxed text-base">${preview}</div>
            <div class="thesis-full hidden text-on-surface-variant leading-relaxed text-base"><p>${fullSummary}</p></div>
            <button onclick="toggleThesis(this)" class="flex items-center gap-1 text-secondary font-label font-bold text-xs uppercase tracking-wider hover:text-on-primary-container transition-colors mt-1">
              <span class="btn-label">Read More</span>
              <span class="material-symbols-outlined text-sm btn-icon">expand_more</span>
            </button>
          </div>
        </div>
      </div>`;
  });
  document.getElementById('dd-arguments').innerHTML = argHtml || '<p class="text-outline text-sm">無資料</p>';
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
          const calcBtn = (ticker && r.result === 'pending')
            ? `<button onclick="goToCalculatorWithStock('${ticker.replace(/'/g, "\\'")}', '${endDate}')"
                 class="flex items-center gap-1 px-3 py-1 rounded-full bg-tertiary-container/20 border border-tertiary-container/40 text-tertiary-container text-xs font-bold hover:bg-tertiary-container/30 transition-colors">
                 <span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1">calculate</span>試算
               </button>`
            : '';
          vpHtml += `
            <div class="p-6 rounded-lg ${st.bg} border-l-4 ${st.border}">
              <div class="flex items-start justify-between mb-2 flex-wrap gap-2">
                <div class="flex items-center gap-2 ${st.cls}">
                  <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">${st.icon}</span>
                  <span class="font-label font-bold uppercase tracking-wider text-xs">${st.label}</span>
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

    if (_discoverCache.length) renderRelated(_discoverCache, summaryId);
    else fetch('/api/summaries/?limit=30').then(r => r.json())
      .then(all => { _discoverCache = dedupeByEpisode(all); renderRelated(_discoverCache, summaryId); })
      .catch(() => {});

  } catch (e) {
    console.error('loadDeepDive failed', e);
  }
}

// ── Related ───────────────────────────────────────────────────
function renderRelated(list, currentId) {
  const el       = document.getElementById('dd-related-grid');
  const filtered = list.filter(s => s.id !== currentId).slice(0, 3);
  if (!filtered.length) { el.innerHTML = '<p class="text-outline text-sm col-span-3">暫無資料</p>'; return; }
  el.innerHTML = filtered.map(s => {
    const st    = cardStyle(s.podcaster || s.source_filename);
    const title = (s.source_filename || '').replace(/\.srt$/i, '') || s.one_sentence_summary?.slice(0, 40);
    return `
      <div onclick="openDeepDive(${s.id})" class="bg-surface-container-lowest rounded-lg overflow-hidden group hover:shadow-xl transition-shadow border border-outline-variant/10 cursor-pointer">
        <div class="h-48 overflow-hidden">
          <div class="w-full h-full flex flex-col items-center justify-center gap-2" style="background:${st.bg}">
            <span class="material-symbols-outlined text-white/70 text-3xl" style="font-variation-settings:'FILL' 1">${st.icon}</span>
          </div>
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

// ── Glossary ──────────────────────────────────────────────────
window._selectedText = '';

document.addEventListener('mouseup', (e) => {
  if (document.getElementById('glossary-card').contains(e.target)) return;
  if (document.getElementById('glossary-btn').contains(e.target)) return;
  if (!document.getElementById('page-deep-dive').classList.contains('active')) {
    document.getElementById('glossary-btn').classList.add('hidden');
    return;
  }
  const sel  = window.getSelection();
  const text = sel ? sel.toString().trim() : '';
  if (text.length >= 2 && text.length <= 20) {
    window._selectedText = text;
    const range = sel.getRangeAt(0).getBoundingClientRect();
    const btn   = document.getElementById('glossary-btn');
    btn.style.top  = (range.bottom + 6) + 'px';
    btn.style.left = range.left + 'px';
    btn.classList.remove('hidden');
  } else {
    document.getElementById('glossary-btn').classList.add('hidden');
    window._selectedText = '';
  }
});

async function lookupGlossary() {
  document.getElementById('glossary-btn').classList.add('hidden');
  const q = window._selectedText;
  if (!q) return;
  const card = document.getElementById('glossary-card');
  try {
    const res     = await fetch(`/api/glossary/lookup/?q=${encodeURIComponent(q)}`);
    const data    = await res.json();
    const results = data.results || [];
    if (!results.length) {
      document.getElementById('gc-term').textContent = `「${q}」`;
      document.getElementById('gc-short').textContent = '目前尚無此詞的解釋。';
      document.getElementById('gc-long').classList.add('hidden');
      document.getElementById('gc-more-btn').classList.add('hidden');
    } else {
      const t = results[0];
      document.getElementById('gc-term').textContent  = t.term;
      document.getElementById('gc-short').textContent = t.short_definition;
      document.getElementById('gc-long').textContent  = t.long_definition || '';
      document.getElementById('gc-long').classList.add('hidden');
      document.getElementById('gc-more-btn').classList.toggle('hidden', !t.long_definition);
    }
    card.style.display = 'block';
  } catch (err) {
    document.getElementById('gc-term').textContent  = '載入失敗';
    document.getElementById('gc-short').textContent = err.message || '請稍後再試。';
    document.getElementById('gc-more-btn').classList.add('hidden');
    card.style.display = 'block';
  }
}

function showGlossaryLong() {
  document.getElementById('gc-long').classList.remove('hidden');
  document.getElementById('gc-more-btn').classList.add('hidden');
}
