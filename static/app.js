/* Tab switching (mobile) */
function switchTab(tab) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.querySelector('[data-tab="' + tab + '"]').classList.add('active');
}

/* Workspace tab switching (desktop) */
function switchWsTab(tab) {
  document.querySelectorAll('.ws-tab').forEach(el => el.classList.toggle('active', el.dataset.ws === tab));
  document.querySelectorAll('#tab-sessions, #tab-schedules').forEach(el => el.classList.remove('ws-active'));
  document.getElementById('tab-' + tab).classList.add('ws-active');
}

/* SVG icon templates */
const ICN = {
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  loader: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>',
  restart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>',
};

const MODES = {
  c:    { icon: ICN.bolt,   cls: 'mode-c',    detail: '<strong>Unrestricted</strong> / skip permissions, no approval prompts' },
  ci:   { icon: ICN.users,  cls: 'mode-ci',   detail: '<strong>Teammate in-process</strong> / skip permissions, teammate mode' },
  safe: { icon: ICN.shield, cls: 'mode-safe', detail: '<strong>Safe mode</strong> / standard permissions, requires approvals' },
};

let tunnelState = { available: false, running: false, url: null, auth_configured: false };
let stoppingSet = new Set();
let shownErrors = new Set();
let editingScheduleId = null;
let expandedRuns = new Set();

function selectMode(mode) {
  document.querySelectorAll('.mode-radio').forEach(el => {
    const isSelected = el.dataset.mode === mode;
    el.classList.toggle('selected', isSelected);
    el.querySelector('input').checked = isSelected;
  });
  const m = MODES[mode];
  document.getElementById('btn-launch').className = 'btn-launch ' + m.cls;
}
function updateMode() { /* compat stub */ }

function defaultName() {
  const d = new Date();
  return 'rc-' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0') + String(d.getSeconds()).padStart(2,'0');
}

document.getElementById('session-name').placeholder = defaultName();

const _apiBase = window.location.origin + '/rc';
// Auth token injected by the server into the page. Mobile Safari doesn't
// forward Basic Auth credentials on fetch/XHR, so we attach it explicitly.
const _authToken = window.__RC_AUTH || '';
async function api(method, path, body) {
  const headers = {'Content-Type': 'application/json'};
  if (_authToken) headers['Authorization'] = _authToken;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(_apiBase + path, opts);
  if (r.status === 401) {
    window.location.href = '/login';
    throw new Error('Authentication required');
  }
  return r.json();
}

/* --- Directory browser (multi-instance) --- */

let browsers = {
  launch: { path: null, selected: null, open: false },
  sched: { path: null, selected: null, open: false },
  wiz: { path: null, selected: null, open: false },
};
let hasProjects = false;

function _ids(ctx) {
  if (ctx === 'launch') return { wrap: 'dir-browser-wrap', input: 'dir-browser-input', browser: 'dir-browser', breadcrumb: 'dir-breadcrumb', list: 'dir-list' };
  return { wrap: ctx + '-dir-browser-wrap', input: ctx + '-dir-browser-input', browser: ctx + '-dir-browser', breadcrumb: ctx + '-dir-breadcrumb', list: ctx + '-dir-list' };
}

function onProjectChange() {
  const sel = document.getElementById('project-select');
  const browserWrap = document.getElementById('dir-browser-wrap');
  if (sel.value === '__browse__') {
    browserWrap.style.display = 'block';
    browsers.launch.selected = null;
    browseTo('launch', browsers.launch.path || browsers.launch.selected);
  } else {
    browserWrap.style.display = 'none';
    closeBrowser('launch');
  }
}

async function loadProjects() {
  try {
    const data = await api('GET', '/projects');
    const projects = data.projects || [];
    const selWrap = document.getElementById('project-select-wrap');
    const browserWrap = document.getElementById('dir-browser-wrap');
    browsers.launch.selected = null;
    browsers.launch.path = data.default || '/';
    if (projects.length === 0) {
      hasProjects = false;
      selWrap.style.display = 'none';
      browserWrap.style.display = 'block';
      document.getElementById('dir-browser-input').value = data.default || '/';
      browseTo('launch', data.default || '/');
    } else {
      hasProjects = true;
      selWrap.style.display = '';
      browserWrap.style.display = 'none';
      const sel = document.getElementById('project-select');
      sel.innerHTML = '<option value="">Default (' + escHtml(data.default_name) + ')</option>';
      projects.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.path;
        opt.textContent = p.name + (p.exists ? '' : ' (missing)');
        opt.title = p.path;
        if (!p.exists) opt.disabled = true;
        sel.appendChild(opt);
      });
      const browse = document.createElement('option');
      browse.value = '__browse__';
      browse.textContent = 'Browse\u2026';
      sel.appendChild(browse);
    }
  } catch(e) {}
}

function toggleBrowser(ctx) {
  const ids = _ids(ctx);
  const panel = document.getElementById(ids.browser);
  if (browsers[ctx].open) {
    closeBrowser(ctx);
  } else {
    panel.classList.add('open');
    browsers[ctx].open = true;
    browseTo(ctx, browsers[ctx].path || browsers[ctx].selected || '/');
  }
}

function closeBrowser(ctx) {
  const ids = _ids(ctx);
  document.getElementById(ids.browser).classList.remove('open');
  browsers[ctx].open = false;
}

async function browseTo(ctx, path) {
  if (!path) path = '/';
  browsers[ctx].path = path;
  const ids = _ids(ctx);
  const listEl = document.getElementById(ids.list);
  const crumbEl = document.getElementById(ids.breadcrumb);
  listEl.innerHTML = '<div class="dir-empty">Loading\u2026</div>';
  try {
    const data = await api('GET', '/browse?path=' + encodeURIComponent(path));
    if (data.error) {
      listEl.innerHTML = '<div class="dir-empty">' + escHtml(data.error) + '</div>';
      return;
    }
    browsers[ctx].path = data.path;
    const parts = data.path.split('/').filter(Boolean);
    let crumbHtml = '<span class="dir-breadcrumb-seg" onclick="browseTo(\'' + ctx + '\',\'/\')">/</span>';
    let accumulated = '';
    parts.forEach((part, i) => {
      accumulated += '/' + part;
      const p = accumulated;
      crumbHtml += '<span class="dir-breadcrumb-sep">/</span><span class="dir-breadcrumb-seg" onclick="browseTo(\'' + ctx + '\',\'' + escHtml(p.replace(/'/g, "\\\\'")) + '\')">' + escHtml(part) + '</span>';
    });
    crumbEl.innerHTML = crumbHtml;
    if (data.dirs.length === 0) {
      listEl.innerHTML = '<div class="dir-empty">No subfolders</div>';
    } else {
      listEl.innerHTML = data.dirs.map(d => {
        const full = (data.path === '/' ? '/' : data.path + '/') + d;
        return '<div class="dir-item" onclick="browseTo(\'' + ctx + '\',\'' + escHtml(full.replace(/'/g, "\\\\'")) + '\')">' + ICN.folder + ' ' + escHtml(d) + '</div>';
      }).join('');
    }
    // Update the input to show current path
    document.getElementById(ids.input).value = data.path;
  } catch(e) {
    listEl.innerHTML = '<div class="dir-empty">Error loading directory</div>';
  }
}

function selectDir(ctx) {
  const ids = _ids(ctx);
  browsers[ctx].selected = browsers[ctx].path;
  document.getElementById(ids.input).value = browsers[ctx].selected;
  closeBrowser(ctx);
  // For sched/wiz context, also update the workdir input
  if (ctx === 'sched') {
    document.getElementById('sched-workdir').value = browsers[ctx].selected;
  } else if (ctx === 'wiz') {
    document.getElementById('wiz-workdir').value = browsers[ctx].selected;
  }
}

/* Close browser when clicking outside — use mousedown so it fires before
   innerHTML updates remove the clicked element from DOM */
document.addEventListener('mousedown', function(e) {
  for (const ctx of Object.keys(browsers)) {
    if (!browsers[ctx].open) continue;
    const ids = _ids(ctx);
    const wrap = document.getElementById(ids.wrap);
    if (wrap && !wrap.contains(e.target)) closeBrowser(ctx);
  }
});

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function showToast(title, message, duration, action, type) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast' + (type === 'success' ? ' toast-success' : '');
  let html = '<div class="toast-title">' + escHtml(title) + '</div>' + escHtml(message);
  if (action) {
    html += '<div><button class="toast-action" id="toast-action-btn">' + escHtml(action.label) + '</button></div>';
  }
  toast.innerHTML = html;
  if (action) {
    toast.querySelector('#toast-action-btn').onclick = () => { toast.remove(); action.fn(); };
  }
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toast-out 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration || 8000);
}

function getSelectedWorkdir() {
  const selWrap = document.getElementById('project-select-wrap');
  if (selWrap.style.display === 'none') {
    return browsers.launch.selected || undefined;
  }
  const sel = document.getElementById('project-select');
  if (sel.value === '__browse__') return browsers.launch.selected || undefined;
  return sel.value || undefined;
}

let launchingSession = null;

async function refresh() {
  const data = await api('GET', '/sessions');
  if (data.errors) {
    for (const [name, error] of Object.entries(data.errors)) {
      if (!shownErrors.has(name) && launchingSession !== name) {
        shownErrors.add(name);
        showToast('Session failed: ' + name, error, 12000);
      }
    }
  }
  const el = document.getElementById('sessions');
  document.getElementById('session-name').placeholder = defaultName();

  const count = (data.sessions || []).length;

  // Update all session count badges
  document.querySelectorAll('.session-count-badge').forEach(badge => {
    if (count > 0) { badge.textContent = count; badge.style.display = ''; }
    else { badge.style.display = 'none'; }
  });

  // Update all stop-all buttons
  document.querySelectorAll('.btn-stop-all-btn').forEach(btn => {
    btn.style.display = count > 1 ? 'inline-flex' : 'none';
    btn.disabled = false;
  });

  if (!data.sessions || data.sessions.length === 0) {
    el.innerHTML = '<div class="empty">No sessions running</div>';
    stoppingSet.clear();
  } else {
    const names = new Set(data.sessions.map(s => s.name));
    for (const n of stoppingSet) { if (!names.has(n)) stoppingSet.delete(n); }

    el.innerHTML = data.sessions.map(s => {
      const badgeClass = s.mode === 'ci' ? 'badge-ci' : s.mode === 'safe' ? 'badge-safe' : 'badge-c';
      const permTag = s.mode === 'safe'
        ? '<span class="perm-tag perm-normal">safe</span>'
        : '<span class="perm-tag perm-skip">skip-perms</span>';
      const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
      const wizardBadge = s.wizard ? ' <span class="badge badge-wizard">wizard</span>' : '';
      const wizardHint = (s.wizard && s.url && s.status !== 'dead')
        ? '<div style="font-size:0.72rem;color:#fbbf24;margin-bottom:0.5rem;">Open this session to finalize your scheduled task with Claude</div>'
        : '';
      const isDead = s.status === 'dead';
      const urlHtml = isDead
        ? '<div class="session-url"><span class="session-dead">\u26a0 Session exited (connection timed out or process stopped)</span></div>'
        : s.url
          ? '<div class="session-url"><a href="' + escHtml(s.url) + '" target="_blank">' + escHtml(s.url) + '</a></div>'
          : '<div class="session-url"><span class="waiting">' + ICN.loader + ' Waiting for URL\u2026</span></div>';
      const copyBtn = s.url && !isDead
        ? '<button class="btn-copy btn-copy-url" onclick="copyUrl(\'' + escHtml(s.url.replace(/'/g, "\\'")) + '\',this)">' + ICN.copy + ' Copy</button>'
        : '';
      const nudgeBtn = !s.url && !isDead
        ? '<button class="btn-unstick" onclick="unstickSession(\'' + s.name.replace(/'/g, "\\'") + '\')">' + ICN.bolt + ' Nudge</button>'
        : '';
      const projectHtml = s.project
        ? '<div class="project-label" title="' + escHtml(s.workdir || '') + '">' + ICN.folder + ' ' + escHtml(s.project) + '</div>'
        : '';
      let tokenHtml = '';
      if (s.tokens != null && !isDead) {
        const tk = s.tokens;
        const maxTokens = 200000;
        const pct = Math.min(100, Math.round(tk / maxTokens * 100));
        const barClass = pct >= 80 ? 'crit' : pct >= 60 ? 'warn' : '';
        const label = tk >= 1000 ? Math.round(tk / 1000) + 'K' : tk;
        tokenHtml = '<div class="token-label">' +
          '<span>' + label + ' tokens (' + pct + '%)</span>' +
          '<div class="token-bar"><div class="token-bar-fill ' + barClass + '" style="width:' + pct + '%"></div></div>' +
        '</div>';
      }
      const isStopping = stoppingSet.has(s.name);
      const previewBtn = !isDead ? '<button class="btn-preview" onclick="openPreview(\'' + s.name.replace(/'/g, "\\'") + '\')">Preview</button>' : '';
      const restartBtn = '<button class="btn-restart" onclick="restartSession(\'' + s.name.replace(/'/g, "\\'") + '\')">' + ICN.restart + ' Restart</button>';
      const stopBtn = isStopping
        ? '<button class="btn-stop" disabled><span class="spinner spinner-sm"></span> Stopping\u2026</button>'
        : '<button class="btn-stop" onclick="stopSession(\'' + s.name.replace(/'/g, "\\'") + '\')">' + ICN.stop + ' Stop</button>';
      return '<div class="session-card' + (isDead ? ' session-card-dead' : '') + '">' +
        '<div class="session-header">' +
          '<span class="session-name">' + escHtml(s.name) + wizardBadge + '</span>' +
          '<span style="display:flex;gap:0.3rem;align-items:center;">' + permTag +
            '<span class="badge ' + badgeClass + '">' + modeLabel + '</span>' +
          '</span>' +
        '</div>' +
        projectHtml + tokenHtml + wizardHint + urlHtml +
        '<div class="session-actions"><div class="session-actions-left">' + previewBtn + restartBtn + stopBtn + '</div>' + (nudgeBtn || copyBtn) + '</div>' +
      '</div>';
    }).join('');
  }
  await refreshTunnel();
  await refreshSchedules();
}

async function refreshTunnel() {
  try { tunnelState = await api('GET', '/tunnel/status'); } catch(e) {
    tunnelState = { available: false, running: false, url: null, auth_configured: false };
  }
  renderShare();
}

function renderShare() {
  const el = document.getElementById('share-section');
  if (!tunnelState.available) {
    el.innerHTML = '<div class="share-card share-dimmed"><h3>' + ICN.globe + ' Remote Access</h3>' +
      '<button class="btn-share" disabled>' + ICN.share + ' Share</button>' +
      '<div class="share-install-hint">Requires <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" target="_blank">cloudflared</a></div></div>';
    return;
  }
  let body = '';
  if (tunnelState.running && tunnelState.url) {
    if (!tunnelState.auth_configured) {
      body += '<div class="share-warning">' + ICN.warn + ' <span>No authentication configured. Anyone with this link can launch sessions.</span></div>';
    }
    body += '<div class="share-url"><a href="' + escHtml(tunnelState.url) + '" target="_blank">' + escHtml(tunnelState.url) + '</a>' +
      '<button class="btn-copy" onclick="copyUrl()">' + ICN.copy + ' Copy</button></div>' +
      '<button class="btn-share-stop" onclick="stopTunnel()">' + ICN.x + ' Stop Sharing</button>';
  } else if (tunnelState.running) {
    body += '<button class="btn-share" disabled><span class="spinner"></span> Starting tunnel\u2026</button>';
  } else {
    body += '<button class="btn-share" onclick="startTunnel()">' + ICN.share + ' Share</button>';
  }
  el.innerHTML = '<div class="share-card"><h3>' + ICN.globe + ' Remote Access</h3>' + body + '</div>';
}

async function startTunnel() {
  await api('POST', '/tunnel/start');
  tunnelState.running = true; tunnelState.url = null;
  renderShare();
}

async function stopTunnel() {
  await api('POST', '/tunnel/stop');
  tunnelState.running = false; tunnelState.url = null;
  renderShare();
}

function copyUrl(url, btnEl) {
  const text = url || tunnelState.url;
  if (!text) return;
  const btn = btnEl || document.querySelector('.btn-copy');
  navigator.clipboard.writeText(text).then(() => {
    if (btn) { btn.innerHTML = ICN.check + ' Copied!'; setTimeout(() => { btn.innerHTML = ICN.copy + ' Copy'; }, 1500); }
  });
}

async function startSession(opts) {
  opts = opts || {};
  const mode = opts.mode || document.querySelector('input[name="launch-mode"]:checked').value;
  const btn = document.getElementById('btn-launch');
  const input = document.getElementById('session-name');
  const name = opts.name || input.value.trim() || input.placeholder;
  const workdir = opts.workdir || getSelectedWorkdir();
  const model = opts.model || document.getElementById('model-select').value;
  if (!opts.name) input.value = '';
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Launching\u2026';
  const body = { name, mode };
  if (model) body.model = model;
  if (workdir) body.workdir = workdir;
  if (opts.sandbox) body.sandbox = true;
  const startResp = await api('POST', '/start', body);
  if (startResp && !startResp.ok) {
    showToast('Launch failed', startResp.message || 'Unknown error');
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Launch Session';
    return;
  }
  const launchName = (startResp && startResp.name) || name;
  launchingSession = launchName;
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const data = await api('GET', '/sessions');
    if (data.errors && data.errors[launchName]) {
      const err = data.errors[launchName];
      shownErrors.add(launchName);
      const isSandboxError = err.includes('root') || err.includes('sudo') || err.includes('privileges');
      if (isSandboxError && !opts.sandbox) {
        showToast('Session failed: ' + launchName, err, 15000, {
          label: 'Retry with sandbox mode (IS_SANDBOX=1)',
          fn: () => startSession({ name, mode, workdir, model, sandbox: true })
        });
      } else {
        showToast('Session failed: ' + launchName, err, 12000);
      }
      break;
    }
    const s = (data.sessions || []).find(s => s.name === launchName);
    if (s && s.url) {
      showToast('Session launched', launchName + ' is ready', 5000, null, 'success');
      break;
    }
  }
  launchingSession = null;
  btn.disabled = false;
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Launch Session';
  refresh();
}

async function stopSession(name) {
  stoppingSet.add(name);
  renderSessions();
  await api('POST', '/stop', { name });
  refresh();
}

function renderSessions() {
  document.querySelectorAll('.btn-stop[onclick]').forEach(btn => {
    const m = btn.getAttribute('onclick').match(/stopSession\('(.+?)'\)/);
    if (m && stoppingSet.has(m[1])) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner spinner-sm"></span> Stopping\u2026';
    }
  });
}

async function stopAll() {
  document.querySelectorAll('.btn-stop-all-btn').forEach(btn => {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-sm"></span> Stopping\u2026';
  });
  await api('POST', '/stop-all');
  refresh();
}

async function unstickSession(name) {
  const btn = document.querySelector('.btn-unstick[onclick*="' + name + '"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner spinner-sm"></span> Nudging\u2026'; }
  const res = await api('POST', '/unstick', { name });
  if (btn) {
    btn.innerHTML = (res.ok ? ICN.check : ICN.warn) + ' ' + (res.message || 'Done');
    setTimeout(() => { btn.disabled = false; btn.innerHTML = ICN.bolt + ' Nudge'; }, 3000);
  }
  setTimeout(refresh, 2000);
}

async function restartSession(name) {
  const btn = document.querySelector('.btn-restart[onclick*="' + name + '"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner spinner-sm"></span> Restarting\u2026'; }
  await api('POST', '/restart', { name, resume: true });
  refresh();
}

/* --- Resume --- */

async function openResumeModal() {
  document.getElementById('resume-modal').style.display = 'flex';
  const el = document.getElementById('resume-list');
  el.innerHTML = '<div class="empty">Loading\u2026</div>';
  try {
    const data = await api('GET', '/resume/sessions');
    const projects = data.projects || [];
    if (projects.length === 0) {
      el.innerHTML = '<div class="empty">No past sessions found</div>';
      return;
    }
    el.innerHTML = projects.map(p => {
      const sessions = p.sessions.map(s => {
        const title = s.name || s.id.substring(0, 8) + '\u2026';
        const updated = formatRelativeTime(s.updated);
        return '<div class="resume-item" onclick="resumeSession(\'' + escHtml(s.id) + '\', \'' + escHtml((s.name || '').replace(/'/g, '')) + '\', \'' + escHtml(p.project) + '\')">' +
          '<div class="resume-item-info">' +
            '<div class="resume-item-title">' + escHtml(title) + '</div>' +
            '<div class="resume-item-meta">' +
              '<span>' + escHtml(s.branch) + '</span>' +
              '<span>' + escHtml(s.size_label) + '</span>' +
              '<span>' + updated + '</span>' +
            '</div>' +
          '</div>' +
          '<button class="resume-item-btn">Resume</button>' +
        '</div>';
      }).join('');
      return '<div class="resume-project">' +
        '<div class="resume-project-name">' + escHtml(p.project) + '</div>' +
        sessions +
      '</div>';
    }).join('');
  } catch(e) {
    el.innerHTML = '<div class="empty">Failed to load sessions</div>';
  }
}

function closeResumeModal() {
  document.getElementById('resume-modal').style.display = 'none';
}

async function resumeSession(sessionId, title, project) {
  closeResumeModal();
  const result = await api('POST', '/resume/start', {
    session_id: sessionId,
    title: title,
    project: project,
    mode: 'c',
  });
  if (result && !result.ok) {
    showToast('Resume failed', result.message || 'Unknown error');
  }
  refresh();
}

/* --- Schedules --- */

async function refreshSchedules() {
  try {
    const data = await api('GET', '/schedules');
    renderScheduleCards(data.schedules || []);
  } catch(e) {
    renderScheduleCards([]);
  }
}

/* --- Run Results Dashboard --- */

function _safeName(name) {
  return (name || '').replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();
}

function normalizeRun(run) {
  // Status - try many field names
  if (!run.status && run.completed_at) run.status = 'completed';
  if (!run.status && run.end_time) run.status = 'completed';
  if (!run.status && run.run_end) run.status = 'completed';
  if (!run.status && run.result) run.status = 'completed';
  if (!run.status && run.total_applications > 0) run.status = 'completed';
  if (!run.status && run.started_at) run.status = 'running';
  // Duration - try many field pairs
  if (run.duration_minutes == null) {
    var start = run.started_at || run.start_time || run.run_start || run.started;
    var end = run.completed_at || run.end_time || run.run_end || run.ended;
    if (start && end) run.duration_minutes = Math.round((new Date(end) - new Date(start)) / 60000);
  }
  // Summary - try many field names
  if (!run.summary && run.result && typeof run.result === 'string') run.summary = run.result;
  if (!run.summary && run.session_notes && typeof run.session_notes === 'string') {
    run.summary = run.session_notes.length > 120 ? run.session_notes.substring(0, 117) + '...' : run.session_notes;
  }
  if (!run.summary && run.total_applications != null) {
    run.summary = 'Applied to ' + run.total_applications + ' jobs';
  }
  if (!run.summary && run.applications_submitted != null) {
    run.summary = 'Applied to ' + run.applications_submitted + ' jobs';
  }
  if (!run.summary && run.applications && run.applications.length > 0) {
    var applied = run.applications.filter(function(a) { return a.status === 'applied'; }).length;
    run.summary = 'Applied to ' + applied + ' jobs';
  }
  // Timestamps - try many field names
  if (!run.started) run.started = run.started_at || run.start_time || run.run_start || run.date || run.run_date;
  // Session/ID
  if (!run.session && run.run_id) run.session = run.run_id;
  return run;
}

function runStatusIcon(status) {
  if (status === 'error' || status === 'failed') return '<span class="run-status-err">\u2717</span>';
  if (status === 'running') return '<span class="run-status-run">' + ICN.loader + '</span>';
  return '<span class="run-status-ok">\u2713</span>';
}

function formatRunTimestamp(iso) {
  if (!iso) return '';
  var d = parseUTC(iso);
  return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) + ', ' +
    String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}

function renderRunResults(runs, jobName) {
  if (!runs || runs.length === 0) return '';
  var id = 'runs-' + jobName.replace(/[^a-zA-Z0-9]/g, '-');
  var isOpen = expandedRuns.has(id);
  var html = '<div class="run-results">' +
    '<div class="run-results-title" onclick="toggleRuns(\'' + id + '\', this)" style="cursor:pointer">' +
      '<span class="run-toggle" id="toggle-' + id + '">' + (isOpen ? '\u25BC' : '\u25B6') + '</span> Recent Runs (' + runs.length + ')' +
    '</div>' +
    '<div class="run-results-body" id="' + id + '" style="display:' + (isOpen ? 'block' : 'none') + '">';
  runs.forEach(function(run, i) {
    run = normalizeRun(run);
    run._jobName = jobName;
    var prefix = i < runs.length - 1 ? '\u251C\u2500' : '\u2514\u2500';
    var ts = formatRunTimestamp(run.started);
    var dur = run.duration_minutes != null ? Math.round(run.duration_minutes) + 'min' : '';
    var summary = run.summary || '';
    var statusIcon = runStatusIcon(run.status);
    var viewRef = run._filename || run.session || '';
    html += '<div class="run-row">' +
      '<span class="run-tree">' + prefix + '</span> ' +
      '<span class="run-ts">' + escHtml(ts) + '</span> ' +
      statusIcon + ' ' +
      (dur ? '<span class="run-dur">' + escHtml(dur) + '</span> ' : '') +
      '<span class="run-summary">' + escHtml(summary) + '</span>' +
      '<button class="btn-sm btn-run-view" onclick="showRunDetail(\'' + escHtml(jobName.replace(/'/g, "\\'")) + '\', \'' + escHtml(viewRef.replace(/'/g, "\\'")) + '\')">View</button>' +
    '</div>';
  });
  html += '</div></div>';
  return html;
}

function toggleRuns(id, titleEl) {
  var body = document.getElementById(id);
  var toggle = document.getElementById('toggle-' + id);
  if (!body) return;
  if (body.style.display === 'none') {
    body.style.display = 'block';
    if (toggle) toggle.textContent = '\u25BC';
    expandedRuns.add(id);
  } else {
    body.style.display = 'none';
    if (toggle) toggle.textContent = '\u25B6';
    expandedRuns.delete(id);
  }
}

async function loadRunsForSchedule(scheduleName) {
  var jobName = _safeName(scheduleName);
  try {
    var runs = await api('GET', '/jobs/' + encodeURIComponent(jobName) + '/runs?limit=10');
    return { runs: runs, jobName: jobName };
  } catch(e) {
    return { runs: [], jobName: jobName };
  }
}

async function showRunDetail(jobName, sessionOrFilename) {
  document.getElementById('run-detail-modal').style.display = 'flex';
  var content = document.getElementById('run-detail-content');
  content.innerHTML = '<div class="empty">' + ICN.loader + ' Loading\u2026</div>';

  try {
    var run = await api('GET', '/jobs/' + encodeURIComponent(jobName) + '/runs/' + encodeURIComponent(sessionOrFilename));
    run = normalizeRun(run);
    if (!run.metrics && run.applications_submitted != null) {
      run.metrics = {applied: run.applications_submitted, evaluated: run.jobs_evaluated || 0, skipped: run.jobs_skipped || 0, searches: run.searches_run || 0};
    }
    if (!run.items && run.applications) run.items = run.applications;
    if (!run.items && run.jobs_applied) run.items = run.jobs_applied;
    var statusCls = run.status === 'error' || run.status === 'failed' ? 'run-badge-error' : run.status === 'running' ? 'run-badge-running' : 'run-badge-ok';
    var statusLabel = run.status || 'complete';
    var dur = run.duration_minutes != null ? Math.round(run.duration_minutes) + ' min' : '';
    var ts = formatRunTimestamp(run.started);

    var html = '<h3>' + escHtml(jobName) + '</h3>' +
      '<div class="run-detail-meta">' +
        (ts ? '<span>' + escHtml(ts) + '</span>' : '') +
        (dur ? '<span>' + escHtml(dur) + '</span>' : '') +
        '<span class="run-badge ' + statusCls + '">' + statusLabel.toUpperCase() + '</span>' +
      '</div>';

    // Build metrics from whatever fields exist
    if (!run.metrics && run.applications) {
      var applied = run.applications.filter(function(a) { return a.status === 'applied'; }).length;
      var skippedArr = run.skipped || [];
      run.metrics = { applied: applied, skipped: Array.isArray(skippedArr) ? skippedArr.length : (run.jobs_skipped || 0), searches: run.searches_run || run.keywords_used ? (run.keywords_used || []).length : 0 };
    }
    if (!run.metrics && run.applications_submitted != null) {
      run.metrics = {applied: run.applications_submitted, evaluated: run.jobs_evaluated || 0, skipped: run.jobs_skipped || 0, searches: run.searches_run || 0};
    }
    // Merge applications into items
    if (!run.items && run.applications) run.items = run.applications;
    if (!run.items && run.jobs_applied) run.items = run.jobs_applied;

    if (run.metrics) {
      html += '<div class="run-metrics">';
      for (var k in run.metrics) {
        html += '<div class="run-metric"><div class="run-metric-val">' + run.metrics[k] + '</div><div class="run-metric-label">' + k + '</div></div>';
      }
      html += '</div>';
    }

    if (run.summary) {
      html += '<div class="run-summary-block">' + escHtml(run.summary) + '</div>';
    }

    if (run.items && run.items.length > 0) {
      var appliedItems = run.items.filter(function(i) { return i.status !== 'skipped'; });
      var skippedItems = run.items.filter(function(i) { return i.status === 'skipped'; });
      if (appliedItems.length > 0) {
        html += '<div class="run-items-title">Applied</div><div class="run-items">';
        appliedItems.forEach(function(item) {
          var title = item.title || item.role || item.job_title || item.company || JSON.stringify(item);
          var company = item.company || '';
          var url = item.url || item.application_url || item.link || '';
          html += '<div class="run-item">' +
            '<span class="run-item-check">\u2713</span> ' +
            '<span>' + escHtml(title) + (company ? ' \u2014 ' + escHtml(company) : '') + '</span>' +
            (url ? ' <a href="' + escHtml(url) + '" target="_blank" class="run-item-link">Open \u2197</a>' : '') +
          '</div>';
        });
        html += '</div>';
      }
      if (skippedItems.length > 0) {
        html += '<div class="run-items-title">Skipped</div><div class="run-items">';
        skippedItems.forEach(function(item) {
          var title = item.title || item.role || item.job_title || item.company || JSON.stringify(item);
          var company = item.company || '';
          var reason = item.reason || '';
          var url = item.url || item.application_url || item.link || '';
          html += '<div class="run-item run-item-skipped">' +
            '<span class="run-item-skip">\u2717</span> ' +
            '<span>' + escHtml(title) + (company ? ' \u2014 ' + escHtml(company) : '') + '</span>' +
            (url ? ' <a href="' + escHtml(url) + '" target="_blank" class="run-item-link">Open \u2197</a>' : '') +
            (reason ? '<div class="run-item-reason">' + escHtml(reason) + '</div>' : '') +
          '</div>';
        });
        html += '</div>';
      }
    }

    if (run.key_findings && run.key_findings.length > 0) {
      html += '<div class="run-items-title">Key Findings</div><div class="run-items">';
      run.key_findings.forEach(function(f) {
        html += '<div class="run-item"><span class="run-item-bullet">\u2022</span> ' + escHtml(f) + '</div>';
      });
      html += '</div>';
    }

    // Log button
    var sessionName = run.session || run.run_id || '';
    if (sessionName) {
      html += '<button class="btn-sm btn-edit" style="margin-top:0.75rem;" onclick="loadRunLog(\'' + escHtml(jobName.replace(/'/g, "\\'")) + '\', \'' + escHtml(sessionName.replace(/'/g, "\\'")) + '\')">View Session Log</button>';
      html += '<pre class="run-log" id="run-log-content" style="display:none;"></pre>';
    }

    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = '<div class="empty">Failed to load run details</div>';
  }
}

async function loadRunLog(jobName, sessionName) {
  var logEl = document.getElementById('run-log-content');
  if (!logEl) return;
  if (logEl.style.display !== 'none') { logEl.style.display = 'none'; return; }
  logEl.style.display = 'block';
  logEl.textContent = 'Loading log\u2026';
  try {
    // Try session-name based log first, then timestamp-based
    var data = await api('GET', '/jobs/' + encodeURIComponent(jobName) + '/logs/' + encodeURIComponent(sessionName + '.log') + '?tail=200');
    if (data.content) {
      logEl.textContent = data.content;
      logEl.scrollTop = logEl.scrollHeight;
    } else {
      logEl.textContent = 'No log content.';
    }
  } catch(e) {
    logEl.textContent = 'Log not available.';
  }
}

function closeRunDetail() {
  document.getElementById('run-detail-modal').style.display = 'none';
}

function renderScheduleCards(schedules) {
  const el = document.getElementById('schedules');
  if (schedules.length === 0) {
    el.innerHTML = '<div class="empty">No scheduled tasks</div>';
    return;
  }
  // First render immediately, then load runs async
  el.innerHTML = schedules.map(s => _renderScheduleCard(s, '')).join('');
  // Load runs for each schedule
  schedules.forEach(function(s) {
    loadRunsForSchedule(s.name).then(function(result) {
      var runsHtml = renderRunResults(result.runs, result.jobName);
      var slot = document.getElementById('runs-' + s.id);
      if (slot) slot.innerHTML = runsHtml;
    });
  });
}

function _renderScheduleCard(s, runsHtml) {
    const hint = formatCronHint(s.cron);
    const lastRun = s.last_run ? formatRelativeTime(s.last_run) : 'Never';
    const nextRun = s.next_run ? formatRelativeTime(s.next_run) : 'N/A';
    const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
    const badgeClass = s.mode === 'ci' ? 'badge-ci' : s.mode === 'safe' ? 'badge-safe' : 'badge-c';
    const historyHtml = (s.history || []).slice(-3).reverse().map(h => {
      const cls = h.status === 'ok' ? 'history-ok' : 'history-error';
      return '<div class="history-item"><span class="' + cls + '">' + (h.status === 'ok' ? '\u2713' : '\u2717') + '</span> ' + formatRelativeTime(h.timestamp) + ' - ' + escHtml(h.message || '') + '</div>';
    }).join('');

    return '<div class="schedule-card">' +
      '<div class="schedule-header">' +
        '<span class="schedule-name">' + escHtml(s.name) + ' <span class="badge ' + badgeClass + '">' + modeLabel + '</span></span>' +
        '<label class="toggle"><input type="checkbox" ' + (s.enabled ? 'checked' : '') +
          ' onchange="toggleSchedule(\'' + s.id + '\', this.checked)"><span class="toggle-slider"></span></label>' +
      '</div>' +
      '<div class="schedule-meta">' +
        '<div>' + ICN.clock + ' <code>' + escHtml(s.cron) + '</code> <span class="cron-hint">(' + escHtml(hint) + ')</span></div>' +
        '<div>Last: ' + lastRun + ' &middot; Next: ' + nextRun + '</div>' +
        (s.workdir ? '<div>' + ICN.folder + ' ' + escHtml(s.workdir) + '</div>' : '') +
      '</div>' +
      (historyHtml ? '<div class="schedule-history">' + historyHtml + '</div>' : '') +
      '<div id="runs-' + s.id + '">' + runsHtml + '</div>' +
      '<div class="schedule-actions">' +
        '<button class="btn-sm btn-edit" onclick="openEditSchedule(\'' + s.id + '\')">' + ICN.edit + ' Edit</button>' +
        '<button class="btn-sm btn-fire" onclick="fireSchedule(\'' + s.id + '\')">' + ICN.play + ' Run Now</button>' +
        '<button class="btn-sm btn-del" onclick="deleteSchedule(\'' + s.id + '\')">' + ICN.trash + ' Delete</button>' +
      '</div>' +
    '</div>';
}

function applyCronPreset() {
  const preset = document.getElementById('sched-cron-preset').value;
  if (preset) {
    document.getElementById('sched-cron').value = preset;
    updateCronPreview();
  }
}

function onSchedProjectChange() {
  const sel = document.getElementById('sched-project-select');
  const input = document.getElementById('sched-workdir');
  const browserWrap = document.getElementById('sched-dir-browser-wrap');
  if (sel.value === '__browse__') {
    input.style.display = 'none';
    browserWrap.style.display = 'block';
    browsers.sched.selected = null;
    browseTo('sched', browsers.sched.path || '/');
  } else if (sel.value === '__custom__') {
    browserWrap.style.display = 'none';
    input.style.display = '';
    input.value = '';
    input.focus();
  } else if (sel.value) {
    browserWrap.style.display = 'none';
    input.style.display = 'none';
    input.value = sel.value;
  }
}

async function populateSchedProjects(currentWorkdir) {
  const wrap = document.getElementById('sched-project-wrap');
  const browserWrap = document.getElementById('sched-dir-browser-wrap');
  try {
    const data = await api('GET', '/projects');
    const projects = data.projects || [];
    const sel = document.getElementById('sched-project-select');
    sel.innerHTML = '';
    browsers.sched.path = data.default || '/';
    /* Default option */
    const defOpt = document.createElement('option');
    defOpt.value = data.default || '';
    defOpt.textContent = 'Default (' + (data.default_name || 'home') + ')';
    sel.appendChild(defOpt);
    /* Project options */
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.path;
      opt.textContent = p.name + (p.exists ? '' : ' (missing)');
      if (!p.exists) opt.disabled = true;
      sel.appendChild(opt);
    });
    /* Browse option */
    const browseOpt = document.createElement('option');
    browseOpt.value = '__browse__';
    browseOpt.textContent = 'Browse\u2026';
    sel.appendChild(browseOpt);
    /* Custom option */
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = 'Custom path\u2026';
    sel.appendChild(custom);
    wrap.style.display = '';
    /* Select matching project or show custom */
    const input = document.getElementById('sched-workdir');
    browserWrap.style.display = 'none';
    if (currentWorkdir) {
      const match = Array.from(sel.options).find(o => o.value === currentWorkdir && o.value !== '__browse__' && o.value !== '__custom__');
      if (match) {
        sel.value = currentWorkdir;
        input.value = currentWorkdir;
        input.style.display = 'none';
      } else {
        sel.value = '__custom__';
        input.value = currentWorkdir;
        input.style.display = '';
      }
    } else {
      sel.value = data.default || '';
      input.value = data.default || '';
      input.style.display = 'none';
    }
  } catch(e) {
    wrap.style.display = 'none';
  }
}

/* --- Wizard --- */

const SCHEDULE_PRESETS = [
  { label: "Every hour",         cron: "0 * * * *"   },
  { label: "Every 2 hours",      cron: "0 */2 * * *" },
  { label: "Every 6 hours",      cron: "0 */6 * * *" },
  { label: "Daily at 9 AM",      cron: "0 9 * * *"   },
  { label: "Daily at noon",      cron: "0 12 * * *"  },
  { label: "Daily at midnight",  cron: "0 0 * * *"   },
  { label: "Weekdays at 9 AM",   cron: "0 9 * * 1-5" },
  { label: "Weekly on Monday",   cron: "0 9 * * 1"   },
  { label: "Monthly on the 1st", cron: "0 0 1 * *"   },
];

let wizardStep = 0;
let wizSelectedPreset = null;

function openNewSchedule() {
  wizardStep = 0;
  wizSelectedPreset = null;
  document.getElementById('wiz-description').value = '';
  document.getElementById('wiz-name').value = '';
  document.getElementById('wiz-mode').value = 'c';
  document.getElementById('wiz-workdir').value = '';
  renderPresetGrid();
  populateWizProjects();
  updateWizardSteps();
  document.getElementById('wizard-modal').style.display = 'flex';
}

function closeWizard() {
  document.getElementById('wizard-modal').style.display = 'none';
}

function renderPresetGrid() {
  const grid = document.getElementById('wiz-preset-grid');
  grid.innerHTML = SCHEDULE_PRESETS.map((p, i) =>
    '<button class="preset-btn" data-idx="' + i + '" onclick="selectPreset(' + i + ')">' + escHtml(p.label) + '</button>'
  ).join('');
}

function selectPreset(idx) {
  wizSelectedPreset = idx;
  document.querySelectorAll('.preset-btn').forEach((btn, i) => {
    btn.classList.toggle('selected', i === idx);
  });
}

function updateWizardSteps() {
  for (let i = 0; i < 4; i++) {
    document.getElementById('wstep-' + i).classList.toggle('active', i === wizardStep);
    const dot = document.getElementById('wdot-' + i);
    dot.classList.toggle('active', i === wizardStep);
    dot.classList.toggle('done', i < wizardStep);
  }
}

function wizardNext() {
  if (wizardStep === 0) {
    const desc = document.getElementById('wiz-description').value.trim();
    if (!desc) return;
    const nameInput = document.getElementById('wiz-name');
    if (!nameInput.value.trim()) {
      nameInput.value = desc.split(/\s+/).slice(0, 4).join('-').toLowerCase().replace(/[^a-z0-9-]/g, '');
    }
  }
  if (wizardStep === 1 && wizSelectedPreset === null) return;
  wizardStep = Math.min(wizardStep + 1, 2);
  updateWizardSteps();
}

function wizardBack() {
  wizardStep = Math.max(wizardStep - 1, 0);
  updateWizardSteps();
}

function onWizProjectChange() {
  const sel = document.getElementById('wiz-project-select');
  const input = document.getElementById('wiz-workdir');
  const browserWrap = document.getElementById('wiz-dir-browser-wrap');
  if (sel.value === '__custom__') {
    input.style.display = 'none';
    browserWrap.style.display = 'block';
    browsers.wiz.selected = null;
    const startPath = browsers.wiz.path || '/';
    document.getElementById('wiz-dir-browser-input').value = startPath;
    browseTo('wiz', startPath);
  } else if (sel.value) {
    input.style.display = 'none';
    browserWrap.style.display = 'none';
    closeBrowser('wiz');
    input.value = sel.value;
  }
}

async function populateWizProjects() {
  const wrap = document.getElementById('wiz-project-wrap');
  try {
    const data = await api('GET', '/projects');
    const projects = data.projects || [];
    browsers.wiz.path = data.default || '/';
    const sel = document.getElementById('wiz-project-select');
    sel.innerHTML = '';
    const defOpt = document.createElement('option');
    defOpt.value = data.default || '';
    defOpt.textContent = 'Default (' + (data.default_name || 'home') + ')';
    sel.appendChild(defOpt);
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.path;
      opt.textContent = p.name + (p.exists ? '' : ' (missing)');
      if (!p.exists) opt.disabled = true;
      sel.appendChild(opt);
    });
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = 'Custom path\u2026';
    sel.appendChild(custom);
    wrap.style.display = '';
    const input = document.getElementById('wiz-workdir');
    sel.value = data.default || '';
    input.value = data.default || '';
    input.style.display = 'none';
  } catch(e) {
    wrap.style.display = 'none';
  }
}

async function wizardCreate() {
  const description = document.getElementById('wiz-description').value.trim();
  if (!description || wizSelectedPreset === null) return;

  const preset = SCHEDULE_PRESETS[wizSelectedPreset];
  const wizProjSel = document.getElementById('wiz-project-select');
  let workdir = document.getElementById('wiz-workdir').value.trim();
  if (wizProjSel && wizProjSel.value && wizProjSel.value !== '__custom__') {
    workdir = wizProjSel.value;
  } else if (wizProjSel && wizProjSel.value === '__custom__' && browsers.wiz.selected) {
    workdir = browsers.wiz.selected;
  }
  const mode = document.getElementById('wiz-mode').value;
  let name = document.getElementById('wiz-name').value.trim();
  if (!name) name = description.split(/\s+/).slice(0, 4).join('-').toLowerCase().replace(/[^a-z0-9-]/g, '');

  const btn = document.getElementById('wiz-create-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner spinner-sm"></span> Creating\u2026';

  const result = await api('POST', '/schedules/wizard', {
    description,
    schedule_label: preset.label,
    cron: preset.cron,
    workdir,
    mode,
    name,
  });

  const sessionName = result.name || name;

  let sessionUrl = null;
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const data = await api('GET', '/sessions');
    const s = (data.sessions || []).find(s => s.name === sessionName);
    if (s && s.url) {
      sessionUrl = s.url;
      break;
    }
  }

  btn.disabled = false;
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Create with Claude';

  const urlEl = document.getElementById('wiz-session-url');
  const linkEl = document.getElementById('wiz-open-link');
  if (sessionUrl) {
    urlEl.innerHTML = '<a href="' + escHtml(sessionUrl) + '" target="_blank">' + escHtml(sessionUrl) + '</a>';
    linkEl.href = sessionUrl;
    linkEl.style.display = '';
  } else {
    urlEl.innerHTML = '<span class="waiting">' + ICN.loader + ' Session starting\u2026 check the sessions list below.</span>';
    linkEl.style.display = 'none';
  }
  wizardStep = 3;
  updateWizardSteps();
  refresh();
}

async function openEditSchedule(id) {
  const data = await api('GET', '/schedules');
  const s = (data.schedules || []).find(x => x.id === id);
  if (!s) return;
  editingScheduleId = id;
  document.getElementById('modal-title').textContent = 'Edit Schedule';
  document.getElementById('sched-name').value = s.name || '';
  document.getElementById('sched-cron').value = s.cron || '';
  const presetSel = document.getElementById('sched-cron-preset');
  const presetMatch = Array.from(presetSel.options).find(o => o.value === s.cron);
  presetSel.value = presetMatch ? s.cron : '';
  document.getElementById('sched-prompt').value = s.prompt || '';
  document.getElementById('sched-file').value = s.instructions_file || '';
  document.getElementById('sched-mode').value = s.mode || 'c';
  document.getElementById('sched-model').value = s.model || '';
  updateCronPreview();
  populateSchedProjects(s.workdir || '');
  document.getElementById('schedule-modal').style.display = 'flex';
}

function closeModal() {
  document.getElementById('schedule-modal').style.display = 'none';
  editingScheduleId = null;
  closeBrowser('sched');
}

async function saveSchedule() {
  const schedProjSel = document.getElementById('sched-project-select');
  let workdir = document.getElementById('sched-workdir').value.trim();
  if (schedProjSel && schedProjSel.value && schedProjSel.value !== '__custom__' && schedProjSel.value !== '__browse__') {
    workdir = schedProjSel.value;
  }
  // If browse was used, take the selected path
  if (schedProjSel && schedProjSel.value === '__browse__' && browsers.sched.selected) {
    workdir = browsers.sched.selected;
  }
  const body = {
    name: document.getElementById('sched-name').value.trim(),
    cron: document.getElementById('sched-cron').value.trim(),
    prompt: document.getElementById('sched-prompt').value.trim(),
    instructions_file: document.getElementById('sched-file').value.trim() || null,
    workdir: workdir,
    mode: document.getElementById('sched-mode').value,
    model: document.getElementById('sched-model').value || null,
  };
  if (!body.name || !body.cron) {
    alert('Name and cron expression are required');
    return;
  }
  if (editingScheduleId) {
    body.id = editingScheduleId;
    await api('POST', '/schedules/update', body);
  } else {
    await api('POST', '/schedules', body);
  }
  closeModal();
  refreshSchedules();
}

async function deleteSchedule(id) {
  if (!confirm('Delete this schedule?')) return;
  await api('POST', '/schedules/delete', { id });
  refreshSchedules();
}

async function toggleSchedule(id, enabled) {
  await api('POST', '/schedules/update', { id, enabled });
  refreshSchedules();
}

async function fireSchedule(id) {
  await api('POST', '/schedules/fire', { id });
  refresh();
}

function utcHourToLocal(h) {
  // Convert a UTC hour (0-23) to local hour by using a Date object
  var d = new Date();
  d.setUTCHours(parseInt(h), 0, 0, 0);
  return d.getHours();
}

function formatCronHint(expr) {
  if (!expr) return '';
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return 'Invalid';
  const [min, hour, dom, mon, dow] = parts;
  const dowNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

  let timeStr = '';
  if (min === '*' && hour === '*') timeStr = 'Every minute';
  else if (min.startsWith('*/')) timeStr = 'Every ' + min.slice(2) + ' minutes';
  else if (hour === '*') timeStr = 'Every hour at :' + min.padStart(2,'0');
  else {
    const localHour = utcHourToLocal(hour);
    timeStr = 'At ' + localHour + ':' + min.padStart(2,'0');
  }

  let dayStr = '';
  if (dom === '*' && dow === '*') dayStr = '';
  else if (dow === '1-5') dayStr = ', weekdays';
  else if (dow === '0,6') dayStr = ', weekends';
  else if (dow !== '*') {
    const days = dow.split(',').map(d => dowNames[parseInt(d)] || d).join(', ');
    dayStr = ', on ' + days;
  }
  else if (dom !== '*') dayStr = ', day ' + dom;

  let monStr = '';
  if (mon !== '*') monStr = ', month ' + mon;

  return timeStr + dayStr + monStr;
}

function parseUTC(iso) {
  // Backend timestamps are UTC but lack 'Z' suffix — force UTC parsing
  if (iso && !iso.endsWith('Z') && !iso.includes('+') && !iso.includes('-', 10)) {
    return new Date(iso + 'Z');
  }
  return new Date(iso);
}

function formatRelativeTime(iso) {
  if (!iso) return 'N/A';
  const d = parseUTC(iso);
  const now = new Date();
  const diffMs = now - d;
  const absDiff = Math.abs(diffMs);
  const future = diffMs < 0;

  if (absDiff < 60000) return future ? 'in <1 min' : '<1 min ago';
  if (absDiff < 3600000) {
    const m = Math.floor(absDiff / 60000);
    return future ? 'in ' + m + ' min' : m + ' min ago';
  }
  if (absDiff < 86400000) {
    const h = Math.floor(absDiff / 3600000);
    return future ? 'in ' + h + 'h' : h + 'h ago';
  }
  const days = Math.floor(absDiff / 86400000);
  return future ? 'in ' + days + 'd' : days + 'd ago';
}

function updateCronPreview() {
  const expr = document.getElementById('sched-cron').value.trim();
  document.getElementById('cron-preview').textContent = expr ? formatCronHint(expr) : '';
}

// --- Session Preview ---
var _previewInterval = null;
var _previewSession = null;

function openPreview(sessionName) {
  _previewSession = sessionName;
  document.getElementById('session-preview-modal').style.display = 'flex';
  document.getElementById('preview-title').textContent = sessionName;
  document.getElementById('preview-output').textContent = 'Loading\u2026';
  document.getElementById('preview-auto-refresh').checked = true;
  refreshPreview();
  _previewInterval = setInterval(refreshPreview, 3000);
}

async function refreshPreview() {
  if (!_previewSession) return;
  try {
    var data = await api('GET', '/sessions/' + encodeURIComponent(_previewSession) + '/preview');
    var pre = document.getElementById('preview-output');
    var text = (data.output || '').replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '').replace(/\x1b[^[]/g, '');
    pre.textContent = text;
    pre.scrollTop = pre.scrollHeight;
  } catch(e) {
    document.getElementById('preview-output').textContent = 'Session not available.';
  }
}

function togglePreviewRefresh(on) {
  if (on && !_previewInterval) {
    _previewInterval = setInterval(refreshPreview, 3000);
  } else if (!on && _previewInterval) {
    clearInterval(_previewInterval);
    _previewInterval = null;
  }
}

function closePreview() {
  document.getElementById('session-preview-modal').style.display = 'none';
  if (_previewInterval) { clearInterval(_previewInterval); _previewInterval = null; }
  _previewSession = null;
}

loadProjects();
refresh();
fetch('/rc/version').then(r=>r.json()).then(d => {
  document.getElementById('version-label').textContent = 'v' + d.version;
  const vt = document.getElementById('version-label-top');
  if (vt) vt.textContent = 'v' + d.version;
}).catch(()=>{});

// Check for updates on load
function checkForUpdate() {
  var el = document.getElementById('update-status');
  if (el) el.innerHTML = '<span class="update-checking">checking\u2026</span>';
  api('GET', '/update-check').then(function(d) {
    if (!el) return;
    if (d.update_available) {
      el.innerHTML = '<button class="update-available" onclick="doUpdate()">v' + escHtml(d.latest) + ' \u2014 Update</button>';
    } else {
      el.innerHTML = '<span class="update-current">up to date</span>';
      setTimeout(function() { el.innerHTML = ''; }, 5000);
    }
  }).catch(function() { if (el) el.innerHTML = ''; });
}

async function doUpdate() {
  var el = document.getElementById('update-status');
  if (el) el.innerHTML = '<span class="update-checking">updating\u2026</span>';
  try {
    var d = await api('POST', '/update');
    if (d.ok) {
      if (el) el.innerHTML = '<span class="update-current">' + escHtml(d.message) + '</span>';
      setTimeout(function() { window.location.reload(); }, 3000);
    } else {
      if (el) el.innerHTML = '<span class="update-error">' + escHtml(d.message) + '</span>';
    }
  } catch(e) {
    // Server restarting — reload after a moment
    setTimeout(function() { window.location.reload(); }, 3000);
  }
}

checkForUpdate();
