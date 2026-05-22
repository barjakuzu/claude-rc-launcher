# React/TSX V4 "Ops Console" Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the RC Launcher frontend in React + TypeScript, matching the V4 "Ops Console — refined" design pixel-for-pixel and wired to the real backend at full feature parity.

**Architecture:** A Vite + React + TS app in `frontend/`, built at dev time to `static/dist/` (committed) so target devices need no Node. The Python server serves the new UI at `/` and the old UI at `/legacy`. Two additive stdlib-only endpoints — `/rc/stats` (per device) and `/rc/overview` (hub aggregator) — supply the dashboard data; all other features reuse existing proxy-aware `/rc/*` endpoints.

**Tech Stack:** Python 3 stdlib HTTP server (existing), Vite 5, React 18, TypeScript 5. Backend tests use Python's built-in `unittest`. Fonts: Geist / Geist Mono / Instrument Serif.

**Source of truth for visuals:** the committed prototype at `docs/design-reference/variant-ops-refined.jsx` and `docs/design-reference/tokens.jsx`. Frontend tasks port these to TSX preserving every inline style exactly; only data sources change as noted.

---

## File Structure

**Backend (modify):**
- `stats.py` (create) — token-history ring buffer + system stats helpers.
- `server.py` — add `/rc/stats`, `/rc/overview` routes; change `/` and add `/legacy`.
- `scheduler.py` — call the stats sampler once per loop iteration.
- `tests/test_stats.py`, `tests/test_overview.py` (create).

**Frontend (create under `frontend/`):**
- `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`.
- `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/tokens.ts`, `src/types.ts`, `src/useLayout.ts`.
- `src/components/` — `primitives.tsx`, `Header.tsx`, `Strip.tsx`, `Grid.tsx`, `SidePanel.tsx`, `MobileDetail.tsx`, `MiniLauncher.tsx`, `PanelTabs.tsx`, `SessionRow.tsx`, `ScheduledRow.tsx`, `Logs.tsx`, and parity modals `ScheduleModal.tsx`, `DirBrowser.tsx`, `PreviewModal.tsx`, `ShareTunnel.tsx`, `ResumeList.tsx`.
- Build output → `static/dist/` (committed). `frontend/node_modules`, vite cache gitignored.

---

## Stage 0 — Prep

### Task 0: Commit the design reference

**Files:**
- Create: `docs/design-reference/variant-ops-refined.jsx`, `docs/design-reference/tokens.jsx`, `docs/design-reference/README.md`

- [ ] **Step 1: Copy prototype files into the repo**

```bash
mkdir -p docs/design-reference
cp /tmp/design-extract/rc-launcher/project/variant-ops-refined.jsx docs/design-reference/
cp /tmp/design-extract/rc-launcher/project/tokens.jsx docs/design-reference/
printf '%s\n' "# V4 Ops Console design reference" "" "Frozen copy of the Claude Design prototype that the React/TSX UI ports from." "Visual source of truth — match inline styles exactly. See the design spec in docs/superpowers/specs/." > docs/design-reference/README.md
```

If `/tmp/design-extract` is gone, re-fetch is not required — the two `.jsx` files are the only inputs; recover them from the original handoff bundle. The plan's frontend tasks quote the relevant interfaces, but the exact pixel values live in these files.

- [ ] **Step 2: Commit**

```bash
git add docs/design-reference
git commit -m "docs: freeze V4 Ops Console prototype as design reference"
```

---

## Stage 1 — Backend data layer (TDD)

### Task 1: Token-history ring buffer + system stats

**Files:**
- Create: `stats.py`
- Test: `tests/test_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stats.py
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stats

class TokenHistoryTest(unittest.TestCase):
    def setUp(self):
        stats._HISTORY.clear()

    def test_sample_appends_and_caps_at_12(self):
        for i in range(15):
            stats.sample_tokens(lambda: i * 1000)
        hist = stats.token_history()
        self.assertEqual(len(hist), 12)
        self.assertEqual(hist[-1], 14000)   # most recent
        self.assertEqual(hist[0], 3000)     # oldest kept (i=3)

    def test_history_is_a_copy(self):
        stats.sample_tokens(lambda: 5)
        stats.token_history().append(999)
        self.assertEqual(stats.token_history(), [5])

class SystemStatsTest(unittest.TestCase):
    def test_system_stats_shape(self):
        s = stats.system_stats()
        self.assertIn("loadavg", s); self.assertEqual(len(s["loadavg"]), 3)
        self.assertIsInstance(s["cores"], int)
        self.assertTrue(s["cores"] >= 1)
        self.assertIsInstance(s["os"], str)
        self.assertTrue(len(s["os"]) > 0)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /var/www/rc-launcher && python3 -m unittest tests.test_stats -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stats'`.

- [ ] **Step 3: Write minimal implementation**

```python
# stats.py
"""Cheap per-device metrics: system load/OS + a token-history ring buffer."""

import os
import platform

_HISTORY = []      # last N summed-token samples
_MAX = 12

def sample_tokens(total_fn):
    """Append the current summed-token total (from total_fn()) to the ring buffer."""
    try:
        val = int(total_fn())
    except Exception:
        val = 0
    _HISTORY.append(val)
    if len(_HISTORY) > _MAX:
        del _HISTORY[: len(_HISTORY) - _MAX]

def token_history():
    """Return a copy of the token history (oldest → newest)."""
    return list(_HISTORY)

def _os_pretty():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()

def system_stats():
    """Return {loadavg:[1m,5m,15m], cores, os}."""
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = [0.0, 0.0, 0.0]
    return {"loadavg": load, "cores": os.cpu_count() or 1, "os": _os_pretty()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /var/www/rc-launcher && python3 -m unittest tests.test_stats -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add stats.py tests/test_stats.py
git commit -m "feat: add token-history ring buffer + system stats helpers"
```

### Task 2: `/rc/stats` endpoint + scheduler sampler

**Files:**
- Modify: `server.py` (do_GET dispatch; import `stats` and session helpers)
- Modify: `scheduler.py` (`_scheduler_loop` calls the sampler each iteration)

- [ ] **Step 1: Wire the sampler into the scheduler loop**

In `scheduler.py`, add at top: `import stats` and `from sessions import list_rc_sessions`. Inside `_scheduler_loop`, once per iteration (every 60s tick), add:

```python
        try:
            stats.sample_tokens(lambda: sum(s.get("tokens", 0) for s in list_rc_sessions()))
        except Exception:
            pass
```

(Place it next to the existing per-minute work; it must run regardless of whether any schedule fires.)

- [ ] **Step 2: Add the `/rc/stats` route in `server.py`**

Add `import stats` near the other imports. In `do_GET`, alongside the existing `elif path == "/version":` style branches, add:

```python
        elif path == "/stats":
            sess = list_rc_sessions()
            s = stats.system_stats()
            s["token_history"] = stats.token_history()
            s["tokens_now"] = sum(x.get("tokens", 0) for x in sess)
            s["sessions"] = len(sess)
            self._json(s)
```

`list_rc_sessions` is already imported in `server.py`.

- [ ] **Step 3: Verify against the running dev server**

Run:
```bash
cd /var/www/rc-launcher && RC_HOME=/tmp/rc-stats-test RC_PORT=8298 RC_HOST=127.0.0.1 \
  RC_AUTH_USER= RC_AUTH_PASS= python3 app.py >/tmp/stats.log 2>&1 &
sleep 2; curl -s http://127.0.0.1:8298/rc/stats; echo
# kill the specific PID printed by: pgrep -af RC_PORT=8298
```
Expected: JSON with `loadavg`, `cores`, `os`, `token_history` (array), `tokens_now`, `sessions`. Kill the test PID by exact pid (never `pkill app.py`).

- [ ] **Step 4: Commit**

```bash
git add server.py scheduler.py
git commit -m "feat: add /rc/stats endpoint and per-minute token sampler"
```

### Task 3: `/rc/overview` hub aggregator (TDD)

**Files:**
- Create: `overview.py` (pure aggregation logic, testable without sockets)
- Modify: `server.py` (route → `overview.build_overview(...)`)
- Test: `tests/test_overview.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_overview.py
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overview

class OverviewTest(unittest.TestCase):
    def test_card_from_parts_online(self):
        card = overview.card_from_parts(
            device={"id": "home", "name": "Home", "base_url": "http://tba-lin.ts.net:8200"},
            sessions=[{"tokens": 1000}, {"tokens": 500}],
            stats={"loadavg": [2.0, 1.0, 1.0], "cores": 4, "os": "Ubuntu 24.04", "token_history": [1, 2, 3]},
        )
        self.assertEqual(card["id"], "home")
        self.assertEqual(card["hostname"], "tba-lin.ts.net")
        self.assertTrue(card["online"])
        self.assertEqual(card["sessions"], 2)
        self.assertEqual(card["tokens"], 1500)
        self.assertEqual(card["loadPct"], 50)          # 2.0 / 4 cores
        self.assertEqual(card["os"], "Ubuntu 24.04")
        self.assertEqual(card["spark"], [1, 2, 3])

    def test_card_offline_when_no_stats(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=None, stats=None,
        )
        self.assertFalse(card["online"])
        self.assertEqual(card["sessions"], 0)
        self.assertEqual(card["tokens"], 0)
        self.assertEqual(card["spark"], [])

    def test_loadpct_caps_at_100(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=[], stats={"loadavg": [9.0], "cores": 2, "os": "o", "token_history": []},
        )
        self.assertEqual(card["loadPct"], 100)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /var/www/rc-launcher && python3 -m unittest tests.test_overview -v`
Expected: FAIL — `No module named 'overview'`.

- [ ] **Step 3: Write `overview.py`**

```python
# overview.py
"""Hub-side aggregation: combine each device's sessions + stats into grid cards."""

from urllib.parse import urlparse

def card_from_parts(device, sessions, stats):
    """Build one grid card. sessions/stats are None when the device is unreachable."""
    online = stats is not None
    sess = sessions or []
    tokens = sum(int(s.get("tokens", 0)) for s in sess)
    load_pct = 0
    os_name, spark = "", []
    if stats:
        cores = max(1, int(stats.get("cores", 1)))
        load1 = (stats.get("loadavg") or [0])[0]
        load_pct = min(100, round((load1 / cores) * 100))
        os_name = stats.get("os", "")
        spark = stats.get("token_history") or []
    host = urlparse(device.get("base_url", "")).hostname or device.get("base_url", "")
    return {
        "id": device["id"], "name": device.get("name", device["id"]),
        "online": online, "hostname": host,
        "sessions": len(sess), "tokens": tokens,
        "loadPct": load_pct, "os": os_name, "spark": spark,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /var/www/rc-launcher && python3 -m unittest tests.test_overview -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add concurrent fetch + route in `server.py`**

In `overview.py` add the I/O layer (not unit-tested; verified live in Step 7):

```python
import base64, json, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

def _fetch(base_url, path, auth_user, auth_pass, timeout=3):
    req = urllib.request.Request(base_url.rstrip("/") + path)
    if auth_user or auth_pass:
        tok = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def fetch_remote_card(device):
    try:
        sess = _fetch(device["base_url"], "/rc/sessions", device.get("auth_user",""), device.get("auth_pass",""))
        st   = _fetch(device["base_url"], "/rc/stats",    device.get("auth_user",""), device.get("auth_pass",""))
        return card_from_parts(device, sess.get("sessions", []), st)
    except Exception:
        return card_from_parts(device, None, None)

def build_overview(local_device, local_sessions, local_stats, remote_devices):
    cards = [card_from_parts(local_device, local_sessions, local_stats)]
    if remote_devices:
        with ThreadPoolExecutor(max_workers=min(8, len(remote_devices))) as ex:
            cards += list(ex.map(fetch_remote_card, remote_devices))
    return cards
```

In `server.py` `do_GET`, add (after the `/stats` branch):

```python
        elif path == "/overview":
            import overview
            local_sess = list_rc_sessions()
            local_stats = {**stats.system_stats(),
                           "token_history": stats.token_history()}
            local_card = {"id": "local", "name": "This machine (VM)", "base_url": ""}
            cards = overview.build_overview(local_card, local_sess, local_stats, load_devices())
            self._json({"devices": cards})
```

`load_devices` is already imported from `devices`.

- [ ] **Step 6: Commit**

```bash
git add overview.py server.py tests/test_overview.py
git commit -m "feat: add /rc/overview hub aggregator with concurrent device fan-out"
```

- [ ] **Step 7: Verify live on the running hub**

Run: `curl -s -u "$(grep ^RC_AUTH_USER= /root/.claude-rc/env|cut -d= -f2-):$(grep ^RC_AUTH_PASS= /root/.claude-rc/env|cut -d= -f2-)" http://127.0.0.1:8200/rc/overview | python3 -m json.tool`
Expected: a `devices` array — `local` plus `home`, each with `online`, `sessions`, `tokens`, `loadPct`, `os`, `spark`. (This requires the hub running this code; deploy happens after the frontend is built. For now, run against a local test instance with a devices.json as in Task 2 Step 3.)

---

## Stage 2 — Frontend scaffold + serving

### Task 4: Scaffold Vite + React + TS

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Modify: `.gitignore`

- [ ] **Step 1: Create the project files**

`frontend/package.json`:
```json
{
  "name": "rc-launcher-frontend",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "tsc -b && vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": {
    "@types/react": "^18.3.3", "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1", "typescript": "^5.5.3", "vite": "^5.4.0"
  }
}
```

`frontend/vite.config.ts`:
```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  base: '/static/dist/',
  build: { outDir: '../static/dist', emptyOutDir: true },
});
```

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020", "useDefineForClassFields": true, "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext", "skipLibCheck": true, "moduleResolution": "bundler",
    "resolveJsonModule": true, "isolatedModules": true, "noEmit": true, "jsx": "react-jsx",
    "strict": true, "noUnusedLocals": true, "noUnusedParameters": true, "noFallthroughCasesInSwitch": true
  },
  "include": ["src"], "references": [{ "path": "./tsconfig.node.json" }]
}
```

`frontend/tsconfig.node.json`:
```json
{ "compilerOptions": { "composite": true, "skipLibCheck": true, "module": "ESNext", "moduleResolution": "bundler", "allowSyntheticDefaultImports": true }, "include": ["vite.config.ts"] }
```

`frontend/index.html` (fonts + root + global resets from the prototype `index.html`):
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Claude RC · Multi-device</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet" />
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: oklch(0.155 0.004 80); }
  body { font-family: 'Geist', system-ui, sans-serif; -webkit-font-smoothing: antialiased; color: oklch(0.96 0.004 80); }
  * { box-sizing: border-box; }
  ::selection { background: rgba(201,100,66,.25); }
  #root { height: 100%; }
</style>
</head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```

`frontend/src/main.tsx`:
```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
```

`frontend/src/App.tsx` (placeholder until Task 8):
```tsx
export function App() { return <div style={{ padding: 24 }}>RC Launcher — V4 (scaffold)</div>; }
```

Append to `.gitignore`:
```
frontend/node_modules
frontend/.vite
*.tsbuildinfo
```

- [ ] **Step 2: Install + build**

Run: `cd /var/www/rc-launcher/frontend && npm install && npm run build`
Expected: `../static/dist/index.html` + `../static/dist/assets/*` produced, no TS errors.

- [ ] **Step 3: Commit (including built dist)**

```bash
cd /var/www/rc-launcher
git add frontend .gitignore static/dist
git commit -m "build: scaffold Vite+React+TS frontend, base=/static/dist/"
```

### Task 5: Serve new UI at `/`, old UI at `/legacy`

**Files:**
- Modify: `server.py` (`do_GET` `/` branch + new `/legacy` branch + static dir for dist)

- [ ] **Step 1: Update routing**

In `server.py` `do_GET`, replace the `if path == "/":` body so it serves the built SPA, and add `/legacy`:

```python
        if path == "/":
            return self._serve_static("/static/dist/index.html")
        elif path == "/legacy":
            auth_hdr = self.headers.get("Authorization", "")
            return self._html(_load_html(auth_hdr))   # old static/index.html
```

Confirm `_serve_static` resolves `/static/dist/index.html` (it serves any `/static/*` path under the static dir). The old `_load_html` path stays for `/legacy`.

- [ ] **Step 2: Verify both UIs load**

Run (against a no-auth local instance as in Task 2 Step 3, after `npm run build`):
```bash
curl -s http://127.0.0.1:8298/ | grep -o '/static/dist/assets/[^"]*' | head
curl -s http://127.0.0.1:8298/legacy | grep -o 'id="device-select"' | head
```
Expected: `/` references `/static/dist/assets/*`; `/legacy` contains the old markup.

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: serve V4 SPA at / and old UI at /legacy"
```

---

## Stage 3 — Frontend foundations

### Task 6: Types + API client

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/api.ts`

- [ ] **Step 1: Define types**

`frontend/src/types.ts`:
```ts
export interface DeviceCard {
  id: string; name: string; online: boolean; hostname: string;
  sessions: number; tokens: number; loadPct: number; os: string; spark: number[];
}
export interface Session {
  name: string; mode: string; url?: string; status?: string;
  tokens?: number; workdir?: string; sessionId?: string; pct?: number;
}
export interface Schedule {
  id: string; name: string; cron: string; enabled: boolean;
  mode?: string; workdir?: string; next_run?: string; device?: string;
}
```

- [ ] **Step 2: API client**

`frontend/src/api.ts`:
```ts
async function req(method: string, path: string, device?: string, body?: unknown) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (device && device !== 'local') headers['X-RC-Device'] = device;
  const opts: RequestInit = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/rc' + path, opts);
  if (r.status === 401) { window.location.href = '/login'; throw new Error('auth'); }
  return r.json();
}
export const api = {
  overview: () => req('GET', '/overview'),
  sessions: (device: string) => req('GET', '/sessions', device),
  schedules: (device: string) => req('GET', '/schedules', device),
  stats: (device: string) => req('GET', '/stats', device),
  projects: (device: string) => req('GET', '/projects', device),
  browse: (device: string, path: string) => req('GET', '/browse?path=' + encodeURIComponent(path), device),
  preview: (device: string, name: string) => req('GET', `/sessions/${encodeURIComponent(name)}/preview`, device),
  start: (device: string, body: unknown) => req('POST', '/start', device, body),
  stop: (device: string, name: string) => req('POST', '/stop', device, { name }),
  restart: (device: string, name: string) => req('POST', '/restart', device, { name }),
  unstick: (device: string, name: string) => req('POST', '/unstick', device, { name }),
  stopAll: (device: string) => req('POST', '/stop-all', device),
  resumeList: (device: string) => req('GET', '/resume/list', device),
  resumeStart: (device: string, body: unknown) => req('POST', '/resume/start', device, body),
  schedCreate: (device: string, body: unknown) => req('POST', '/schedules', device, body),
  schedUpdate: (device: string, body: unknown) => req('POST', '/schedules/update', device, body),
  schedDelete: (device: string, id: string) => req('POST', '/schedules/delete', device, { id }),
  schedFire: (device: string, id: string) => req('POST', '/schedules/fire', device, { id }),
  tunnelStatus: () => req('GET', '/tunnel/status'),
  tunnelStart: () => req('POST', '/tunnel/start'),
  tunnelStop: () => req('POST', '/tunnel/stop'),
  updateCheck: () => req('GET', '/update-check'),
  update: () => req('POST', '/update'),
};
```

Note: verify exact resume/list/start paths against `server.py` during implementation; adjust if the route names differ.

- [ ] **Step 3: Type-check + commit**

Run: `cd frontend && npx tsc -b`
Expected: no errors.
```bash
cd /var/www/rc-launcher && git add frontend/src/types.ts frontend/src/api.ts && git commit -m "feat: frontend types + typed /rc API client"
```

### Task 7: Tokens, layout hook, primitives

**Files:**
- Create: `frontend/src/tokens.ts`, `frontend/src/useLayout.ts`, `frontend/src/components/primitives.tsx`

- [ ] **Step 1: Port tokens + helpers**

`frontend/src/tokens.ts` — port the `RT` palette and `tintFor/tintSoft/tintEdge` from `docs/design-reference/variant-ops-refined.jsx` lines 10–30, and `fmtK`/`fmtPct`/`capColor` plus the `FN` status colors from `docs/design-reference/tokens.jsx` lines 9–14, 137–145. Add `FONT_SANS/MONO/SERIF` (tokens.jsx lines 4–6) and a stable hue helper:
```ts
export const hueForId = (id: string) => {
  let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360; return h;
};
```
Export everything as named exports (no `window` globals).

- [ ] **Step 2: Layout hook**

`frontend/src/useLayout.ts` — full-window version of the prototype's `useLayout` (variant lines 33–45), using `window.innerWidth` + a `resize` listener:
```ts
import { useEffect, useState } from 'react';
export function useLayout() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener('resize', on); return () => window.removeEventListener('resize', on);
  }, []);
  return { width: w, mobile: w < 720, tablet: w >= 720 && w < 1100, desktop: w >= 1100 };
}
```

- [ ] **Step 3: Primitives**

`frontend/src/components/primitives.tsx` — port `Dot`, `Sparkline`, `CapBar`, `Icons`, `StatusPill` from `docs/design-reference/tokens.jsx` lines 149–258 to TSX with explicit prop types. Inject the keyframes (`rc-pulse`, `rc-spin`, `rc-shimmer`) once via a module-level effect or a `<style>` in `App`. Keep every style value identical.

- [ ] **Step 4: Type-check + commit**

Run: `cd frontend && npx tsc -b` → no errors.
```bash
git add frontend/src && git commit -m "feat: design tokens, useLayout hook, UI primitives"
```

---

## Stage 4 — Shell (header, strip, grid) wired to /rc/overview

### Task 8: App shell + overview polling

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/Header.tsx`, `Strip.tsx`, `Grid.tsx`

- [ ] **Step 1: App state + polling**

Rewrite `App.tsx` to own: `cards: DeviceCard[]` (poll `api.overview()` every 5s), `openId` (selected device or null), `tab`. Render `Header`, `Strip` (desktop only), `Grid`, and the side panel (Stage 5). Use `useLayout()`. Port the root container styles from variant lines 55–78.

- [ ] **Step 2: Header + MachineSelector**

`Header.tsx` — port `RHeader` + `MachineSelector` + `DropItem` (variant lines 82–237). Data: build the selector list from `cards` (id/name/online/tokens/hostname). "All devices" → `openId=null`; selecting a device → `openId=d.id`. "Add a device…" item links to `/legacy` schedule/device docs for now (full add-flow is config-edit; show the `devices.json` hint). Add the Share, version-chip, and "⋯" menu placeholders (wired in Stage 6).

- [ ] **Step 3: Strip + Grid + DeviceCard**

`Strip.tsx` — port `RStrip` (lines 240–266) computing cells from aggregate of `cards` (online x/N, Σ sessions, Σ tokens, avg loadPct). `Grid.tsx` — port `RGrid` + `RDeviceCard` + `RMobileStrip` (lines 269–390). Map card fields: sparkline← `card.spark`, tokens← `card.tokens`, bottom bar← `card.loadPct` (label `{loadPct}% load · {sessions} sess`), hue← `hueForId(card.id)`, icon← from `card.os` (`/mac/i`→laptop, `/ubuntu|debian|linux/i`→server, else server). Clicking a card sets `openId`.

- [ ] **Step 4: Build + visual verify**

Run: `cd frontend && npm run build` → no errors.
Manually load via a local no-auth instance with a `devices.json` (two devices) and confirm grid renders with live data. (Screenshots optional — the user can open the running instance.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src static/dist && git commit -m "feat: V4 shell — header/selector, aggregate strip, device grid wired to /rc/overview"
```

---

## Stage 5 — Side panel, tabs, sessions, launcher

### Task 9: Side panel + mobile detail + tabs

**Files:**
- Create: `frontend/src/components/SidePanel.tsx`, `MobileDetail.tsx`, `PanelTabs.tsx`, `Logs.tsx`

- [ ] **Step 1: Panels**

Port `RSidePanel` (lines 393–409), `RMobileDetail` (412–440), `RPanelHeader` (442–464), `RPanelTabs` (500–521), `RPanelBody` shell (523–551). The open device's data comes from `cards.find(id)` for the header and from `api.sessions(device)` + `api.schedules(device)` polled while open. `Logs.tsx` ← `RPanelBody` logs branch, fed by `api.stats(device)` (load, os, tokens_now, sessions) rendered as the log-style lines.

- [ ] **Step 2: Session rows + actions**

`SessionRow.tsx` — port `RSessionRow` (553–583). Wire the four action buttons + a per-row "⋯" overflow: copy `sessionId` (clipboard), open `url` (link icon → `window.open`), restart (`api.restart`), stop (`api.stop`), and in "⋯": unstick (`api.unstick`), resume context, preview (opens `PreviewModal`). After an action, refetch sessions. Map session fields: `s.name`, `s.status`, `s.workdir`→dir basename, `s.tokens`→`tokensK`, `s.pct` (compute from tokens if absent), `s.sessionId` (from `s.url` tail or `s.sessionId`).

- [ ] **Step 3: Build + commit**

Run: `cd frontend && npm run build` → no errors.
```bash
git add frontend/src static/dist && git commit -m "feat: device side panel, tabs, session rows with live actions"
```

### Task 10: Launcher with progressive disclosure

**Files:**
- Create: `frontend/src/components/MiniLauncher.tsx`, `DirBrowser.tsx`

- [ ] **Step 1: Mini-launcher**

Port `RMiniLauncher` (lines 466–498). Minimal row: workdir input (default from `api.projects(device).default`) + mode `<select>` (STANDARD/TEAMMATE/SAFE) + Launch. Add an "Options" toggle revealing: model `<select>` (Default/Sonnet/Haiku → 1/2/3), name input, sandbox checkbox, and a "Browse…" button opening `DirBrowser`. Launch → `api.start(device, { name, mode: {STANDARD:'c',TEAMMATE:'ci',SAFE:'safe'}[mode], model, workdir, sandbox })` then refetch sessions and switch to the Sessions tab.

- [ ] **Step 2: Directory browser**

`DirBrowser.tsx` — a small modal/panel calling `api.browse(device, path)` (returns `{path, parent, dirs}`); clicking a dir navigates, "Select" sets the launcher workdir. Style to match panel cards (card bg, border, mono font).

- [ ] **Step 3: Build + commit**

Run: `cd frontend && npm run build` → no errors.
```bash
git add frontend/src static/dist && git commit -m "feat: launcher with options expander + directory browser"
```

---

## Stage 6 — Parity: schedules, tunnel, preview, resume, update, stop-all

### Task 11: Schedule CRUD

**Files:**
- Create: `frontend/src/components/ScheduledRow.tsx`, `ScheduleModal.tsx`

- [ ] **Step 1: Scheduled rows**

Port `RScheduledRow` (lines 585–607). Add action buttons per row: enable/disable toggle (`api.schedUpdate` with `enabled`), fire now (`api.schedFire`), edit (opens modal), delete (`api.schedDelete`, confirm).

- [ ] **Step 2: Schedule modal**

`ScheduleModal.tsx` — create/edit form matching panel styling: name, cron (with the same preset `<select>` options the legacy UI uses — read them from `static/app.js` schedule wizard during implementation), mode, workdir (+ DirBrowser), enabled. Submit → `api.schedCreate` / `api.schedUpdate`; then refetch schedules.

- [ ] **Step 3: Build + commit**

Run: `cd frontend && npm run build` → no errors.
```bash
git add frontend/src static/dist && git commit -m "feat: schedule CRUD (rows + create/edit modal) in V4 panel"
```

### Task 12: Tunnel share, preview, resume, update, stop-all, logout

**Files:**
- Create: `frontend/src/components/ShareTunnel.tsx`, `PreviewModal.tsx`, `ResumeList.tsx`
- Modify: `frontend/src/components/Header.tsx`

- [ ] **Step 1: Header actions**

Wire the header Share button → `ShareTunnel` (poll `api.tunnelStatus`, start/stop, show + copy URL). Version chip → `api.updateCheck` on mount; if `update_available` show "vX — Update" → `api.update()`. "⋯" menu → Stop all (`api.stopAll(device)` for current/all), Logout (`window.location.href='/logout'`), global refresh, link to `/legacy`.

- [ ] **Step 2: Preview + resume**

`PreviewModal.tsx` — fetch `api.preview(device, name)`, show output in a mono `<pre>`, optional 3s auto-refresh (port the behavior from legacy `app.js` preview). `ResumeList.tsx` — `api.resumeList(device)` → list, "Resume" → `api.resumeStart`. Surface resume from the Sessions tab header ("Resume…" button) and preview from the row "⋯".

- [ ] **Step 3: Build + commit**

Run: `cd frontend && npm run build` → no errors.
```bash
git add frontend/src static/dist && git commit -m "feat: tunnel share, preview, resume, update-check, stop-all in V4"
```

---

## Stage 7 — Responsive QA, deploy, cutover

### Task 13: Responsive + end-to-end verification

- [ ] **Step 1: Build clean**

Run: `cd frontend && npm run build` → zero TS errors, dist updated. Commit dist if changed.

- [ ] **Step 2: Deploy to hub + verify live**

```bash
git push origin main
cd /root/.claude-rc/app && git pull --ff-only
sudo systemctl restart claude-rc-launcher && sleep 3 && systemctl is-active claude-rc-launcher
```
Then bump `VERSION` in `config.py` (so the home box update restarts) and pull on `tba-lin` too:
```bash
timeout 40 ssh barjazz@tba-lin 'export XDG_RUNTIME_DIR=/run/user/1000; cd ~/.claude-rc/app && git pull --ff-only && systemctl --user restart claude-rc'
```

- [ ] **Step 3: E2E checks (mirror the multi-device deploy verification)**

Via `https://claude.barjazz.dev` (and localhost): grid shows both devices online with live tokens/load; switch device; launch a session on `home` through the panel and confirm in `tba-lin` tmux; run stop/restart/unstick/preview; create→fire→delete a schedule; start/stop tunnel; trigger update-check. Confirm `/legacy` still works.

- [ ] **Step 4: Responsive sweep**

Resize the browser to ~1440 (3-col + side panel), ~900 (2-col), ~430 (1-col + full-screen detail). Confirm layout switches and the mobile detail overlay opens/closes.

- [ ] **Step 5: Update docs**

Update `CLAUDE.md` (frontend is now React/TS in `frontend/`, built to `static/dist/`, served at `/`; old UI at `/legacy`; `npm run build` before commit) and the Obsidian note. Commit.

### Task 14: Remove legacy (after parity confirmed)

- [ ] **Step 1:** Once the user confirms parity, remove the `/legacy` route, delete old `static/index.html` / `app.js` / `style.css`, and drop the "Classic UI" link. Commit: `chore: remove legacy UI after V4 parity`. (Hold this task until the user signs off.)

---

## Self-Review

- **Spec coverage:** serving/coexistence (Tasks 4–5), `/rc/stats` (Tasks 1–2), `/rc/overview` (Task 3), tokens/primitives/api (Tasks 6–7), shell (Task 8), panel+sessions+launcher (Tasks 9–10), full parity — schedules/tunnel/preview/resume/update/stop-all (Tasks 11–12), responsive + deploy + cutover (Tasks 13–14). All spec sections mapped.
- **Placeholders:** frontend visual steps reference exact prototype line ranges in committed `docs/design-reference/*` rather than re-pasting ~700 lines; data-wiring deltas, interfaces, and API signatures are given in full. Two explicit "verify exact route name during implementation" notes (resume paths, schedule cron presets) point at the authoritative source files.
- **Type consistency:** `DeviceCard`/`Session`/`Schedule` (Task 6) are used consistently; `api.*` signatures in Task 6 match call sites in Tasks 8–12; backend `card_from_parts` keys match the `DeviceCard` interface.
