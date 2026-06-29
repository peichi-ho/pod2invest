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

function savePreferences() {
  const prefs = {
    style:  [...document.querySelectorAll('#pref-style .pref-chip')].filter(b => b.className.includes('text-secondary')).map(b => b.dataset.val),
    topics: [...document.querySelectorAll('#pref-topics .pref-chip')].filter(b => b.className.includes('text-secondary')).map(b => b.dataset.val),
    depth:  [...document.querySelectorAll('#pref-depth .pref-chip')].filter(b => b.className.includes('text-secondary')).map(b => b.dataset.val),
  };
  localStorage.setItem('pod2invest_prefs', JSON.stringify(prefs));
  const msg = document.getElementById('pref-saved-msg');
  msg.classList.remove('hidden');
  setTimeout(() => msg.classList.add('hidden'), 3000);
}

function loadPreferences() {
  try {
    const prefs = JSON.parse(localStorage.getItem('pod2invest_prefs') || '{}');
    if (prefs.style)  prefs.style.forEach(v  => { const b = document.querySelector(`#pref-style [data-val="${v}"]`);  if (b) b.className = _prefActiveClass; });
    if (prefs.topics) prefs.topics.forEach(v => { const b = document.querySelector(`#pref-topics [data-val="${v}"]`); if (b) b.className = _prefActiveClass; });
    if (prefs.depth)  prefs.depth.forEach(v  => { const b = document.querySelector(`#pref-depth [data-val="${v}"]`);  if (b) b.className = _prefActiveClass; });
  } catch {}
}
