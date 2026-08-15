// ── Shared state ──────────────────────────────────────────────
let _discoverCache = [];
let _pageHistory = [];
let _accuracyCache = null;
let _podcastImages = {};  // show_name → image_url
let _backOverride = null; // 下一次 goBack() 要去的頁面，用一次就清掉（例如「查看完整摘要」這種
                           // 跳到 deep-dive 但內容跟共用頁面堆疊裡記的不一樣的入口，避免返回鍵跳錯集數）

async function loadPodcastImages() {
  try {
    const res = await fetch('/api/summaries/podcast-images/');
    if (res.ok) _podcastImages = await res.json();
  } catch (_) {}
}

function podcastAvatar(showName, bg, icon, cls = '') {
  const img = _podcastImages[showName];
  if (img) return `<img src="${img}" alt="${showName}" class="w-full h-full object-cover ${cls}"/>`;
  return `<div class="w-full h-full flex items-center justify-center" style="background:${bg}">
    <span class="material-symbols-outlined text-white/70 text-3xl" style="font-variation-settings:'FILL' 1">${icon}</span>
  </div>`;
}

// ── Card colour / icon util ────────────────────────────────────
const _cardBgs   = ['#113236','#286671','#472500','#1c1c16','#2c4c50','#d97f12','#1a3040','#3a2000'];
const _cardIcons = ['podcasts','trending_up','show_chart','account_balance','public','workspace_premium','ssid_chart','mic'];

function cardStyle(str) {
  let h = 0;
  for (const c of (str || '')) h = (h * 31 + c.charCodeAt(0)) & 0xffffffff;
  const i = Math.abs(h) % _cardBgs.length;
  return { bg: _cardBgs[i], icon: _cardIcons[i] };
}

function dedupeByEpisode(list) {
  const seen = new Set();
  return list.filter(s => {
    if (seen.has(s.source_filename)) return false;
    seen.add(s.source_filename);
    return true;
  });
}

function formatTime(sec) {
  if (isNaN(sec)) return '--:--';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function getTwoSentences(text) {
  if (!text) return '';
  const sentences = text.match(/[^。！？!?]+[。！？!?]+/g) || [];
  if (sentences.length >= 2) return sentences.slice(0, 2).join('');
  return text.slice(0, 80) + (text.length > 80 ? '...' : '');
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Navigation ────────────────────────────────────────────────
function goBack() {
  if (_backOverride) {
    const target = _backOverride;
    _backOverride = null;
    // showPage() 進來時本來就會把同一個頁面名稱再推一次，這裡順便清掉避免多按一次沒反應
    if (_pageHistory.length && _pageHistory[_pageHistory.length - 1] === target) {
      _pageHistory.pop();
    }
    _renderPage(target);
    return;
  }
  if (_pageHistory.length > 0) {
    const prev = _pageHistory.pop();
    _renderPage(prev);
  }
}

function showPage(page) {
  const currentActive = document.querySelector('.page.active');
  if (currentActive) {
    const currentId = currentActive.id.replace('page-', '');
    if (currentId !== page) _pageHistory.push(currentId);
  }
  _renderPage(page);
}

function _renderPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => { b.classList.remove('active'); b.classList.add('opacity-60'); });
  document.getElementById('page-' + page).classList.add('active');
  window.scrollTo(0, 0);

  const navMap = { discover: 'discover', 'deep-dive': 'discover', rankings: 'rankings', podcaster: 'rankings', calculator: 'calculator', ai: 'ai', profile: 'profile' };
  const navId = 'nav-' + (navMap[page] || page);
  const activeBtn = document.getElementById(navId);
  if (activeBtn) { activeBtn.classList.add('active'); activeBtn.classList.remove('opacity-60'); }

  // Global input bar hidden — AI page has its own inline input
  document.getElementById('ai-input-bar').classList.add('hidden');
  if (page === 'ai' && typeof onAiPageShow === 'function') onAiPageShow();
}

// ── Accuracy cache (shared by discover, rankings, podcaster) ──
async function _ensureAccuracyCache() {
  if (_accuracyCache) return _accuracyCache;
  try {
    const res = await fetch('/api/summaries/accuracy-ranking/');
    const records = res.ok ? await res.json() : [];
    const map = {};
    for (const r of records) {
      if (!r.podcaster) continue;
      map[r.podcaster] = { pass: r.pass, fail: r.fail, total: r.total, pct: r.accuracy };
    }
    _accuracyCache = map;
  } catch (e) {
    _accuracyCache = {};
  }
  return _accuracyCache;
}

// ── Boot ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadPodcastImages();
  loadDiscoverData();
  loadHotTags();
  loadRankings();
  loadPreferences();

  // disc-audio progress
  const discAudio = document.getElementById('disc-audio');
  discAudio.addEventListener('loadedmetadata', () => {
    const el = document.getElementById('disc-duration');
    if (el) el.textContent = formatTime(discAudio.duration);
  });
  discAudio.addEventListener('timeupdate', () => {
    if (!discAudio.duration) return;
    const pct = (discAudio.currentTime / discAudio.duration * 100).toFixed(1) + '%';
    const fill = document.getElementById('disc-progress-fill');
    const cur  = document.getElementById('disc-current-time');
    if (fill) fill.style.width = pct;
    if (cur)  cur.textContent = formatTime(discAudio.currentTime);
  });
  discAudio.addEventListener('ended', () => {
    const icon = document.getElementById('disc-play-icon');
    if (icon) icon.textContent = 'play_arrow';
  });

  // dd-audio progress
  const audio = document.getElementById('dd-audio');
  if (!audio) return;
  audio.addEventListener('loadedmetadata', () => {
    document.getElementById('dd-duration').textContent = formatTime(audio.duration);
  });
  audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    document.getElementById('dd-progress-fill').style.width = pct + '%';
    document.getElementById('dd-progress-thumb').style.left = pct + '%';
    document.getElementById('dd-current-time').textContent = formatTime(audio.currentTime);
  });
  audio.addEventListener('ended', () => {
    document.getElementById('dd-play-icon').textContent = 'play_arrow';
    document.getElementById('dd-progress-fill').style.width = '0%';
    document.getElementById('dd-progress-thumb').style.left = '0%';
    document.getElementById('dd-current-time').textContent = '0:00';
  });
});
