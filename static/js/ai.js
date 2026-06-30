// ── AI Assistant state ────────────────────────────────────────
let currentConversationId = null;
let aiIsLoading = false;
let _aiSidebarOpen = true;

function toggleAiSidebar() {
  const sidebar  = document.getElementById('ai-sidebar');
  const main     = document.getElementById('ai-main');
  const icon     = document.getElementById('ai-sidebar-toggle-icon');
  const labels   = sidebar ? sidebar.querySelectorAll('.ai-sidebar-label') : [];
  const histList = document.getElementById('ai-history-list');
  _aiSidebarOpen = !_aiSidebarOpen;
  const w = _aiSidebarOpen ? '240px' : '52px';
  if (sidebar) sidebar.style.width = w;
  if (main) { main.style.paddingLeft = w; }
  if (icon)    icon.textContent = _aiSidebarOpen ? 'menu_open' : 'menu';
  labels.forEach(el => { el.style.display = _aiSidebarOpen ? '' : 'none'; });
  if (histList) histList.style.display = _aiSidebarOpen ? '' : 'none';
}

function onAiPageShow() {
  // Hide global input bar — AI page has its own inline input
  const bar = document.getElementById('ai-input-bar');
  if (bar) bar.classList.add('hidden');
  // Refresh empty state with personalized cards if no conversation is active
  const emptyState = document.getElementById('chat-empty-state');
  if (emptyState && !currentConversationId) {
    emptyState.innerHTML = buildEmptyStateHTML();
  }
  loadConversations();
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const msg   = input.value.trim();
  if (!msg || aiIsLoading) return;
  input.value = '';
  await submitMessage(msg);
}

async function sendQuickAction(text) {
  if (aiIsLoading) return;
  await submitMessage(text);
}

async function submitMessage(query) {
  aiIsLoading = true;
  hideChatEmptyState();
  addUserMessage(query);
  const loadingEl = addLoadingMessage();
  try {
    const body = { query };
    if (currentConversationId) body.conversation_id = currentConversationId;
    const res  = await fetch('/api/ai/chat/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    loadingEl.remove();
    if (data.ok) {
      currentConversationId = data.conversation_id;
      addAIMessage(data.answer, data.follow_ups || []);
      loadConversations();
    } else {
      addAIMessage('發生錯誤：' + (data.error || '請稍後再試'));
    }
  } catch (e) {
    loadingEl.remove();
    addAIMessage('網路錯誤，請稍後再試。');
  } finally {
    aiIsLoading = false;
  }
}

function hideChatEmptyState() {
  const el = document.getElementById('chat-empty-state');
  if (el) el.remove();
}

function addUserMessage(text) {
  const container = document.getElementById('chat-messages');
  const div    = document.createElement('div');
  div.className = 'flex justify-end ml-12';
  const p = document.createElement('p');
  p.className = 'font-body text-sm leading-relaxed';
  p.textContent = text;
  const bubble = document.createElement('div');
  bubble.className = 'bg-tertiary-container text-on-tertiary px-6 py-4 rounded-t-xl rounded-bl-xl shadow-sm max-w-md';
  bubble.appendChild(p);
  div.appendChild(bubble);
  container.appendChild(div);
  div.scrollIntoView({ behavior: 'smooth' });
}

function addLoadingMessage() {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'flex justify-start mr-12 space-x-4';
  div.innerHTML = `<div class="w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center shrink-0 mt-1"><span class="material-symbols-outlined text-on-secondary-container text-sm">psychology</span></div><div class="bg-surface-container-lowest border border-secondary/20 px-6 py-4 rounded-t-xl rounded-br-xl shadow-sm"><p class="font-body text-sm text-outline animate-pulse">思考中...</p></div>`;
  container.appendChild(div);
  div.scrollIntoView({ behavior: 'smooth' });
  return div;
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _renderMarkdown(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^[\*\-] (.+)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul class="list-disc list-inside space-y-1 my-1">$1</ul>')
    .replace(/\n/g, '<br>');
}

function addAIMessage(text, followUps = []) {
  const container = document.getElementById('chat-messages');
  const wrapper = document.createElement('div');
  wrapper.className = 'flex flex-col gap-2';

  const div = document.createElement('div');
  div.className = 'flex justify-start mr-12 space-x-4';
  const bubble = document.createElement('div');
  bubble.className = 'bg-surface-container-lowest border border-secondary/20 px-6 py-4 rounded-t-xl rounded-br-xl shadow-sm flex-1 font-body text-sm leading-relaxed';
  bubble.innerHTML = _renderMarkdown(text);
  const icon = document.createElement('div');
  icon.className = 'w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center shrink-0 mt-1';
  icon.innerHTML = '<span class="material-symbols-outlined text-on-secondary-container text-sm">psychology</span>';
  div.appendChild(icon);
  div.appendChild(bubble);
  wrapper.appendChild(div);

  if (followUps && followUps.length) {
    const chips = document.createElement('div');
    chips.className = 'flex flex-wrap gap-2 pl-12 pr-12';
    followUps.forEach(q => {
      const btn = document.createElement('button');
      btn.className = 'px-3 py-1.5 rounded-full border border-secondary/30 bg-surface-container-lowest text-secondary font-label text-xs font-semibold hover:bg-secondary-container/30 transition-all text-left';
      btn.textContent = q;
      btn.onclick = () => sendQuickAction(q);
      chips.appendChild(btn);
    });
    wrapper.appendChild(chips);
  }

  container.appendChild(wrapper);
  wrapper.scrollIntoView({ behavior: 'smooth' });
}

// ── Personalized question card generator ───────────────────────
const _TOPIC_LABELS = {
  macro:       '總體經濟',
  tech:        'AI/半導體',
  crypto:      '加密貨幣',
  stocks:      '個股/選股',
  realestate:  '房地產',
  forex:       '外匯/匯率',
  etf:         'ETF/指數',
};

// 5 card categories, each with 3 templates keyed by style (conservative/aggressive/balanced)
const _CARD_TEMPLATES = [
  {
    icon: 'podcasts',
    label: '找集數',
    templates: {
      conservative: [
        '有哪集在討論 {topic} 的風險與注意事項？',
        '有沒有主播分析過 {topic} 的潛在危機？',
        '哪集對 {topic} 的風險提醒最完整？',
      ],
      aggressive: [
        '有哪集在討論 {topic} 的做多機會？',
        '主播有沒有談過 {topic} 的成長潛力？',
        '哪集對 {topic} 的看多邏輯分析最深？',
      ],
      balanced: [
        '有沒有哪集在介紹 {topic}？',
        '哪集對 {topic} 的分析最有參考價值？',
        '有哪集主播深入討論過 {topic}？',
      ],
    },
  },
  {
    icon: 'layers',
    label: '整合觀點',
    templates: {
      conservative: [
        '各主播怎麼看 {topic} 的下行風險？',
        'Podcast 裡提到 {topic} 有哪些要注意的？',
        '各節目對 {topic} 的謹慎看法有哪些？',
      ],
      aggressive: [
        '各主播對 {topic} 有沒有看多的觀點？',
        'Podcast 裡有哪些關於 {topic} 的進場邏輯？',
        '各節目對 {topic} 的多頭論點是什麼？',
      ],
      balanced: [
        '各主播對 {topic} 的整體看法是什麼？',
        'Podcast 怎麼看 {topic} 的機會與風險？',
        '{topic} 的多空觀點各是什麼？',
      ],
    },
  },
  {
    icon: 'compare_arrows',
    label: '比較分析',
    templates: {
      conservative: [
        '不同主播對 {topic} 的謹慎看法有哪些差異？',
        '{topic} 在不同節目裡的風險評估有何不同？',
        '主播們對 {topic} 看法最分歧的地方是？',
      ],
      aggressive: [
        '不同主播對 {topic} 看多的理由一樣嗎？',
        '哪個主播對 {topic} 最看好，理由是什麼？',
        '{topic} 在不同節目裡的成長預測有何差異？',
      ],
      balanced: [
        '不同主播對 {topic} 的看法一樣嗎？',
        '{topic} 前後的市場判斷有什麼轉變？',
        '各節目對 {topic} 的投資邏輯有什麼差異？',
      ],
    },
  },
  {
    icon: 'recommend',
    label: '推薦節目',
    templates: {
      conservative: [
        '推薦幾集討論 {topic} 防禦策略的節目',
        '哪幾集對 {topic} 的風險分析最完整？',
        '有沒有主播專門談過 {topic} 的保守操作？',
      ],
      aggressive: [
        '推薦幾集分析 {topic} 成長機會的節目',
        '哪幾集在討論 {topic} 的做多邏輯？',
        '有沒有看好 {topic} 的主播，推薦哪集？',
      ],
      balanced: [
        '推薦幾集談 {topic} 的 Podcast',
        '有沒有哪集在講 {topic} 的最新趨勢？',
        '我想聽關於 {topic} 的節目，有推薦嗎？',
      ],
    },
  },
  {
    icon: 'candlestick_chart',
    label: '股票數據',
    templates: {
      conservative: [
        '台積電現在的股息殖利率和本益比？',
        '台股大盤目前的估值合理嗎？',
        '有哪些防禦型股票現在值得關注？',
      ],
      aggressive: [
        '台積電現在還有上漲空間嗎？',
        '哪些 AI 概念股近期走勢最強？',
        '現在有什麼高成長個股值得關注？',
      ],
      balanced: [
        '台積電現在的股價和本益比是多少？',
        '大盤現在的位置和近期走勢怎麼樣？',
        '現在有什麼值得關注的投資機會？',
      ],
    },
  },
];

function _pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function buildSuggestedQuestions() {
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem('pod2invest_prefs') || '{}'); } catch {}

  const style    = (prefs.style && prefs.style.length) ? prefs.style[0] : 'balanced';
  const styleKey = ['conservative', 'aggressive'].includes(style) ? style : 'balanced';

  // topic source: hot tags → user preference topics → default
  let topicPool;
  if (typeof _hotTagNames !== 'undefined' && _hotTagNames.length >= 5) {
    topicPool = [..._hotTagNames].sort(() => Math.random() - 0.5);
  } else if (prefs.topics && prefs.topics.length) {
    topicPool = prefs.topics.map(t => _TOPIC_LABELS[t] || t);
  } else {
    topicPool = ['台積電', '總體經濟', 'AI產業', '美股', 'ETF'];
  }

  return _CARD_TEMPLATES.map((cat, i) => {
    const topic    = topicPool[i % topicPool.length];
    const pool     = cat.templates[styleKey];
    const question = _pickRandom(pool).replace('{topic}', topic);
    return { icon: cat.icon, label: cat.label, question };
  });
}

function buildEmptyStateHTML() {
  const subtitle = (typeof _hotTagNames !== 'undefined' && _hotTagNames.length)
    ? '根據近期熱門話題為您推薦'
    : (() => {
        try {
          const p = JSON.parse(localStorage.getItem('pod2invest_prefs') || '{}');
          return (p.topics && p.topics.length) ? '根據您的偏好為您量身推薦' : '詢問股票、Podcast 重點或投資觀點';
        } catch { return '詢問股票、Podcast 重點或投資觀點'; }
      })();

  const cards = buildSuggestedQuestions();
  const cardsHTML = cards.map(c => `
    <button onclick="sendQuickAction('${c.question.replace(/'/g, '&#39;')}')"
      class="flex flex-col gap-3 p-4 rounded-2xl border border-outline-variant/30 bg-surface-container-lowest
             text-left hover:bg-secondary-container/20 hover:border-secondary/30 transition-all active:scale-[0.98] w-full">
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-secondary text-base" style="font-variation-settings:'FILL' 1">${c.icon}</span>
        <span class="font-label text-[10px] font-bold uppercase tracking-widest text-outline">${c.label}</span>
      </div>
      <p class="font-body text-sm text-on-surface leading-snug">${c.question}</p>
    </button>`).join('');

  return `
    <div class="flex flex-col items-center justify-center py-10 space-y-6 w-full">
      <div class="flex flex-col items-center space-y-2">
        <div class="w-14 h-14 rounded-full bg-secondary-container flex items-center justify-center">
          <span class="material-symbols-outlined text-on-secondary-container text-3xl">psychology</span>
        </div>
        <h3 class="font-['Epilogue'] font-bold text-xl text-tertiary-container">有什麼想了解的？</h3>
        <p class="text-on-surface-variant text-sm">${subtitle}</p>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-3xl px-1">
        ${cardsHTML}
      </div>
    </div>`;
}

function startNewConversation() {
  currentConversationId = null;
  const container = document.getElementById('chat-messages');
  container.innerHTML = `<div id="chat-empty-state" class="flex-1 flex flex-col items-center justify-center">${buildEmptyStateHTML()}</div>`;
}

function openHistoryDrawer() {
  document.getElementById('ai-history-overlay').classList.remove('hidden');
  document.getElementById('ai-history-drawer').style.transform = 'translateX(0)';
  loadConversations();
}

function closeHistoryDrawer() {
  document.getElementById('ai-history-overlay').classList.add('hidden');
  document.getElementById('ai-history-drawer').style.transform = 'translateX(-100%)';
}

async function loadConversations() {
  const list = document.getElementById('ai-history-list');
  try {
    const res  = await fetch('/api/ai/conversations/');
    const data = await res.json();
    if (!data.ok || !data.data.length) {
      list.innerHTML = '<p class="text-outline text-xs text-center py-6">尚無對話記錄</p>';
      return;
    }
    list.innerHTML = '';
    for (const group of data.data) {
      const label = document.createElement('p');
      label.className = 'text-outline text-[10px] font-bold uppercase tracking-widest px-2 pt-4 pb-1';
      label.textContent = group.group;
      list.appendChild(label);
      for (const conv of group.conversations) {
        const row = document.createElement('div');
        row.className = 'flex items-center gap-1 group rounded-xl hover:bg-surface-container transition-colors';
        const btn = document.createElement('button');
        btn.className = 'flex-1 text-left px-3 py-2.5 text-sm text-on-surface font-body truncate';
        btn.textContent = conv.title;
        btn.onclick = () => loadConversationMessages(conv.id, conv.title);
        const del = document.createElement('button');
        del.className = 'opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-surface-container-high transition-all';
        del.innerHTML = '<span class="material-symbols-outlined text-outline text-base">delete</span>';
        del.onclick = (e) => { e.stopPropagation(); deleteConversation(conv.id, row); };
        row.appendChild(btn);
        row.appendChild(del);
        list.appendChild(row);
      }
    }
  } catch {
    list.innerHTML = '<p class="text-outline text-xs text-center py-6">載入失敗</p>';
  }
}

async function loadConversationMessages(convId) {
  currentConversationId = convId;
  const container = document.getElementById('chat-messages');
  container.innerHTML = '<p class="text-outline text-sm text-center py-8 animate-pulse">載入對話中...</p>';
  closeHistoryDrawer();
  try {
    const res  = await fetch(`/api/ai/conversations/${convId}/messages/`);
    const data = await res.json();
    container.innerHTML = '';
    if (!data.ok || !data.data.length) {
      container.innerHTML = '<p class="text-outline text-sm text-center py-8">此對話沒有訊息</p>';
      return;
    }
    for (const msg of data.data) {
      if (msg.role === 'user') addUserMessage(msg.content);
      else addAIMessage(msg.content);
    }
  } catch {
    container.innerHTML = '<p class="text-outline text-sm text-center py-8">載入失敗</p>';
  }
}

async function deleteConversation(convId, rowEl) {
  try {
    const res  = await fetch(`/api/ai/conversations/${convId}/`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      rowEl.remove();
      if (currentConversationId === convId) startNewConversation();
    }
  } catch { /* silent */ }
}
