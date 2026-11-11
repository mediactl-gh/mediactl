// web-share.js — share/pair modal helpers

let _shareCode = '';
let _shareBase = location.origin; // fallback until /api/host responds

// ── Modal helper ────────────────────────────────────────────────────────────
function _showModal(html) {
  document.getElementById('modal').innerHTML = html;
  document.getElementById('modal-backdrop').style.display = 'flex';
}

function _startRegistrationFromAuthGate() {
  _openRegModal();
  // Pre-fill code if arrived via QR scan
  const urlCode = new URLSearchParams(location.search).get('code');
  if (urlCode) {
    const inp = document.getElementById('reg-code-input');
    if (inp) {
      inp.value = urlCode.toUpperCase();
      submitCode(); // advance straight to step 2
    }
  }
}

function _registerWebPairHandlers() {
  onEvent('web.pair', evt => {
    if (evt.hint === 'web.pair.redeemed' && _shareCode) {
      closeModal();
      _shareCode = '';
    }
  });
}
_registerWebPairHandlers();

function _syncShareButtonVisibility(user) {
  const shareBtn = document.getElementById('share-btn');
  if (shareBtn) shareBtn.classList.toggle('visible', user?.tags?.includes('admin') === true);
}

const _HOMESCREEN_TIP = /iPhone|iPad|iPod/.test(navigator.userAgent)
  ? 'Tap the Share button (□↑) in Safari, then "Add to Home Screen" for a full-screen app.'
  : /Android/.test(navigator.userAgent)
  ? 'Tap the menu (⋮) in Chrome, then "Add to Home screen" for a full-screen app.'
  : 'Open this URL on your mobile device and use the browser menu to add it to your home screen.';

async function openShareModal() {
  _showModal(`
    <h3>Pair a device</h3>
    <div id="share-qr-wrap">
      <div id="share-qr"></div>
      <div id="share-code-display" style="font-family:var(--mono);font-size:28px;font-weight:500;letter-spacing:.25em;text-align:center;padding:10px 0 2px">——————</div>
      <div style="font-size:11px;color:var(--text3);text-align:center">Scan the QR or enter the code on the new device</div>
    </div>
    <div>
      <div class="form-label" style="margin-bottom:8px">Role</div>
      <div class="tag-chip-group" id="share-tag-group">
        <div class="tag-chip selected" data-tag="user">user</div>
        <div class="tag-chip" data-tag="admin">admin</div>
        <div class="tag-chip" data-tag="guest">guest</div>
      </div>
    </div>
    <div style="background:var(--surface2);border-radius:10px;padding:12px 14px;font-size:11px;color:var(--text2);line-height:1.6">
      <strong style="color:var(--text);display:block;margin-bottom:3px;font-size:11px">📱 Add to Home Screen</strong>
      ${_HOMESCREEN_TIP}
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn" onclick="regenerateCode()">regenerate</button>
      <button class="btn" onclick="copyPairCode()">copy code</button>
      <button class="btn accent" onclick="closeModal()">done</button>
    </div>
  `);
  // wire tag chips
  document.querySelectorAll('#share-tag-group .tag-chip').forEach(c =>
    c.addEventListener('click', () => c.classList.toggle('selected')));
  await _generateCode();
}

function _getSelectedTags() {
  return [...document.querySelectorAll('#share-tag-group .tag-chip.selected')]
    .map(c => c.dataset.tag);
}

async function _generateCode() {
  const tags = _getSelectedTags().length ? _getSelectedTags() : ['user'];
  try {
    const [meRes, hostRes] = await Promise.all([
      fetch(`/api/me?generate=true&tags=${tags.join(',')}`),
      fetch('/api/host')
    ]);
    const me            = await meRes.json();
    const { url: base } = await hostRes.json();
    _shareCode = me.pair_code;
    _shareBase = base;
    document.getElementById('share-code-display').textContent = _shareCode;
    _renderQR();
  } catch (e) {
    const el = document.getElementById('share-code-display');
    if (el) el.textContent = 'error';
  }
}

function _renderQR() {
  const wrap = document.getElementById('share-qr');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (typeof QRCode !== 'undefined') {
    new QRCode(wrap, {
      text:         `${_shareBase}/?code=${_shareCode}`,
      width:        160, height: 160,
      colorDark:    '#e8eaf0', colorLight: '#181b22',
      correctLevel: QRCode.CorrectLevel.M,
    });
  }
}

async function regenerateCode() { await _generateCode(); }

function copyPairCode() {
  if (!_shareCode) return;
  navigator.clipboard?.writeText(_shareCode).then(() => toast('Code copied', 'ok'));
}

// ── Registration flow state ────────────────────────────────────────────────
const _AVATARS = ['🙂','😎','🦊','🐼','🐸','🦁','🐯','🐻','🐨','🦋',
                  '🌙','⚡','🔥','🌊','🍀','🎯','🎸','🚀','🤖','🧠',
                  '👾','🎮','🌈','🦄'];
let _regCode = '';

// ── Registration Step 1: pairing code entry ────────────────────────────────
function _openRegModal() {
  _showModal(`
    <h3>Welcome to mediactl</h3>
    <div style="display:flex;flex-direction:column;gap:8px">
      <input class="reg-code-input" id="reg-code-input" maxlength="6"
             placeholder="XXXXXX" autocomplete="off" spellcheck="false"
             style="font-family:var(--mono);font-size:24px;font-weight:500;letter-spacing:.2em;
                    text-align:center;text-transform:uppercase;background:var(--surface2);
                    border:1px solid var(--border2);border-radius:10px;padding:14px;
                    color:var(--text);width:100%">
      <div style="font-size:11px;color:var(--text3);text-align:center">Enter the pairing code shown on the server terminal</div>
      <div id="reg-code-error" style="font-size:11px;color:var(--red);text-align:center;display:none">Invalid or already-used code</div>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn accent" onclick="submitCode()">continue →</button>
    </div>
  `);
  document.getElementById('modal').dataset.locked = 'true';
  const inp = document.getElementById('reg-code-input');
  _bindRegCodeInputHandlers(inp);
  setTimeout(() => inp.focus(), 100);
}

function submitCode() {
  const inp  = document.getElementById('reg-code-input');
  const code = inp?.value.trim().toUpperCase();
  if (!code || code.length < 4) return;
  _regCode = code;
  _openRegStep2();
}

// ── Registration Step 2: profile + avatar ─────────────────────────────────
function _openRegStep2() {
  const avatarHTML = _AVATARS.map((em, i) =>
    `<div class="avatar-opt${i===0?' selected':''}" data-emoji="${em}"
          onclick="selectAvatar(this)" style="aspect-ratio:1;border-radius:8px;
          border:2px solid ${i===0?'var(--accent)':'transparent'};
          background:var(--surface2);font-size:18px;display:flex;
          align-items:center;justify-content:center;cursor:pointer">${em}</div>`
  ).join('');
  _showModal(`
    <h3>Set up your profile</h3>
    <div style="display:flex;flex-direction:column;gap:6px">
      <div class="form-label">Your name</div>
      <input class="reg-name-input" id="reg-name-input" placeholder="e.g. Tom" maxlength="32"
             style="width:100%;background:var(--surface2);border:1px solid var(--border2);
                    border-radius:10px;padding:10px 14px;color:var(--text);
                    font-family:var(--font);font-size:14px;font-weight:500">
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      <div class="form-label">Avatar</div>
      <div style="display:grid;grid-template-columns:repeat(8,1fr);gap:6px">${avatarHTML}</div>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn" onclick="_openRegModal()">← back</button>
      <button class="btn accent" onclick="submitRegistration()">join</button>
    </div>
  `);
  document.getElementById('modal').dataset.locked = 'true';
  const inp = document.getElementById('reg-name-input');
  _bindRegProfileInputHandlers(inp);
  setTimeout(() => inp.focus(), 100);
}

function _bindRegCodeInputHandlers(inp) {
  if (!inp) return;
  inp.addEventListener('input', () => { inp.value = inp.value.toUpperCase(); });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') submitCode(); });
}

function _bindRegProfileInputHandlers(inp) {
  if (!inp) return;
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') submitRegistration(); });
}

function selectAvatar(el) {
  document.querySelectorAll('.avatar-opt').forEach(o => {
    o.classList.remove('selected');
    o.style.borderColor = 'transparent';
  });
  el.classList.add('selected');
  el.style.borderColor = 'var(--accent)';
}

function _selectedAvatar() {
  return document.querySelector('.avatar-opt.selected')?.dataset.emoji || '🙂';
}

// ── Registration Step 3: redeem + finalize ────────────────────────────────
async function submitRegistration() {
  const name   = document.getElementById('reg-name-input')?.value.trim();
  const avatar = _selectedAvatar();
  if (!name) { document.getElementById('reg-name-input')?.focus(); return; }
  try {
    const r = await fetch('/api/users/redeem', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: _regCode, name, avatar })
    });
    if (r.status === 403) {
      _openRegModal();
      setTimeout(() => {
        document.getElementById('reg-code-input').classList.add('error');
        document.getElementById('reg-code-error').style.display = 'block';
      }, 50);
      return;
    }
    document.getElementById('modal').dataset.locked = 'false';
    closeModal();
    history.replaceState({}, '', '/');
    _postUserDevices().catch(() => {});
    init();
  } catch (e) {
    toast('Registration failed', 'err');
  }
}
