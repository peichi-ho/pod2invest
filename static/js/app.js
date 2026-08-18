// ── Shared state ──────────────────────────────────────────────
let _discoverCache = [];
let _pageHistory = [];
let _accuracyCache = null;
let _podcastImages = {};  // show_name → image_url
let _backOverride = null;       // 下一次 goBack() 要去的頁面，用一次就清掉（例如「查看完整摘要」這種
                                 // 跳到 deep-dive 但內容跟共用頁面堆疊裡記的不一樣的入口，避免返回鍵跳錯集數）
let _backOverrideOwner = null;  // _backOverride 綁定的頁面（設定當下要跳去顯示的目的頁）
let _backOverrideArmed = false; // 是否已經真的顯示過 owner 頁一次。使用者如果沒有在 owner 頁真的按
                                 // 返回、而是改用底部導覽列等其他方式離開，_backOverride 會一直卡著沒
                                 // 被消耗掉，之後在任何頁面按返回都會被劫持跳去過期的目標——showPage()
                                 // 偵測到「已經 armed 過、卻又被呼叫」時會把過期的 override 清掉，見下方。

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

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Navigation ────────────────────────────────────────────────
function goBack() {
  if (_backOverride) {
    const target = _backOverride;
    _backOverride = null;
    _backOverrideOwner = null;
    _backOverrideArmed = false;
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
  // _backOverride 已經在 owner 頁顯示過一次（armed），卻又被呼叫到 showPage()——代表使用者
  // 不是按返回鍵離開 owner 頁的（例如改點底部導覽列），override 沒被正常消耗掉、已經過期，
  // 清掉避免它卡住污染到接下來任何一頁的返回鍵。真正設定 override 當下呼叫的那次 showPage()
  // 因為 armed 還是 false，不會被這裡誤清掉。
  if (_backOverride && _backOverrideArmed) {
    _backOverride = null;
    _backOverrideOwner = null;
    _backOverrideArmed = false;
  }

  const currentActive = document.querySelector('.page.active');
  if (currentActive) {
    const currentId = currentActive.id.replace('page-', '');
    if (currentId !== page) _pageHistory.push(currentId);
  }
  _renderPage(page);

  if (_backOverride && page === _backOverrideOwner) {
    _backOverrideArmed = true;
  }
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
  if (page === 'assets' && typeof onAssetsPageShow === 'function') onAssetsPageShow();
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

// ── Favorites（收藏標的）＋ 跨頁面跳轉到 ASSETS 詳情頁 ──────────
// Discover/Rankings/Deep Dive/Assets 共用，放在 app.js 是因為它比這幾個頁面
// 自己的 JS 都先載入（見 templates/base.html 的 <script> 順序）。
let _favoritesCache = null;          // Set<"category:symbol">
let _favoritesLoadingPromise = null;

function _favKey(category, symbol) { return `${category}:${symbol}`; }

// 跟 apps/assets/services/tw_stock_rankings.py 的 _is_tw_etf() 保持同一條規則。
function _isTwEtfCode(code) { return /^00\d{2,4}[A-Z]?$/.test(code); }

// ticker 格式參考 apps/summaries/models.py TickerMap 的註解："2330.TW" / "NVDA"。
// 回傳 { category, symbol }（symbol 不含 .TW 後綴）；不是 .TW 結尾（美股/指數/
// 大宗商品等）一律回傳 null，代表目前 ASSETS 頁面還不支援。
function resolveAssetCategory(ticker) {
  if (!ticker) return null;
  const t = ticker.trim();
  if (!t.toUpperCase().endsWith('.TW')) return null;
  const code = t.slice(0, -3).toUpperCase();
  if (!code) return null;
  return { category: _isTwEtfCode(code) ? 'tw_etf' : 'tw_stock', symbol: code };
}

async function _ensureFavoritesCache() {
  if (_favoritesCache) return _favoritesCache;
  if (_favoritesLoadingPromise) return _favoritesLoadingPromise;
  _favoritesLoadingPromise = (async () => {
    try {
      const res = await fetch('/api/accounts/favorites/');
      const data = res.ok ? await res.json() : { symbols: [] };
      _favoritesCache = new Set((data.symbols || []).map(s => _favKey(s.category, s.symbol)));
    } catch (e) {
      // accountsdb 還沒接上時這裡一定會失敗，優雅退回空清單，不能讓整頁掛掉。
      _favoritesCache = new Set();
    }
    return _favoritesCache;
  })();
  return _favoritesLoadingPromise;
}

// 極簡共用提示訊息，「尚未支援」跟收藏失敗都用這個，全專案目前沒有 toast 元件。
function showToast(msg) {
  let el = document.getElementById('app-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'app-toast';
    el.className = 'fixed left-1/2 bottom-24 -translate-x-1/2 z-[999] px-4 py-2.5 rounded-full bg-on-primary-container text-white text-sm font-semibold shadow-lg opacity-0 pointer-events-none transition-opacity duration-200';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => { el.style.opacity = '0'; }, 2000);
}

function onAssetNameClick(ticker, displayName) {
  const resolved = resolveAssetCategory(ticker);
  if (!resolved) { showToast('尚未支援此標的'); return; }
  showPage('assets');
  openAssetDetail({ symbol: resolved.symbol, category: resolved.category, name: displayName || resolved.symbol });
}

function _paintFavoriteIcon(key, isFav) {
  document.querySelectorAll(`[data-fav-key="${key}"]`).forEach(el => {
    const icon = el.querySelector('.material-symbols-outlined');
    if (icon) icon.style.fontVariationSettings = `'FILL' ${isFav ? 1 : 0}`;
    el.classList.toggle('text-[#d97f12]', isFav);
    el.classList.toggle('text-outline/40', !isFav);
  });
}

// 更新收藏狀態的單一入口：改快取、改畫面上所有星星圖示、發自訂事件讓
// assets.js 的「我的最愛」清單可以跟著同步（見 assets.js 的事件監聽）。
// 樂觀更新（先假設會成功）用它來立刻反應；請求失敗時也用它復原成原本狀態，
// 兩種情況畫面邏輯完全一樣，不用寫兩份。
function _setFavoriteState(category, symbol, isFav) {
  const key = _favKey(category, symbol);
  if (isFav) _favoritesCache.add(key); else _favoritesCache.delete(key);
  _paintFavoriteIcon(key, isFav);
  window.dispatchEvent(new CustomEvent('pod2invest:favorite-toggled', {
    detail: { category, symbol, favorited: isFav },
  }));
}

async function toggleFavoriteStar(btnEl, category, symbol) {
  await _ensureFavoritesCache();
  const key = _favKey(category, symbol);
  const wasFav = _favoritesCache.has(key);
  const nextFav = !wasFav;

  // 樂觀更新：先假設會成功，畫面立刻反應（星星圖示 + 我的最愛清單），
  // 不用等網路來回才看到變化；失敗的話下面會呼叫同一個函式復原回 wasFav。
  _setFavoriteState(category, symbol, nextFav);
  btnEl.disabled = true;

  try {
    const res = await fetch('/api/accounts/favorites/toggle/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category, symbol }),
    });
    if (res.status === 401) {
      _setFavoriteState(category, symbol, wasFav);
      showToast('請先登入才能收藏');
      return;
    }
    if (!res.ok) {
      _setFavoriteState(category, symbol, wasFav);
      showToast('操作失敗，請稍後再試');
      return;
    }
    const data = await res.json();
    if (data.favorited !== nextFav) {
      // 跟伺服器最後認定的狀態對不上（例如兩個分頁同時操作），以伺服器為準校正。
      _setFavoriteState(category, symbol, data.favorited);
    }
  } catch (e) {
    _setFavoriteState(category, symbol, wasFav);
    showToast('網路錯誤，請稍後再試');
  } finally {
    btnEl.disabled = false;
  }
}

// 產生「名稱 + 星星」HTML，discover.js/rankings.js/deep_dive.js 共用。
// 呼叫前要先 await _ensureFavoritesCache()，不然星星狀態會一律顯示未收藏。
function renderAssetNameStar(ticker, displayName, nameClass = '') {
  const resolved = resolveAssetCategory(ticker);
  const key = resolved ? _favKey(resolved.category, resolved.symbol) : null;
  const isFav = !!(resolved && _favoritesCache && _favoritesCache.has(key));
  const safeName = escapeHtml(displayName || '');
  const safeTicker = (ticker || '').replace(/'/g, "\\'");
  const safeDisplay = safeName.replace(/'/g, "\\'");
  const star = resolved ? `
    <button type="button" data-fav-key="${key}" onclick="event.stopPropagation(); toggleFavoriteStar(this, '${resolved.category}', '${resolved.symbol}')"
      class="asset-star-btn inline-flex items-center justify-center w-6 h-6 flex-shrink-0 ${isFav ? 'text-[#d97f12]' : 'text-outline/40'} hover:opacity-70 transition-opacity">
      <span class="material-symbols-outlined text-base" style="font-variation-settings:'FILL' ${isFav ? 1 : 0}">star</span>
    </button>` : '';
  return `<span class="inline-flex items-center gap-1">
    <span class="${nameClass} cursor-pointer hover:underline decoration-dotted underline-offset-2" onclick="event.stopPropagation(); onAssetNameClick('${safeTicker}', '${safeDisplay}')">${safeName}</span>
    ${star}
  </span>`;
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
