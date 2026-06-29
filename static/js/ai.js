// ── AI Assistant state ────────────────────────────────────────
let currentConversationId = null;
let aiIsLoading = false;

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
      addAIMessage(data.answer);
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

function addAIMessage(text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'flex justify-start mr-12 space-x-4';
  const p = document.createElement('p');
  p.className = 'font-body text-sm leading-relaxed whitespace-pre-wrap';
  p.textContent = text;
  const bubble = document.createElement('div');
  bubble.className = 'bg-surface-container-lowest border border-secondary/20 px-6 py-4 rounded-t-xl rounded-br-xl shadow-sm max-w-2xl';
  bubble.appendChild(p);
  const icon = document.createElement('div');
  icon.className = 'w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center shrink-0 mt-1';
  icon.innerHTML = '<span class="material-symbols-outlined text-on-secondary-container text-sm">psychology</span>';
  div.appendChild(icon);
  div.appendChild(bubble);
  container.appendChild(div);
  div.scrollIntoView({ behavior: 'smooth' });
}

function startNewConversation() {
  currentConversationId = null;
  const container = document.getElementById('chat-messages');
  container.innerHTML = `<div id="chat-empty-state" class="flex flex-col items-center justify-center py-16 space-y-4">
    <div class="w-16 h-16 rounded-full bg-secondary-container flex items-center justify-center">
      <span class="material-symbols-outlined text-on-secondary-container text-3xl">psychology</span>
    </div>
    <div class="text-center space-y-1">
      <h3 class="font-['Epilogue'] font-bold text-xl text-tertiary-container">AI 投資助理</h3>
      <p class="text-on-surface-variant text-sm">詢問股票、Podcast 重點或投資觀點</p>
    </div>
    <div class="flex flex-wrap gap-2 justify-center pt-2">
      <button onclick="sendQuickAction('台積電最新股價與本益比？')" class="px-4 py-2 rounded-full border border-outline-variant bg-surface-container-lowest text-secondary font-label text-xs font-semibold hover:bg-secondary-container/30 transition-all">台積電股價與本益比</button>
      <button onclick="sendQuickAction('最新一集Podcast重點摘要')" class="px-4 py-2 rounded-full border border-outline-variant bg-surface-container-lowest text-secondary font-label text-xs font-semibold hover:bg-secondary-container/30 transition-all">最新Podcast摘要</button>
      <button onclick="sendQuickAction('現在有什麼值得關注的投資機會？')" class="px-4 py-2 rounded-full border border-outline-variant bg-surface-container-lowest text-secondary font-label text-xs font-semibold hover:bg-secondary-container/30 transition-all">近期投資機會</button>
    </div>
  </div>`;
  closeHistoryDrawer();
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
