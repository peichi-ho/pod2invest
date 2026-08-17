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
      <div class="flex-1 min-w-0">
        <div class="font-['Epilogue'] font-bold text-lg text-tertiary-container truncate">${renderAssetNameStar(`${r.symbol}.TW`, r.name || r.symbol)}</div>
        <div class="text-sm text-outline">${r.symbol}</div>
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

async function _runAssetSearch(q) {
  const dd = document.getElementById('assets-search-dropdown');
  dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">搜尋中...</p>';
  dd.classList.remove('hidden');
  try {
    const url = `/api/assets/rankings/?category=${_assetsCategory}&q=${encodeURIComponent(q)}&sort=volume&direction=desc&limit=8`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      dd.innerHTML = `<p class="text-error text-sm p-4 text-center">${data.error || '搜尋失敗'}</p>`;
      return;
    }
    const rows = data.data || [];
    if (!rows.length) {
      dd.innerHTML = '<p class="text-outline text-sm p-4 text-center">找不到相關標的</p>';
      return;
    }
    dd.innerHTML = rows.map(r => {
      const name = (r.name || r.symbol).replace(/'/g, "\\'");
      const pct = r.change_pct;
      const isUp = pct != null && pct > 0;
      const isDown = pct != null && pct < 0;
      const pctColor = pct == null ? 'text-outline' : (isUp ? 'text-[#ba1a1a]' : isDown ? 'text-[#1e8e3e]' : 'text-outline');
      const pctLabel = pct == null ? '—' : `${isUp ? '▲' : isDown ? '▼' : ''} ${Math.abs(pct).toFixed(2)}%`;
      return `
        <div onclick="openAssetDetailBySymbol('${r.symbol}', '${_assetsCategory}', '${name}'); _closeAssetSearch();"
          class="flex items-center justify-between gap-3 px-4 py-4 hover:bg-surface-container-highest cursor-pointer border-b border-outline-variant/10 last:border-0 transition-colors">
          <div class="flex items-baseline gap-2 min-w-0">
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

function _assetInfoTile(label, value) {
  return `<div class="bg-surface-container-lowest rounded-lg p-4 text-center">
    <div class="text-xs text-outline font-bold uppercase tracking-widest mb-1.5">${label}</div>
    <div class="text-lg font-bold text-tertiary-container">${value}</div>
  </div>`;
}

function _renderAssetBasicInfoTiles(category, d) {
  if (category === 'tw_etf') {
    return [
      _assetInfoTile('追蹤指數', d.tracking_index_name || '—'),
      _assetInfoTile('配息政策', d.distribution_policy || '—'),
      _assetInfoTile('規模 (AUM)', d.aum != null ? _fmtNum(d.aum, 0) : '—'),
      _assetInfoTile('總開支率 TER', d.ter != null ? d.ter.toFixed(2) + '%' : '—'),
      _assetInfoTile('經理費', d.mgmt_fee != null ? d.mgmt_fee.toFixed(2) + '%' : '—'),
      _assetInfoTile('保管費', d.custody_fee != null ? d.custody_fee.toFixed(2) + '%' : '—'),
      _assetInfoTile('成立日', d.inception_date || '—'),
    ].join('');
  }
  return [
    _assetInfoTile('市值', d.market_cap != null ? _fmtNum(d.market_cap, 0) : '—'),
    _assetInfoTile('本益比', d.pe_ratio != null ? Number(d.pe_ratio).toFixed(1) : '—'),
    _assetInfoTile('殖利率', d.dividend_yield != null ? Number(d.dividend_yield).toFixed(2) + '%' : '—'),
    _assetInfoTile('52週高', d.week52_high != null ? Number(d.week52_high).toFixed(1) : '—'),
    _assetInfoTile('52週低', d.week52_low != null ? Number(d.week52_low).toFixed(1) : '—'),
    _assetInfoTile('產業', d.industry || d.sector || '—'),
  ].join('');
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
