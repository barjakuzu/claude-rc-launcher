# React/TSX V4 "Ops Console" Redesign — Design Spec

_2026-05-22_

## Context

The RC Launcher frontend is today a single vanilla-JS app (`static/index.html` +
`static/app.js` + `static/style.css`) served directly by the Python stdlib HTTP
server. Since adding the multi-device hub (v1.4.0), the UI exposes a basic device
dropdown but the visual design hasn't kept pace with the product becoming a
multi-machine control center.

The user mocked up a redesign in Claude Design and chose **V4 · Ops Console —
refined**: a dark, professional multi-device dashboard with a top machine
selector as primary nav, an aggregate stats strip, a responsive device-card grid,
and a slide-in side panel (full-screen on mobile) containing a launcher and
Sessions / Scheduled / Logs tabs. The prototype is in
`/tmp/design-extract/rc-launcher/project/` (primary file `variant-ops-refined.jsx`,
shared primitives/tokens in `tokens.jsx`).

Goal: rebuild the frontend in **React + TypeScript**, matching V4 pixel-for-pixel,
wired to the real backend, **at full feature parity** with the current UI.

## Decisions (confirmed with user)

1. **Build at dev time, commit `dist/`.** Vite + React + TS. The build runs on the
   dev machine and the bundled output is committed. Target devices need **no Node** —
   `git pull` + restart deploy model is preserved unchanged.
2. **Wire real data + cheap extras.** Use real backend data; add a small per-device
   stats endpoint for cheap real metrics (loadavg, OS, token history). No faked data.
3. **Full feature parity, one migration.** Every current feature is rebuilt inside
   the V4 shell (not dropped). `/legacy` serves the old UI as a temporary safety net,
   removed once parity is verified.
4. **Progressive disclosure** in the launcher: minimal row (workdir + mode + Launch);
   model / name / sandbox / directory-browser behind an "Options" expander.

## Architecture

### Serving & coexistence

- New `frontend/` directory: Vite + React + TypeScript project.
- Vite configured with **`base: '/static/dist/'`** → all asset URLs become
  `/static/dist/assets/…`, which the existing `_serve_static` in `server.py` already
  serves. `build.outDir` → `../static/dist`.
- `server.py` routing changes (minimal):
  - `GET /` → serve `static/dist/index.html` (new V4 UI).
  - `GET /legacy` → serve old `static/index.html` (fallback; old `app.js`/`style.css`
    stay in `static/` and load as today).
  - Static serving of `/static/dist/*` works via the existing handler.
- The committed `dist/` is the only build artifact; `frontend/node_modules` and
  Vite caches are gitignored.

### Backend changes (additive, stdlib-only)

- **`GET /rc/stats`** — served by *every* device app. Returns
  `{ loadavg: [1m,5m,15m], cores, os: "<pretty name>", token_history: number[] }`.
  - `loadavg` via `os.getloadavg()`; `cores` via `os.cpu_count()`; `os` from
    `/etc/os-release` PRETTY_NAME (fallback `platform.platform()`).
  - `token_history`: in-memory ring buffer (last 12 samples) of summed live-session
    tokens, appended once per minute by the existing scheduler thread
    (`scheduler.py:_scheduler_loop`). No persistence needed.
- **`GET /rc/overview`** — hub-only aggregator. Concurrently (threads, ~3s timeout
  each) queries every registered device (local + remotes via the existing proxy
  path) for `/rc/sessions` + `/rc/stats`, returning one array:
  ```
  [{ id, name, online, hostname, sessions, tokens, loadPct, os, spark[] }]
  ```
  Offline/unreachable devices return `online:false` quickly. Local device is always
  online. This single call fills the grid + aggregate strip.
- Existing endpoints are reused unchanged (all already proxy-aware via `X-RC-Device`):
  `/rc/devices`, `/rc/sessions`, `/rc/start`, `/rc/stop`, `/rc/restart`,
  `/rc/unstick`, `/rc/resume/*`, `/rc/sessions/:name/preview`, `/rc/schedules` (+
  create/update/delete/fire), `/rc/tunnel/*`, `/rc/projects`, `/rc/browse`,
  `/rc/update-check`, `/rc/update`, `/rc/version`.

### Frontend structure (`frontend/src/`)

- `main.tsx`, `App.tsx` — root + global state (selected device, open panel, tab).
- `api.ts` — typed fetch wrapper hitting `/rc/*`; attaches `X-RC-Device: <id>` for
  device-scoped calls; handles 401 → redirect `/login`, 502 → "device unreachable".
- `tokens.ts` — the V4 `RT` palette (OKLCH), fonts (Geist / Geist Mono / Instrument
  Serif), and helpers: `fmtK`, `fmtPct`, `capColor`, `tintFor/tintSoft/tintEdge`,
  stable `hueForId`.
- `useLayout.ts` — window-width hook → `{ mobile (<720), tablet (720–1099), desktop }`.
- `types.ts` — `Device`, `Session`, `Schedule`, `Overview` interfaces.
- Components mirroring the prototype (same visual output):
  - primitives: `Dot`, `Sparkline`, `CapBar`, `Icons`, `StatusPill`.
  - `Header` + `MachineSelector`, `Strip`, `Grid` + `DeviceCard`,
    `SidePanel` / `MobileDetail`, `MiniLauncher`, `PanelTabs`,
    `SessionRow`, `ScheduledRow`, `Logs`.
  - parity modals: `LauncherOptions` (expander), `ScheduleModal` (create/edit + cron
    presets + dir browser), `DirBrowser`, `PreviewModal`, `ShareTunnel`, `ResumeList`.
- Polling: `/rc/overview` every 5s; the open device panel polls `/rc/sessions` +
  `/rc/schedules` for that device.

### Design → real-data mapping

| V4 element | Real source |
|---|---|
| Machine selector + grid | `/rc/overview` |
| Online dot / hostname | reachability; host parsed from device `base_url` |
| Sparkline | `stats.token_history` |
| Tokens number | summed from live sessions |
| Card bottom bar | **CPU load %** (`loadavg[0]/cores`) — replaces design's token-cap bar |
| Device hue / icon | stable hue hashed from `id`; icon chosen from OS string |
| Aggregate strip | online x/N · Σ sessions · Σ tokens · avg load% |
| Mini-launcher | workdir + mode (+ Options: model/name/sandbox/dir-browser) → `POST /rc/start` |
| Session rows | live sessions; actions open-URL / copy-id / restart / stop / unstick / resume / preview |
| Scheduled tab | `/rc/schedules` + full CRUD |
| Logs tab | device health from `/rc/stats` + session preview |

Fields with no honest source (region, geo location, per-device token cap) are
**omitted**, not faked.

### Feature-parity map (old → V4 location)

- **Header:** machine selector (replaces device dropdown); Share/tunnel button;
  version chip with update-available action; "⋯/power" menu → Logout, Stop all,
  global refresh; link to `/legacy` (temporary).
- **Grid:** device cards + aggregate strip; "Add a device" via machine-selector item.
- **Side panel:**
  - Launcher: workdir + dir-browser + project picker + mode + model + name + sandbox.
  - Sessions tab: open URL, copy id, restart, stop, unstick, resume, preview (per-row "⋯").
  - Scheduled tab: list + create/edit modal (cron presets + dir browser) + enable/disable + fire + delete.
  - Logs tab: `/rc/stats` health + preview.
- **Auth:** login/logout remain server-rendered.

## Out of scope

- No change to tmux/session internals, the proxy, scheduler logic, or auth.
- No new device-connectivity work (Tailscale/devices.json unchanged).
- Light theme (Atelier) and the V1/V2/V3 variants are not built.

## Testing / verification

- `frontend` builds clean (`npm run build`) with no TS errors; `dist/` committed.
- Hub serves `/` (new UI) and `/legacy` (old UI); `/static/dist/assets/*` load.
- `/rc/stats` returns valid JSON on hub and home box; `/rc/overview` lists both
  devices with correct online state, sessions, tokens, load.
- End-to-end through the running hub (and `https://rc.example.com`): switch
  device, launch a session, run each session action, create/edit/fire a schedule,
  start/stop the tunnel, trigger update-check — each verified against real effects
  (mirrors the manual checks used for the multi-device deploy).
- Responsive: desktop (3-col + side panel), tablet (2-col), mobile (1-col +
  full-screen detail) all render correctly.
