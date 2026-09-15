// ── ASSETS 頁面：排名列表 + 標的詳情 ──────────────────────────────
// 詳情頁的股價走勢圖／新聞直接重用 calculator.js 的 renderStockChart()/fetchStockNews()，
// 只是傳自己的 asset- 前綴 DOM id，不重寫整份繪圖/新聞邏輯（見 calculator.js 的註解）。

const ASSET_CATEGORIES = [
  { key: 'tw_stock', label: '台股' },
  { key: 'tw_etf', label: '台股ETF' },
];

// 漲幅／跌幅都是 sort=change，只是 direction 不同，用 id 區分 UI 上要 highlight 哪個按鈕。
const ASSET_SORTS = [
  { id: 'volume', label: '成交量', sort: 'volume', direction: 'desc' },
  { id: 'price', label: '成交價', sort: 'price', direction: 'desc' },
  { id: 'change_desc', label: '漲幅', sort: 'change', direction: 'desc' },
  { id: 'change_asc', label: '跌幅', sort: 'change', direction: 'asc' },
];

let _assetsCategory = 'tw_stock';
let _assetsSortId = 'volume';
let _assetsSearchTimer = null;
let _assetsFetchSeq = 0;   // 過期回應防護，仿照 calculator.js 的 _scenarioFetchSeq 寫法
let _assetsRows = [];      // 目前排名列表資料，點擊項目時查詢用（不用另外打一次 API 拿名稱）

function initAssetsPage() {
  _renderAssetCategoryTabs();
  _renderAssetSortTabs();
  loadAssetRankings();
  loadAssetFavorites();
}

// 從別的分頁（Discover/Rankings/Deep Dive）切回 ASSETS 分頁時呼叫，見 app.js 的
// _renderPage()。使用者可能剛剛才在別的頁面收藏了新標的，這裡要重新抓一次，
// 不能一直用開機當下的舊快取。
function onAssetsPageShow() {
  loadAssetFavorites();
}

function _renderAssetCategoryTabs() {
  const el = document.getElementById('assets-category-tabs');
  if (!el) return;
  el.innerHTML = ASSET_CATEGORIES.map(c => {
    const active = c.key === _assetsCategory;
    const cls = active
      ? 'px-5 py-2 bg-tertiary-container text-white rounded-full font-label text-sm font-semibold whitespace-nowrap transition-colors'
      : 'px-5 py-2 bg-secondary/10 text-secondary rounded-full font-label text-sm font-semibold whitespace-nowrap border border-secondary/20 hover:bg-secondary/20 transition-colors';
    return `<button onclick="setAssetCategory('${c.key}')" class="${cls}">${c.label}</button>`;
  }).join('');
}

function _renderAssetSortTabs() {
  const el = document.getElementById('assets-sort-tabs');
  if (!el) return;
  el.innerHTML = ASSET_SORTS.map(s => {
    const active = s.id === _assetsSortId;
    const cls = active
      ? 'text-sm font-bold px-5 py-2.5 rounded-full bg-tertiary-container text-white transition-colors'
      : 'text-sm font-bold px-5 py-2.5 rounded-full bg-surface-container text-outline hover:bg-surface-container-high transition-colors';
    return `<button onclick="setAssetSort('${s.id}')" class="${cls}">${s.label}</button>`;
  }).join('');
}

function setAssetCategory(key) {
  if (key === _assetsCategory) return;
  _assetsCategory = key;
  _renderAssetCategoryTabs();
  loadAssetRankings();
  loadAssetFavorites();
}

function setAssetSort(id) {
  if (id === _assetsSortId) return;
  _assetsSortId = id;
  _renderAssetSortTabs();
  loadAssetRankings();
}

async function loadAssetRankings() {
  const seq = ++_assetsFetchSeq;
  const listEl = document.getElementById('assets-list');
  listEl.innerHTML = '<p class="text-outline text-sm py-8 text-center">載入中...</p>';

  const sortCfg = ASSET_SORTS.find(s => s.id === _assetsSortId) || ASSET_SORTS[0];
  try {
    const url = `/api/assets/rankings/?category=${_assetsCategory}&sort=${sortCfg.sort}&direction=${sortCfg.direction}&limit=50`;
    const [res] = await Promise.all([fetch(url), _ensureFavoritesCache()]);
    const data = await res.json();
    if (seq !== _assetsFetchSeq) return; // 已經有更新的請求發出，這筆回應過期了

    if (!res.ok) {
      listEl.innerHTML = `<p class="text-error text-sm py-8 text-center">${data.error || '載入失敗'}</p>`;
      document.getElementById('assets-as-of-date').textContent = '';
      return;
    }

    _assetsRows = data.data || [];
    document.getElementById('assets-as-of-date').textContent =
      data.as_of_date ? `資料日期：${data.as_of_date}（共 ${data.count} 檔）` : '';
    _renderAssetList(_assetsRows);
  } catch (e) {
    if (seq !== _assetsFetchSeq) return;
    listEl.innerHTML = '<p class="text-error text-sm py-8 text-center">載入失敗，請稍後再試</p>';
  }
}

function _fmtVolume(v) {
  if (v == null) return '—';
  if (v >= 100000000) return (v / 100000000).toFixed(2) + '億';
  if (v >= 10000) return (v / 10000).toFixed(1) + '萬';
  return v.toLocaleString();
}

// 主排名列表跟「我的最愛」區塊共用同一份卡片樣式，只有 rankLabel（序號 vs ★）
// 跟點擊行為不一樣，避免兩份重複的 HTML。data-row-fav-key 讓「我的最愛」清單
// 可以在收藏/取消收藏時直接找到整列做新增/移除，不用重新打 API。
function _assetStarButton(category, symbol) {
  const key = _favKey(category, symbol);
  const isFav = !!(_favoritesCache && _favoritesCache.has(key));
  return `<button type="button" data-fav-key="${key}" onclick="event.stopPropagation(); toggleFavoriteStar(this, '${category}', '${symbol}')"
    class="asset-star-btn inline-flex items-center justify-center w-6 h-6 flex-shrink-0 ${isFav ? 'text-[#d97f12]' : 'text-outline/40'} hover:opacity-70 transition-opacity">
    <span class="material-symbols-outlined text-base" style="font-variation-settings:'FILL' ${isFav ? 1 : 0}">star</span>
  </button>`;
}

function _assetRowHtml(r, rankLabel, onclickExpr) {
  const pct = r.change_pct;
  const changeAbs = r.change_abs;
  const isUp = pct != null && pct > 0;
  const isDown = pct != null && pct < 0;
  // 台股慣例是漲＝紅、跌＝綠，跟美股配色相反，不要套用美股那套紅跌綠漲的邏輯。
  const pctColor = pct == null ? 'text-outline' : (isUp ? 'text-[#ba1a1a]' : isDown ? 'text-[#1e8e3e]' : 'text-outline');
  const arrow = isUp ? '▲ ' : isDown ? '▼ ' : '';
  const changeLabel = pct == null
    ? '—'
    : `${arrow}${changeAbs != null ? Math.abs(changeAbs).toFixed(2) : '—'} (${Math.abs(pct).toFixed(2)}%)`;
  const rowKey = _favKey(_assetsCategory, r.symbol);
  return `
    <div data-row-fav-key="${rowKey}" onclick="${onclickExpr}" class="flex items-center gap-4 px-5 py-5 rounded-lg bg-surface-container-lowest hover:bg-surface-container-low transition-colors cursor-pointer border border-transparent hover:border-outline-variant/20">
      <div class="w-8 text-outline text-sm font-bold">${rankLabel}</div>
      <div class="flex-1 min-w-0 flex items-center gap-2">
        <div class="font-['Epilogue'] font-bold text-lg text-tertiary-container truncate">${escapeHtml(r.name || r.symbol)}</div>
        <div class="text-sm text-outline flex-shrink-0">${r.symbol}</div>
        ${_assetStarButton(_assetsCategory, r.symbol)}
      </div>
      <div class="flex items-baseline gap-2">
        <div class="font-['Epilogue'] font-bold text-lg text-on-surface">${r.close != null ? r.close.toFixed(2) : '—'}</div>
        <div class="text-sm font-bold ${pctColor} whitespace-nowrap">${changeLabel}</div>
      </div>
      <div class="hidden sm:flex items-baseline gap-1.5 ml-3">
        <span class="text-sm text-outline">成交量</span>
        <span class="text-sm font-semibold text-on-surface-variant">${_fmtVolume(r.volume)}</span>
      </div>
      <span class="material-symbols-outlined text-outline/40 text-xl flex-shrink-0">chevron_right</span>
    </div>`;
}

function _renderAssetList(rows) {
  const listEl = document.getElementById('assets-list');
  if (!rows.length) {
    listEl.innerHTML = '<p class="text-outline text-sm py-8 text-center">目前沒有資料</p>';
    return;
  }
  listEl.innerHTML = rows.map((r, i) => _assetRowHtml(r, i + 1, `openAssetDetail(${i})`)).join('');
}

// 給收藏清單／搜尋下拉建議共用：已經知道 symbol/category/name，直接開詳情頁，
// 不用像主列表那樣靠 index 去查 _assetsRows。
function openAssetDetailBySymbol(symbol, category, name) {
  openAssetDetail({ symbol, category, name });
}

// ── 搜尋下拉建議 ──────────────────────────────────────────────
// 跟 discover.js 的搜尋下拉是同一套模式：輸入時debounce查詢、結果顯示在輸入框
// 正下方的浮動清單，不影響下面主排名列表的排序/內容；點結果直接跳進詳情頁。
function onAssetSearchInput() {
  const q = (document.getElementById('assets-search')?.value || '').trim();
  const dd = document.getElementById('assets-search-dropdown');
  clearTimeout(_assetsSearchTimer);
  if (!q) { dd.classList.add('hidden'); dd.innerHTML = ''; return; }
  _assetsSearchTimer = setTimeout(() => _runAssetSearch(q), 300);
}

function onAssetSearchKeydown(e) {
  if (e.key === 'Escape') _closeAssetSearch();
}

function _closeAssetSearch() {
  document.getElementById('assets-search-dropdown').classList.add('hidden');
  document.getElementById('assets-search').value = '';
}

// 搜尋不分「台股」／「台股ETF」目前選哪個 tab，兩個分類都查、結果混在一起顯示
// （並各自標註分類），不然使用者在台股 tab 搜 0050 會因為分類被鎖死而找不到結果。
async function _runAssetSearch(q) {
  const dd = document.getElementById('assets-search-dropdown');
  dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">搜尋中...</p>';
  dd.classList.remove('hidden');
  try {
    const results = await Promise.all(ASSET_CATEGORIES.map(async c => {
      const url = `/api/assets/rankings/?category=${c.key}&q=${encodeURIComponent(q)}&sort=volume&direction=desc&limit=8`;
      const res = await fetch(url);
      const data = await res.json();
      return { category: c.key, ok: res.ok, data, rows: res.ok ? (data.data || []) : [] };
    }));

    if (results.every(r => !r.ok)) {
      dd.innerHTML = `<p class="text-error text-sm p-4 text-center">${results[0].data.error || '搜尋失敗'}</p>`;
      return;
    }

    const rows = results
      .flatMap(r => r.rows.map(row => ({ ...row, category: r.category })))
      .sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0))
      .slice(0, 8);

    if (!rows.length) {
      dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">找不到相關標的</p>';
      return;
    }
    dd.innerHTML = rows.map(r => {
      const name = (r.name || r.symbol).replace(/'/g, "\\'");
      const categoryLabel = ASSET_CATEGORIES.find(c => c.key === r.category)?.label || r.category;
      const pct = r.change_pct;
      const isUp = pct != null && pct > 0;
      const isDown = pct != null && pct < 0;
      const pctColor = pct == null ? 'text-outline' : (isUp ? 'text-[#ba1a1a]' : isDown ? 'text-[#1e8e3e]' : 'text-outline');
      const pctLabel = pct == null ? '—' : `${isUp ? '▲' : isDown ? '▼' : ''} ${Math.abs(pct).toFixed(2)}%`;
      return `
        <div onclick="openAssetDetailBySymbol('${r.symbol}', '${r.category}', '${name}'); _closeAssetSearch();"
          class="flex items-center justify-between gap-3 px-4 py-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
          <div class="flex items-baseline gap-2 min-w-0">
            <span class="text-[10px] font-bold text-outline uppercase tracking-wider flex-shrink-0 px-1.5 py-0.5 rounded bg-surface-container-highest">${categoryLabel}</span>
            <span class="text-base font-bold text-tertiary-container truncate">${r.name || r.symbol}</span>
            <span class="text-sm text-outline flex-shrink-0">${r.symbol}</span>
          </div>
          <div class="flex items-baseline gap-2 flex-shrink-0">
            <span class="text-base font-bold text-on-surface">${r.close != null ? r.close.toFixed(2) : '—'}</span>
            <span class="text-sm font-semibold ${pctColor} whitespace-nowrap">${pctLabel}</span>
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    dd.innerHTML = '<p class="text-error text-sm p-4 text-center">搜尋失敗，請稍後再試</p>';
  }
}

document.addEventListener('click', (e) => {
  const dd = document.getElementById('assets-search-dropdown');
  const inp = document.getElementById('assets-search');
  if (dd && inp && !dd.contains(e.target) && e.target !== inp) dd.classList.add('hidden');
});

const _FAV_EMPTY_HTML = '<p class="text-outline text-sm py-4 text-center">還沒有收藏的標的，點標的名稱旁邊的星號開始收藏</p>';

// 「我的最愛」區塊固定顯示（不管有沒有收藏都在），沒有收藏時顯示空狀態提示，
// 不要整個區塊消失不見——不然使用者會以為這個功能不存在。
async function loadAssetFavorites() {
  const listEl = document.getElementById('assets-favorites-list');
  if (!listEl) return;

  const favs = await _ensureFavoritesCache();
  const symbols = [...favs]
    .filter(k => k.startsWith(_assetsCategory + ':'))
    .map(k => k.split(':')[1]);

  if (!symbols.length) { listEl.innerHTML = _FAV_EMPTY_HTML; return; }
  listEl.innerHTML = '<p class="text-outline text-sm py-4 text-center">載入中...</p>';

  try {
    const url = `/api/assets/rankings/?category=${_assetsCategory}&symbols=${symbols.map(encodeURIComponent).join(',')}&sort=volume&direction=desc&limit=${symbols.length}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      listEl.innerHTML = `<p class="text-error text-sm py-4 text-center">${data.error || '載入失敗'}</p>`;
      return;
    }
    const rows = data.data || [];
    if (!rows.length) { listEl.innerHTML = _FAV_EMPTY_HTML; return; }
    listEl.innerHTML = rows.map(r => {
      const name = (r.name || r.symbol).replace(/'/g, "\\'");
      return _assetRowHtml(r, '★', `openAssetDetailBySymbol('${r.symbol}', '${_assetsCategory}', '${name}')`);
    }).join('');
  } catch (e) {
    listEl.innerHTML = '<p class="text-error text-sm py-4 text-center">載入失敗，請稍後再試</p>';
  }
}

// 星星狀態改變時（見 app.js 的 toggleFavoriteStar/_setFavoriteState），
// 「我的最愛」清單要立刻跟著加/移除那一列，不用等使用者離開再回來才刷新。
window.addEventListener('pod2invest:favorite-toggled', (e) => {
  const { category, symbol, favorited } = e.detail;
  if (category !== _assetsCategory) return; // 不是目前顯示的分類，這裡不用動
  if (favorited) _addFavoriteRowIfKnown(symbol);
  else _removeFavoriteRow(category, symbol);
});

function _removeFavoriteRow(category, symbol) {
  const listEl = document.getElementById('assets-favorites-list');
  if (!listEl) return;
  const key = _favKey(category, symbol);
  listEl.querySelectorAll(`[data-row-fav-key="${key}"]`).forEach(el => el.remove());
  if (!listEl.querySelector('[data-row-fav-key]')) listEl.innerHTML = _FAV_EMPTY_HTML;
}

function _addFavoriteRowIfKnown(symbol) {
  const listEl = document.getElementById('assets-favorites-list');
  if (!listEl) return;
  const key = _favKey(_assetsCategory, symbol);
  if (listEl.querySelector(`[data-row-fav-key="${key}"]`)) return; // 已經在清單裡了

  // 優先用目前排名列表／詳情頁已經有的資料直接插入，不用等重新打 API 才看得到；
  // 兩邊都沒有這支標的的資料（例如收藏動作是從別的頁面點的）才退回整個重新整理。
  let row = _assetsRows.find(r => r.symbol === symbol);
  if (!row && _assetDetailSymbol === symbol) {
    row = {
      symbol,
      name: document.getElementById('asset-detail-name').textContent,
      close: null, volume: null, change_pct: null,
    };
  }
  if (!row) { loadAssetFavorites(); return; }

  if (!listEl.querySelector('[data-row-fav-key]')) listEl.innerHTML = ''; // 清掉空狀態提示
  const name = (row.name || row.symbol).replace(/'/g, "\\'");
  const html = _assetRowHtml(row, '★', `openAssetDetailBySymbol('${row.symbol}', '${_assetsCategory}', '${name}')`);
  listEl.insertAdjacentHTML('afterbegin', html);
}

// ── 標的詳情 ──────────────────────────────────────────────────
let _assetDetailCategory = null;
let _assetDetailSymbol = null;
let _assetDetailFetchSeq = 0;

const ASSET_CHART_IDS = {
  svg: 'asset-stock-svg', placeholder: 'asset-stock-chart-placeholder',
  name: 'asset-stock-name', lastPrice: 'asset-stock-last-price',
  subtitle: 'asset-stock-chart-subtitle', change: 'asset-stock-change',
  histReturn: 'asset-stock-hist-return', info: 'asset-stock-info',
  dataAsof: 'asset-stock-data-asof',
};
const ASSET_NEWS_IDS = { section: 'asset-stock-news-section', list: 'asset-stock-news-list' };

// 詳情頁右上角的收藏星號是固定的 DOM 元素（不像列表是每列各自渲染），
// 所以每次開一支新標的的詳情頁都要重新設定它對應的 category/symbol 跟目前收藏狀態。
// data-fav-key 設好之後，toggleFavoriteStar() 本來就會自動更新所有同 key 的元素
// （見 app.js），這裡不用另外處理點擊後的畫面更新。
function _updateAssetDetailStar(category, symbol) {
  const btn = document.getElementById('asset-detail-star-btn');
  if (!btn) return;
  const key = _favKey(category, symbol);
  btn.setAttribute('data-fav-key', key);
  btn.onclick = () => toggleFavoriteStar(btn, category, symbol);

  const paint = () => {
    const isFav = !!(_favoritesCache && _favoritesCache.has(key));
    const icon = btn.querySelector('.material-symbols-outlined');
    if (icon) icon.style.fontVariationSettings = `'FILL' ${isFav ? 1 : 0}`;
    btn.classList.toggle('text-[#d97f12]', isFav);
    btn.classList.toggle('text-outline/40', !isFav);
  };
  paint();
  _ensureFavoritesCache().then(paint); // 快取還沒載完時先顯示未收藏，載完後再刷新一次
}

// indexOrAsset 可以是「目前排名列表裡的 index」（原本的用法，維持不變），
// 也可以是 { symbol, category, name } 物件——讓 Discover/Rankings/Deep Dive
// 這些沒有 _assetsRows 狀態的頁面也能直接開某支標的的詳情頁。
function openAssetDetail(indexOrAsset) {
  const row = (typeof indexOrAsset === 'object' && indexOrAsset !== null)
    ? indexOrAsset
    : _assetsRows[indexOrAsset];
  if (!row || !row.symbol) return;

  const category = row.category || _assetsCategory;
  if (category !== _assetsCategory) {
    _assetsCategory = category;
    _renderAssetCategoryTabs();   // 從別的頁面跳進來時，讓列表頁的分類 tab 狀態同步，
                                   // 使用者按「返回列表」時看到的分類才會跟剛剛開的詳情頁一致
  }
  _assetDetailCategory = category;
  _assetDetailSymbol = row.symbol;

  document.getElementById('assets-list-view').classList.add('hidden');
  document.getElementById('assets-detail-view').classList.remove('hidden');
  document.getElementById('asset-detail-name').textContent = row.name || row.symbol;
  document.getElementById('asset-detail-symbol').textContent = row.symbol;
  _updateAssetDetailStar(category, row.symbol);

  document.getElementById('asset-basic-info').classList.add('hidden');
  document.getElementById('asset-basic-info-status').textContent = '載入中...';

  _loadAssetBasicInfo(_assetDetailCategory, _assetDetailSymbol);
  _highlightAssetPeriodBtn('1y');
  _fetchAndRenderAssetChart(row.symbol, '1y');
  fetchStockNews(`${row.symbol}.TW`, ASSET_NEWS_IDS);
  window.scrollTo(0, 0);
}

function closeAssetDetail() {
  document.getElementById('assets-detail-view').classList.add('hidden');
  document.getElementById('assets-list-view').classList.remove('hidden');
  // 如果是從別的頁面（例如首頁搜尋結果）跳進來看這支標的，_backOverride 會被設成原本
  // 那個頁面——這裡要直接跳回去，不能只收合成本頁清單，不然使用者會卡在 Assets 排行榜，
  // 回不去剛剛的搜尋結果。正常從 Assets 頁面自己點進詳情頁的情況不會設 _backOverride，
  // 行為維持原樣（就只是收合成清單）。
  if (_backOverride) goBack();
}

function setAssetStockPeriod(period) {
  _highlightAssetPeriodBtn(period);
  if (_assetDetailSymbol) _fetchAndRenderAssetChart(_assetDetailSymbol, period);
}

function _highlightAssetPeriodBtn(period) {
  document.querySelectorAll('.asset-stock-period-btn').forEach(btn => {
    btn.className = btn.dataset.period === period
      ? 'asset-stock-period-btn px-3 py-1.5 rounded-full font-label text-xs font-bold transition-all bg-tertiary-container text-white'
      : 'asset-stock-period-btn px-3 py-1.5 rounded-full font-label text-xs font-bold transition-all text-secondary';
  });
}

async function _fetchAndRenderAssetChart(symbol, period) {
  const seq = ++_assetDetailFetchSeq;
  document.getElementById('asset-stock-chart-placeholder').textContent = '載入中...';
  document.getElementById('asset-stock-chart-placeholder').classList.remove('hidden');
  document.getElementById('asset-stock-svg').classList.add('hidden');
  try {
    const ticker = `${symbol}.TW`;
    const res = await fetch(`/api/calculator/stock-chart/?ticker=${encodeURIComponent(ticker)}&period=${period}`);
    const data = await res.json();
    if (seq !== _assetDetailFetchSeq) return; // 使用者切了別的期間/標的，這筆回應過期了
    if (!res.ok) {
      document.getElementById('asset-stock-chart-placeholder').textContent = data.error || '載入失敗';
      return;
    }
    renderStockChart(data, ASSET_CHART_IDS);
  } catch (e) {
    if (seq !== _assetDetailFetchSeq) return;
    document.getElementById('asset-stock-chart-placeholder').textContent = '載入失敗，請稍後再試';
  }
}

function _fmtNum(v, digits = 2) {
  return v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
}

// 市值原始數字對台股大型股（動輒兆元）來說位數太多不好讀，改用中文單位簡寫。
function _fmtMarketCap(v) {
  if (v == null) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return (v / 1e12).toFixed(2) + '兆';
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '億';
  if (abs >= 1e4) return (v / 1e4).toFixed(1) + '萬';
  return v.toLocaleString();
}

function _assetInfoTile(label, value, sub = '', valueSizeCls = 'text-lg') {
  // 同一列裡有的格子多一行提示（sub）、有的沒有，grid 會把每列撐到最高格子的高度；
  // 標題一律留在頂端對齊，沒有 sub 的格子則把數值置中在標題下方剩餘的空間裡，
  // 不然數值會看起來卡在上緣、下面留一大塊空白。
  const valueBlock = sub
    ? `<div class="${valueSizeCls} font-bold text-tertiary-container">${value}</div>${sub}`
    : `<div class="flex-1 flex flex-col justify-center"><div class="${valueSizeCls} font-bold text-tertiary-container">${value}</div></div>`;
  return `<div class="bg-surface-container-lowest rounded-lg p-4 text-center flex flex-col">
    <div class="text-xs text-outline font-bold uppercase tracking-widest mb-1.5">${label}</div>
    ${valueBlock}
  </div>`;
}

// 台股慣例是漲／增＝紅、跌／減＝綠，跟 _assetRowHtml 的 pctColor 用同一套配色，
// 讓市值年變化、EPS年增率、營收成長這些提示跟排名列表的漲跌顏色是同一套邏輯。
function _trendArrowColor(value) {
  if (value == null) return { arrow: '', color: 'text-outline' };
  if (value > 0) return { arrow: '▲', color: 'text-[#ba1a1a]' };
  if (value < 0) return { arrow: '▼', color: 'text-[#1e8e3e]' };
  return { arrow: '', color: 'text-outline' };
}

function _marketCapSubline(d) {
  if (d.market_cap_change_1y == null) return '';
  const { arrow, color } = _trendArrowColor(d.market_cap_change_1y);
  const pct = Math.abs(d.market_cap_change_1y * 100).toFixed(1);
  return `<div class="text-xs font-semibold mt-1"><span class="text-outline font-normal">近一年</span> <span class="${color}">${arrow} ${pct}%</span></div>`;
}

function _epsSubline(d) {
  const parts = [];
  if (d.eps_yoy_change != null) {
    const { arrow, color } = _trendArrowColor(d.eps_yoy_change);
    const pct = Math.abs(d.eps_yoy_change * 100).toFixed(1);
    parts.push(`<span class="text-outline font-normal">近一年</span> <span class="${color}">${arrow} ${pct}%</span>`);
  }
  if (d.eps != null && d.eps < 0) {
    parts.push(`<span class="text-error font-bold">虧損</span>`);
  }
  if (!parts.length) return '';
  return `<div class="text-xs font-semibold mt-1">${parts.join(' ')}</div>`;
}

function _revenueGrowthMainValue(d) {
  if (d.revenue_growth == null) return '—';
  const pct = Number(d.revenue_growth) * 100;
  const sign = pct > 0 ? '+' : '';
  const { color } = _trendArrowColor(d.revenue_growth);
  return `<span class="${color}">${sign}${pct.toFixed(1)}%</span>`;
}

// 跟同產業基準比，在 ±10% 以內算「接近」，避免兩個數字只差一點點卻被貼上「高於/低於」的標籤。
function _industryCompareLabel(value, benchmark) {
  if (value == null || !benchmark || benchmark.value == null) return null;
  const diffRatio = (value - benchmark.value) / benchmark.value;
  if (Math.abs(diffRatio) <= 0.1) return { arrow: '', color: 'text-outline', text: '接近產業平均' };
  return diffRatio > 0
    ? { arrow: '▲', color: 'text-[#ba1a1a]', text: '高於產業平均' }
    : { arrow: '▼', color: 'text-[#1e8e3e]', text: '低於產業平均' };
}

function _peSubline(d) {
  const cmp = _industryCompareLabel(d.pe_ratio, d.pe_industry_benchmark);
  if (!cmp) return '';
  return `<div class="text-xs font-semibold mt-1 ${cmp.color}">${cmp.arrow ? cmp.arrow + ' ' : ''}${cmp.text}</div>`;
}

// 金融業不適用的說明泡泡改用滑入/滑出顯示，且用 fixed 定位 + 動態掛到 <body> 下，
// 跳脫負債比格子本身（那格外面還包著「查看更多」的滑出動畫容器，設了 overflow-hidden
// 才能做出收合效果，泡泡如果還是格子內的子元素會被這層 overflow-hidden 裁掉），
// 也不會撐大負債比那格的高度。
function showDebtRatioFinanceNote(event) {
  let note = document.getElementById('debt-ratio-finance-note');
  if (!note) {
    note = document.createElement('div');
    note.id = 'debt-ratio-finance-note';
    note.className = 'hidden fixed z-50 w-80 max-w-[calc(100vw-16px)] text-sm text-left bg-surface-container-high rounded-lg p-4 text-on-surface-variant leading-relaxed shadow-lg pointer-events-none';
    note.innerHTML = `
      <div id="debt-ratio-finance-note-arrow" class="absolute -top-1 -translate-x-1/2 w-2 h-2 bg-surface-container-high rotate-45"></div>
      銀行、保險、金控等金融業的負債比天生偏高（存款、保單準備金在會計上都算負債），不適合跟一般產業比較。
    `;
    document.body.appendChild(note);
  }
  // 視窗沒展開全螢幕時，泡泡如果還是固定用觸發元素置中會超出畫面右（或左）邊，
  // 所以改成量完泡泡實際寬度後夾在視窗範圍內，箭頭再另外算偏移量跟著指回觸發文字。
  note.classList.remove('hidden');
  const rect = event.currentTarget.getBoundingClientRect();
  const margin = 8;
  const noteWidth = note.offsetWidth;
  const centerX = rect.left + rect.width / 2;
  const left = Math.max(margin, Math.min(centerX - noteWidth / 2, window.innerWidth - noteWidth - margin));
  note.style.left = `${left}px`;
  note.style.top = `${rect.bottom + 8}px`;
  note.style.transform = 'none';

  const arrow = document.getElementById('debt-ratio-finance-note-arrow');
  if (arrow) {
    arrow.style.left = `${Math.max(12, Math.min(centerX - left, noteWidth - 12))}px`;
  }
}

function hideDebtRatioFinanceNote() {
  const note = document.getElementById('debt-ratio-finance-note');
  if (note) note.classList.add('hidden');
}

function _debtRatioSubline(d) {
  if (d.is_finance_industry) {
    return `<div class="mt-1">
      <span class="text-xs font-semibold text-outline inline-flex items-center justify-center gap-0.5 cursor-help"
        onmouseenter="showDebtRatioFinanceNote(event)" onmouseleave="hideDebtRatioFinanceNote()">
        金融業不適用<span class="material-symbols-outlined text-sm leading-none">info</span>
      </span>
    </div>`;
  }
  const cmp = _industryCompareLabel(d.debt_ratio, d.debt_ratio_industry_benchmark);
  if (!cmp) return '';
  return `<div class="text-xs font-semibold mt-1 ${cmp.color}">${cmp.arrow ? cmp.arrow + ' ' : ''}${cmp.text}</div>`;
}

function _assetEtfClassificationValue(d) {
  // 分類先靠 etf_classification.py 的手動對照表（etfdb 撈不到完整清單前的過渡做法），
  // 兩個維度都有值才疊兩行顯示，只有一個就單行顯示，都沒有就是「—」。
  if (d.strategy_type && d.theme) {
    return `${d.strategy_type}<div class="text-xs font-normal normal-case tracking-normal text-outline mt-1">${d.theme}</div>`;
  }
  return d.strategy_type || d.theme || '—';
}

// 個股基本資料第二排（營收成長率／本益比／負債比）預設收合，只留產業／市值／EPS
// 這種第一眼最想看的資訊，其餘靠「查看更多」按鈕展開，避免詳情頁一開就塞六格資訊。
function _assetBasicInfoToggleBtn() {
  return `<button type="button" onclick="toggleAssetBasicInfoMore()"
    class="w-full flex items-center justify-center gap-1 py-2 mt-2 text-sm font-semibold text-outline hover:text-tertiary-container transition-colors">
    <span id="asset-basic-info-toggle-label">查看更多</span>
    <span class="material-symbols-outlined text-lg" id="asset-basic-info-toggle-icon">expand_more</span>
  </button>`;
}

// 用 CSS grid 的 grid-template-rows: 0fr → 1fr 做滑出效果，不用 JS 量測高度；
// 外層 wrap 負責動畫，裡面再包一層 overflow-hidden 才能在 0fr 時真的把內容裁掉。
function toggleAssetBasicInfoMore() {
  const wrap = document.getElementById('asset-basic-info-row2-wrap');
  const label = document.getElementById('asset-basic-info-toggle-label');
  const icon = document.getElementById('asset-basic-info-toggle-icon');
  if (!wrap) return;
  const willExpand = wrap.classList.contains('grid-rows-[0fr]');
  wrap.classList.toggle('grid-rows-[0fr]', !willExpand);
  wrap.classList.toggle('grid-rows-[1fr]', willExpand);
  label.textContent = willExpand ? '收起清單' : '查看更多';
  icon.textContent = willExpand ? 'expand_less' : 'expand_more';
}

function _renderAssetBasicInfoTiles(category, d) {
  if (category === 'tw_etf') {
    return `<div class="grid grid-cols-3 gap-3">${[
      _assetInfoTile('分類', _assetEtfClassificationValue(d)),
      _assetInfoTile('追蹤指數', d.tracking_index_name || '—'),
      _assetInfoTile('配息政策', d.distribution_policy || '—'),
      _assetInfoTile('規模 (AUM)', d.aum != null ? _fmtNum(d.aum, 0) : '—'),
      _assetInfoTile('總開支率 TER', d.ter != null ? d.ter.toFixed(2) + '%' : '—'),
      _assetInfoTile('經理費', d.mgmt_fee != null ? d.mgmt_fee.toFixed(2) + '%' : '—'),
      _assetInfoTile('保管費', d.custody_fee != null ? d.custody_fee.toFixed(2) + '%' : '—'),
      _assetInfoTile('成立日', d.inception_date || '—'),
    ].join('')}</div>`;
  }
  const row1 = [
    _assetInfoTile('產業', d.industry || d.sector || '—', '', 'text-xl'),
    _assetInfoTile(
      '市值',
      d.market_cap != null
        ? `<span class="text-outline text-sm align-middle mr-1">NT$</span>${_fmtMarketCap(d.market_cap)}`
        : '—',
      _marketCapSubline(d)
    ),
    _assetInfoTile('EPS', d.eps != null ? Number(d.eps).toFixed(2) : '—', _epsSubline(d)),
  ].join('');
  const row2 = [
    _assetInfoTile('營收成長率', _revenueGrowthMainValue(d), '', 'text-xl'),
    _assetInfoTile('本益比', d.pe_ratio != null ? Number(d.pe_ratio).toFixed(1) : '—', _peSubline(d)),
    _assetInfoTile('負債比', d.debt_ratio != null ? Number(d.debt_ratio).toFixed(1) + '%' : '—', _debtRatioSubline(d)),
  ].join('');
  return `<div class="grid grid-cols-3 gap-3">${row1}</div>
    <div id="asset-basic-info-row2-wrap" class="grid grid-rows-[0fr] transition-[grid-template-rows] duration-300 ease-in-out">
      <div class="overflow-hidden">
        <div class="grid grid-cols-3 gap-3 pt-3">${row2}</div>
      </div>
    </div>
    ${_assetBasicInfoToggleBtn()}`;
}

async function _loadAssetBasicInfo(category, symbol) {
  const box = document.getElementById('asset-basic-info');
  const status = document.getElementById('asset-basic-info-status');
  try {
    const res = await fetch(`/api/assets/basic-info/?category=${category}&symbol=${encodeURIComponent(symbol)}`);
    const data = await res.json();
    if (!res.ok) {
      status.textContent = data.error || '無法取得基本資料';
      return;
    }
    status.textContent = '';
    box.innerHTML = _renderAssetBasicInfoTiles(category, data);
    box.classList.remove('hidden');
  } catch (e) {
    status.textContent = '無法取得基本資料';
  }
}
