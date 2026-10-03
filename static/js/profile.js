// ── Preference chip styling (共用給編輯模式的 5 個偏好分類) ──────
const _prefActiveClass  = 'pref-chip px-5 py-2 rounded-full border border-secondary bg-secondary/10 text-secondary font-label text-sm font-bold transition-all';
const _prefDefaultClass = 'pref-chip px-5 py-2 rounded-full border border-outline-variant/40 bg-surface-container text-on-surface-variant font-label text-sm font-semibold transition-all hover:border-secondary/40';

function togglePref(groupId, btn, mode) {
  const group = document.getElementById(groupId);
  if (mode === 'single') {
    group.querySelectorAll('.pref-chip').forEach(b => b.className = _prefDefaultClass);
    btn.className = _prefActiveClass;
  } else {
    btn.className = btn.className.includes('text-secondary') ? _prefDefaultClass : _prefActiveClass;
  }
}

// 跟 templates/accounts/onboarding.html 問卷用的是同一套欄位／選項值，這樣「先顯示
// 註冊時填的選項」才對得起來，而且首頁 discover.js 的 _prefScore() 讀的也是這幾個
// 欄位——欄位對不上的話，編輯偏好後首頁推薦不會真的跟著變。
const PREF_TAXONOMY = [
  { key: 'level', mode: 'single', label: '投資經驗', icon: 'school', options: [
      { val: 'Novice',       label: '投資小白',   emoji: '🌱' },
      { val: 'Intermediate', label: '有基本經驗', emoji: '📈' },
      { val: 'Expert',       label: '市場老手',   emoji: '🎯' },
  ]},
  { key: 'markets', mode: 'multi', label: '關注領域', icon: 'public', options: [
      { val: 'TW',      label: '台股',     emoji: '🇹🇼' },
      { val: 'US',      label: '美股',     emoji: '🇺🇸' },
      { val: 'Crypto',  label: '加密貨幣', emoji: '₿' },
      { val: 'General', label: '還不確定', emoji: '🌍' },
  ]},
  { key: 'style', mode: 'single', label: '分析方法', icon: 'analytics', options: [
      { val: 'Technical',    label: 'K 線與成交量',       emoji: '📊' },
      { val: 'Fundamental',  label: '財報與公司體質',     emoji: '📋' },
      { val: 'Macro',        label: '國際局勢與政策',     emoji: '🌐' },
      { val: 'Passive',      label: '聽專家的／跟著大盤', emoji: '🤝' },
  ]},
  { key: 'goal', mode: 'single', label: '獲利模式', icon: 'trending_up', options: [
      { val: 'Growth',   label: '低買高賣賺價差', emoji: '🚀' },
      { val: 'Dividend', label: '穩定領股息',     emoji: '💰' },
      { val: 'Passive',  label: '長期持有大盤',   emoji: '🏔️' },
  ]},
  { key: 'capital', mode: 'single', label: '資金規模', icon: 'payments', options: [
      { val: 'Low',    label: '5,000 元以下',       emoji: '🌱' },
      { val: 'Medium', label: '5,000～30,000 元',   emoji: '📦' },
      { val: 'High',   label: '30,000 元以上',      emoji: '💼' },
  ]},
];

let _prefsData = null;
let _prefsEditing = false;

async function loadPrefsSection() {
  try {
    const res = await fetch('/api/accounts/preferences/');
    _prefsData = res.ok ? await res.json() : {};
  } catch (e) {
    _prefsData = {};
  }
  _prefsEditing = false;
  renderPrefsSection();
}

function renderPrefsSection() {
  const el      = document.getElementById('prefs-groups');
  const saveBtn = document.getElementById('prefs-save-btn');
  const editBtn = document.getElementById('prefs-edit-btn');
  if (!el) return;
  saveBtn.classList.toggle('hidden', !_prefsEditing);
  editBtn.querySelector('.material-symbols-outlined').textContent = _prefsEditing ? 'close' : 'edit';

  el.innerHTML = PREF_TAXONOMY.map(group => {
    const current    = _prefsData ? _prefsData[group.key] : null;
    const currentSet = group.mode === 'multi' ? new Set(current || []) : new Set(current ? [current] : []);

    if (!_prefsEditing) {
      const selected = group.options.filter(o => currentSet.has(o.val));
      const body = selected.length
        ? `<div class="flex gap-3 flex-wrap">${selected.map(o =>
            `<span class="px-5 py-2 rounded-full border border-secondary bg-secondary/10 text-secondary font-label text-sm font-bold">${o.emoji} ${o.label}</span>`
          ).join('')}</div>`
        : '<p class="text-outline text-sm">尚未設定</p>';
      return `
        <div class="bg-surface-container-lowest rounded-lg p-6 border border-outline-variant/10 space-y-4">
          <div class="flex items-center gap-2 mb-1">
            <span class="material-symbols-outlined text-secondary text-lg" style="font-variation-settings:'FILL' 1">${group.icon}</span>
            <h4 class="font-['Epilogue'] font-bold text-base text-tertiary-container">${group.label}</h4>
          </div>
          ${body}
        </div>`;
    }

    return `
      <div class="bg-surface-container-lowest rounded-lg p-6 border border-outline-variant/10 space-y-4">
        <div class="flex items-center gap-2 mb-1">
          <span class="material-symbols-outlined text-secondary text-lg" style="font-variation-settings:'FILL' 1">${group.icon}</span>
          <h4 class="font-['Epilogue'] font-bold text-base text-tertiary-container">${group.label}${group.mode === 'multi' ? ' <span class="text-xs text-outline font-normal">（可多選）</span>' : ''}</h4>
        </div>
        <div class="flex gap-3 flex-wrap" id="pref2-${group.key}">
          ${group.options.map(o =>
            `<button type="button" data-val="${o.val}" onclick="togglePref('pref2-${group.key}', this, '${group.mode}')" class="${currentSet.has(o.val) ? _prefActiveClass : _prefDefaultClass}">${o.emoji} ${o.label}</button>`
          ).join('')}
        </div>
      </div>`;
  }).join('');
}

function togglePrefsEditMode() {
  _prefsEditing = !_prefsEditing;
  renderPrefsSection();
}

async function savePreferences() {
  const payload = {};
  PREF_TAXONOMY.forEach(group => {
    const groupEl = document.getElementById('pref2-' + group.key);
    if (!groupEl) return;
    const selected = [...groupEl.querySelectorAll('.pref-chip')]
      .filter(b => b.className.includes('text-secondary'))
      .map(b => b.dataset.val);
    payload[group.key] = group.mode === 'multi' ? selected : (selected[0] || '');
  });

  try {
    const res = await fetch('/api/accounts/preferences/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) { showToast('儲存失敗，請稍後再試'); return; }
    _prefsData    = await res.json();
    _prefsEditing = false;
    renderPrefsSection();
    const msg = document.getElementById('pref-saved-msg');
    msg.classList.remove('hidden');
    setTimeout(() => msg.classList.add('hidden'), 3000);
  } catch (e) {
    showToast('網路錯誤，請稍後再試');
  }
}

// ── Profile header（大頭貼／使用者名稱／加入年月）─────────────────
function formatJoinDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return `Joined ${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function renderProfileAvatar() {
  const el = document.getElementById('profile-avatar-inner');
  if (!el || !_userProfile) return;
  el.innerHTML = _userProfile.avatar_base64
    ? `<img src="${_userProfile.avatar_base64}" alt="${escapeHtml(_userProfile.username || '')}" class="w-full h-full object-cover"/>`
    : `<div class="w-full h-full flex flex-col items-center justify-center gap-1" style="background:#d97f12"><span class="material-symbols-outlined text-white/70 text-3xl" style="font-variation-settings:'FILL' 1">person</span></div>`;
}

function renderProfileHeader() {
  if (!_userProfile) return;
  document.getElementById('profile-username').textContent = _userProfile.username || '';
  document.getElementById('profile-joined').textContent   = formatJoinDate(_userProfile.date_joined);
  renderProfileAvatar();
}

const AVATAR_MAX_BYTES = 2 * 1024 * 1024; // 前端先擋 2MB 原始檔案，avatar/ API 那邊還有一層寬鬆防呆

function onAvatarFileSelected(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = ''; // 清掉，讓使用者可以重選同一個檔案也會再觸發一次 onchange
  if (!file) return;
  if (!file.type.startsWith('image/')) { showToast('請選擇圖片檔案'); return; }
  if (file.size > AVATAR_MAX_BYTES) { showToast('圖片太大，請選擇 2MB 以內的圖片'); return; }

  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const res = await fetch('/api/accounts/avatar/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_base64: reader.result }),
      });
      const data = await res.json();
      if (!res.ok) { showToast(data.error || '上傳失敗，請稍後再試'); return; }
      if (_userProfile) _userProfile.avatar_base64 = data.avatar_base64;
      renderProfileAvatar();
      renderHeaderAvatar();
      showToast('頭像已更新');
    } catch (e) {
      showToast('網路錯誤，請稍後再試');
    }
  };
  reader.readAsDataURL(file);
}

// ── Edit Profile modal（姓名／Email／密碼）────────────────────
function _hideEl(id) { document.getElementById(id).classList.add('hidden'); }
function _showFieldError(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.classList.remove('hidden');
}

function openEditProfileModal() {
  document.getElementById('edit-username').value      = (_userProfile && _userProfile.username) || '';
  document.getElementById('edit-email').value         = (_userProfile && _userProfile.email) || '';
  document.getElementById('edit-old-password').value  = '';
  document.getElementById('edit-new-password').value  = '';
  document.getElementById('edit-new-password2').value = '';
  ['edit-profile-error', 'edit-profile-success', 'edit-password-error', 'edit-password-success'].forEach(_hideEl);
  document.getElementById('edit-profile-modal').classList.remove('hidden');
}

function closeEditProfileModal() {
  document.getElementById('edit-profile-modal').classList.add('hidden');
}

async function saveProfileInfo() {
  _hideEl('edit-profile-error'); _hideEl('edit-profile-success');
  const username = document.getElementById('edit-username').value.trim();
  const email    = document.getElementById('edit-email').value.trim();
  if (!username) { _showFieldError('edit-profile-error', '姓名不能為空'); return; }

  try {
    const res  = await fetch('/api/accounts/profile/', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email }),
    });
    const data = await res.json();
    if (!res.ok) { _showFieldError('edit-profile-error', data.error || '更新失敗'); return; }
    if (_userProfile) { _userProfile.username = data.username; _userProfile.email = data.email; }
    renderProfileHeader();
    renderHeaderAvatar();
    document.getElementById('edit-profile-success').classList.remove('hidden');
  } catch (e) {
    _showFieldError('edit-profile-error', '網路錯誤，請稍後再試');
  }
}

async function changePassword() {
  _hideEl('edit-password-error'); _hideEl('edit-password-success');
  const oldPw  = document.getElementById('edit-old-password').value;
  const newPw  = document.getElementById('edit-new-password').value;
  const newPw2 = document.getElementById('edit-new-password2').value;

  if (!oldPw || !newPw) { _showFieldError('edit-password-error', '請填寫原密碼和新密碼'); return; }
  if (newPw !== newPw2) { _showFieldError('edit-password-error', '兩次輸入的新密碼不一致'); return; }
  if (newPw.length < 8) { _showFieldError('edit-password-error', '新密碼至少需要 8 個字元'); return; }

  try {
    const res  = await fetch('/api/accounts/change-password/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
    });
    const data = await res.json();
    if (!res.ok) { _showFieldError('edit-password-error', data.error || '更新失敗'); return; }
    document.getElementById('edit-old-password').value  = '';
    document.getElementById('edit-new-password').value  = '';
    document.getElementById('edit-new-password2').value = '';
    document.getElementById('edit-password-success').classList.remove('hidden');
  } catch (e) {
    _showFieldError('edit-password-error', '網路錯誤，請稍後再試');
  }
}

// ── 觀看紀錄／收藏單集／最愛 Podcast：共用小工具 ──────────────────
async function _fetchSummariesByIds(ids) {
  if (!ids.length) return [];
  const res = await fetch(`/api/summaries/?ids=${ids.join(',')}`);
  if (!res.ok) return [];
  const list = await res.json();
  const byId = {};
  list.forEach(s => { byId[s.id] = s; });
  return ids.map(id => byId[id]).filter(Boolean); // 依原本順序（最新在前）排列
}

function _episodeTitle(s) {
  return (s.source_filename || '').replace(/\.srt$/i, '') || (s.one_sentence_summary || '').slice(0, 40);
}

function _savedEpisodeRowHtml(s) {
  const st      = cardStyle(s.podcaster || s.source_filename);
  const dateStr = (s.published_at || s.created_at || '').slice(0, 10);
  return `
    <div onclick="closeProfileListModal(); openDeepDive(${s.id})" class="group flex gap-4 p-4 rounded-lg bg-surface-container-lowest hover:bg-surface-container-low transition-colors cursor-pointer border border-transparent hover:border-outline-variant/20">
      <div class="w-20 h-20 rounded bg-surface-container overflow-hidden shrink-0">${podcastAvatar(s.podcaster, st.bg, st.icon)}</div>
      <div class="flex flex-col justify-center min-w-0">
        <span class="text-[10px] font-['Epilogue'] font-bold text-on-primary-container uppercase tracking-widest truncate">${escapeHtml(s.podcaster || '')}</span>
        <h4 class="font-['Epilogue'] font-bold text-tertiary-container group-hover:text-secondary transition-colors line-clamp-1">${escapeHtml(_episodeTitle(s))}</h4>
        <p class="text-sm text-on-surface-variant line-clamp-1">${dateStr}</p>
      </div>
    </div>`;
}

function _historyCardHtml(s) {
  const st = cardStyle(s.podcaster || s.source_filename);
  return `
    <div onclick="closeProfileListModal(); openDeepDive(${s.id})" class="flex-shrink-0 w-40 cursor-pointer group">
      <div class="w-40 h-28 rounded-lg overflow-hidden bg-surface-container-low mb-2">${podcastAvatar(s.podcaster, st.bg, st.icon)}</div>
      <p class="text-[10px] font-bold text-on-primary-container uppercase tracking-widest truncate">${escapeHtml(s.podcaster || '')}</p>
      <h4 class="font-['Epilogue'] font-bold text-sm text-tertiary-container line-clamp-2 group-hover:text-secondary transition-colors">${escapeHtml(_episodeTitle(s))}</h4>
    </div>`;
}

async function _buildPodcastRows(names) {
  const [podRes, accMap] = await Promise.all([
    fetch('/api/summaries/podcasters/?limit=200'),
    _ensureAccuracyCache(),
  ]);
  const podData = podRes.ok ? await podRes.json() : [];
  const epMap = {};
  podData.forEach(p => { epMap[p.podcaster] = p.episodes; });
  return names.map(name => {
    const st = cardStyle(name);
    return { name, episodes: epMap[name] ?? '--', acc: accMap[name] || null, bg: st.bg, icon: st.icon };
  });
}

function _podcastRowHtml(r) {
  const safe    = (r.name || '').replace(/'/g, "\\'");
  const epStr   = r.episodes !== '--' ? `${r.episodes} 集` : '節目資料';
  const accStr  = r.acc && r.acc.total > 0 ? ` · 準確率 ${r.acc.pct}%` : '';
  return `
    <div onclick="closeProfileListModal(); openRankedPodcasterByName('${safe}')" class="flex items-center justify-between p-4 bg-surface-container-low rounded-lg hover:bg-surface-container-highest transition-all cursor-pointer">
      <div class="flex items-center gap-4 min-w-0">
        <div class="w-12 h-12 rounded-full overflow-hidden flex-shrink-0">${podcastAvatar(r.name, r.bg, r.icon)}</div>
        <div class="min-w-0"><h4 class="font-bold text-tertiary-container text-base truncate">${escapeHtml(r.name)}</h4><p class="text-xs text-outline">${epStr}${accStr}</p></div>
      </div>
      <span class="material-symbols-outlined text-outline/50 flex-shrink-0">chevron_right</span>
    </div>`;
}

function _emptyListMsg(text) {
  return `<p class="text-outline text-sm text-center py-6">${text}</p>`;
}

// ── 三個區塊各自的「預覽」載入（Profile 頁面上直接顯示的那幾筆）────
async function loadWatchHistory() {
  const el = document.getElementById('history-scroll');
  try {
    const res  = await fetch('/api/accounts/history/?limit=10');
    if (res.status === 401) { el.innerHTML = ''; return; }
    const data = res.ok ? await res.json() : { items: [] };
    const ids  = (data.items || []).map(i => i.summary_id);
    if (!ids.length) { el.innerHTML = '<p class="text-outline text-sm">還沒有觀看紀錄，去首頁看幾集吧</p>'; return; }
    const summaries = await _fetchSummariesByIds(ids);
    el.innerHTML = summaries.map(_historyCardHtml).join('') || '<p class="text-outline text-sm">還沒有觀看紀錄</p>';
  } catch (e) {
    el.innerHTML = '<p class="text-outline text-sm">載入失敗</p>';
  }
}

async function loadSavedEpisodes() {
  const el = document.getElementById('saved-episodes-list');
  try {
    const res  = await fetch('/api/accounts/favorites/episodes/');
    if (res.status === 401) { el.innerHTML = ''; return; }
    const data = res.ok ? await res.json() : { summary_ids: [] };
    const ids  = data.summary_ids || [];
    if (!ids.length) { el.innerHTML = '<p class="text-outline text-sm py-4">還沒有收藏的單集，去單集頁面點日期旁邊的書籤圖示開始收藏</p>'; return; }
    const summaries = await _fetchSummariesByIds(ids.slice(0, 2));
    el.innerHTML = summaries.map(_savedEpisodeRowHtml).join('');
  } catch (e) {
    el.innerHTML = '<p class="text-outline text-sm py-4">載入失敗</p>';
  }
}

async function loadFavoritePodcasts() {
  const el = document.getElementById('favorite-podcasts-list');
  try {
    const res  = await fetch('/api/accounts/favorites/podcasts/');
    if (res.status === 401) { el.innerHTML = ''; return; }
    const data = res.ok ? await res.json() : { podcasters: [] };
    const names = data.podcasters || [];
    if (!names.length) { el.innerHTML = '<p class="text-outline text-sm py-4">還沒有收藏的節目，去節目排行榜點書籤圖示開始收藏</p>'; return; }
    const rows = await _buildPodcastRows(names.slice(0, 2));
    el.innerHTML = rows.map(_podcastRowHtml).join('');
  } catch (e) {
    el.innerHTML = '<p class="text-outline text-sm py-4">載入失敗</p>';
  }
}

// ── 顯示全部／查看全部彈窗 ──────────────────────────────────────
async function openProfileListModal(kind) {
  const modal = document.getElementById('profile-list-modal');
  const title = document.getElementById('profile-list-modal-title');
  const body  = document.getElementById('profile-list-modal-body');
  modal.classList.remove('hidden');
  body.innerHTML = '<p class="text-outline text-sm text-center py-6">載入中...</p>';

  try {
    if (kind === 'history') {
      title.textContent = '觀看紀錄';
      const res  = await fetch('/api/accounts/history/');
      const data = res.ok ? await res.json() : { items: [] };
      const ids  = (data.items || []).map(i => i.summary_id);
      if (!ids.length) { body.innerHTML = _emptyListMsg('還沒有觀看紀錄'); return; }
      const summaries = await _fetchSummariesByIds(ids);
      body.innerHTML = summaries.map(_savedEpisodeRowHtml).join('');
    } else if (kind === 'episodes') {
      title.textContent = '你的單集';
      const res  = await fetch('/api/accounts/favorites/episodes/');
      const data = res.ok ? await res.json() : { summary_ids: [] };
      const ids  = data.summary_ids || [];
      if (!ids.length) { body.innerHTML = _emptyListMsg('還沒有收藏的單集'); return; }
      const summaries = await _fetchSummariesByIds(ids);
      body.innerHTML = summaries.map(_savedEpisodeRowHtml).join('');
    } else if (kind === 'podcasts') {
      title.textContent = '最愛 Podcast';
      const res  = await fetch('/api/accounts/favorites/podcasts/');
      const data = res.ok ? await res.json() : { podcasters: [] };
      const names = data.podcasters || [];
      if (!names.length) { body.innerHTML = _emptyListMsg('還沒有收藏的節目'); return; }
      const rows = await _buildPodcastRows(names);
      body.innerHTML = rows.map(_podcastRowHtml).join('');
    }
  } catch (e) {
    body.innerHTML = _emptyListMsg('載入失敗，請稍後再試');
  }
}

function closeProfileListModal() {
  const modal = document.getElementById('profile-list-modal');
  if (modal) modal.classList.add('hidden');
}

// ── 進入 Profile 頁面時觸發（見 app.js 的 _renderPage）────────────
async function onProfilePageShow() {
  await loadUserProfile(); // 每次進頁都重新抓，確保跟其他分頁/裝置同步
  renderProfileHeader();
  loadPrefsSection();
  loadWatchHistory();
  loadSavedEpisodes();
  loadFavoritePodcasts();
}
