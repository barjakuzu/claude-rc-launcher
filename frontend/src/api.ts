export interface UpdateResult {
  ok: boolean;
  message: string;
  remote_sha?: string;
  pending_commits?: string[];
}

// Per-device config parity report (configreport.py's collect_config_report).
export interface ConfigReport {
  claude_version: string | null;
  launcher_version: string | null;
  claude_config: {
    path: string;
    head: string | null;
    short_head: string | null;
    dirty: boolean;
    dirty_files: string[];
    behind_remote: number | null;
    last_commit_date: string | null;
  };
  skills: { count: number; names: string[]; dangling: string[]; deps_missing: string[]; device_only: string[] };
  agents: string[];
  rules: { shared: string[]; local: string[] };
  plugins: { declared: string[]; installed: string[]; disabled: string[]; missing: string[]; extra: string[] };
  marketplaces: string[];
  settings: {
    hooks_present: boolean;
    remote_control_at_startup: boolean | null;
    skills_symlinked: boolean;
    sha256: string | null;
    base_sync: { kind: 'in-sync' | 'stale' | 'unknown'; missing: string[]; differing: string[] };
  };
  effective_model: string | null;
  env_model_override?: string;
  claude_local_md: boolean;
  generated_at: number;
  errors: string[];
}

// Hub fan-out across all devices, plus computed skew reasons per device id.
export interface ConfigMatrix {
  devices: Record<string, ConfigReport | { error: string }>;
  hub_head: string | null;
  skew: Record<string, string[]>;
}

// Fleet roll-up (hub-wide devices + sessions), from Task 8/9's store-backed
// endpoints. Never proxied to a device — see server.py's _should_proxy.
export interface FleetDevice {
  id: string;
  name: string;
  role: string;
  version: string | null;
  claude_version: string | null;
  last_seen: number | null;
  online: number;
  /** True when this device's usage snapshot is incomplete (Phase 3 wiring,
   * CONTRACT.md section 3): a consumer must not treat a low number here as
   * authoritative. Absent on a backend that doesn't send it yet. */
  usage_partial?: boolean;
}

// Cumulative token accounting for one session, joined from the hub's
// session_usage table (CONTRACT.md sections 1/3). `null` means no
// transcript data exists for the session (distinct from all-zero usage),
// so it must render as a dash, never as 0.
export interface SessionUsage {
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
  effective: number;
  last_ts: number;
}

export interface FleetSession {
  device_id: string;
  session_id: string;
  name: string | null;
  cwd: string | null;
  kind: string | null;
  state: string | null;
  started_at: number | null;
  ended_at: number | null;
  last_seen: number | null;
  external: number;
  needs_attention: boolean;
  // Optional — a parallel backend change carries these through the store
  // into /api/fleet, shaped exactly like the per-device GET /sessions rows
  // (sessions.py's list_rc_sessions()/SessionRow.tsx's `Session`). Absent
  // until that lands or when a row's fields genuinely don't apply; the UI
  // must degrade gracefully rather than assume presence.
  pid?: number | null;
  /** Tmux pane an external session was adopted into, or null/absent if none
   * was found — no Preview/terminal access is possible without this. */
  tmux?: { session_name: string; pane_id: string } | null;
  /** Remote Control URL, once enabled — null until then. */
  rc_url?: string | null;
  tokens?: number;
  /** Backend mode string ('sh' sessions have no transcript). */
  mode?: string;
  claude?: { pid?: number | null; state?: string | null } | Record<string, unknown>;
  /** Cumulative token usage for this session (CONTRACT.md section 3).
   * `undefined` on a backend that doesn't send it yet; `null` when the
   * backend sent it but has no transcript data for this session (the two
   * are different and must be told apart: never render `null` as 0). */
  usage?: SessionUsage | null;
  /** Seconds since `usage.last_ts`, or null. Undefined when the backend
   * doesn't send it yet. */
  usage_age_seconds?: number | null;
}

export interface FleetView {
  devices: FleetDevice[];
  sessions: FleetSession[];
}

// ─── Cost + alerts (Phase 3 wiring, CONTRACT.md sections 3/5) ────────────────
// /api/cost and /api/alerts do not exist on the backend yet (a parallel lane
// is building them), so fetchCost/fetchAlerts below throw on a 404 or any
// non-2xx response so callers can degrade honestly instead of showing a
// spinner that never resolves or a fake zero.

export interface CostDailyBucket {
  day: string;
  effective: number;
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
}

export interface CostDevice {
  device_id: string;
  name: string;
  total_effective: number;
  /** Newest day first. */
  daily: CostDailyBucket[];
}

export interface CostProject {
  device_id: string;
  project: string;
  effective: number;
}

export interface CostTotals {
  effective: number;
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
}

export interface CostReport {
  generated_at: number;
  days: number;
  devices: CostDevice[];
  /** Sorted by effective descending, capped at 50 entries. */
  projects: CostProject[];
  totals: CostTotals;
}

export interface AlertFinding {
  rule: string;
  severity: string;
  target_type: string;
  device_id: string;
  session_id: string;
  name: string;
  message: string;
  value: number;
  threshold: number;
  since: number;
  first_seen: number;
  last_seen: number;
}

export interface AlertsSummary {
  alert: number;
  warn: number;
  rules: Record<string, number>;
}

export interface AlertsReport {
  generated_at: number;
  summary: AlertsSummary;
  alerts: AlertFinding[];
  /** guard.LAST_LOAD_ERROR: a broken guard.json surfaces here instead of
   * silently falling back to defaults. Must be shown to the user, not
   * swallowed. */
  config_error: string | null;
}

// Deliberately bypasses req()'s "always call r.json()" behavior: these two
// routes don't exist yet, and a 404 that returns an HTML error page (rather
// than JSON) would otherwise surface as a confusing JSON-parse error instead
// of a clean "not available" state.
async function reqStrict<T>(path: string): Promise<T> {
  const r = await fetch('/rc' + path);
  if (r.status === 401) { window.location.href = '/login'; throw new Error('auth'); }
  if (!r.ok) throw new Error(`request failed: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function fetchCost(days = 30): Promise<CostReport> {
  return reqStrict<CostReport>(`/api/cost?days=${days}`);
}

export async function fetchAlerts(): Promise<AlertsReport> {
  return reqStrict<AlertsReport>('/api/alerts');
}

export interface SessionEvent {
  id: number;
  device_id: string;
  session_id: string;
  ts: number;
  event: string;
  extra: Record<string, unknown>;
}

export interface AuditEntry {
  id: number;
  ts: number;
  actor: string;
  action: string;
  target: string;
  device_id: string;
  detail: string;
}

async function req(method: string, path: string, device?: string, body?: unknown) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (device && device !== 'local') headers['X-RC-Device'] = device;
  const opts: RequestInit = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/rc' + path, opts);
  if (r.status === 401) { window.location.href = '/login'; throw new Error('auth'); }
  return r.json();
}

// Mirrors api.overview()'s fetch style — hub-only, never proxied to a device.
export async function fetchConfigMatrix(): Promise<ConfigMatrix> {
  return req('GET', '/api/config-matrix') as Promise<ConfigMatrix>;
}

// Mirrors fetchConfigMatrix's fetch style — hub-only, never proxied.
export async function fetchFleet(): Promise<FleetView> {
  return req('GET', '/api/fleet') as Promise<FleetView>;
}

export async function fetchSessionEvents(deviceId: string, sessionId: string, limit = 50): Promise<{ events: SessionEvent[] }> {
  return req('GET', `/api/sessions/${encodeURIComponent(deviceId)}/${encodeURIComponent(sessionId)}/events?limit=${limit}`) as Promise<{ events: SessionEvent[] }>;
}

export async function fetchAudit(limit = 50): Promise<{ audit: AuditEntry[] }> {
  return req('GET', `/api/audit?limit=${limit}`) as Promise<{ audit: AuditEntry[] }>;
}
export const api = {
  overview: () => req('GET', '/overview'),
  sessions: (device: string) => req('GET', '/sessions', device),
  schedules: (device: string) => req('GET', '/schedules', device),
  stats: (device: string) => req('GET', '/stats', device),
  projects: (device: string) => req('GET', '/projects', device),
  browse: (device: string, path: string) => req('GET', '/browse?path=' + encodeURIComponent(path), device),
  preview: (device: string, name: string, q?: { viewer: string; cols: number; rows: number; active?: boolean }) =>
    req('GET', `/sessions/${encodeURIComponent(name)}/preview${q ? `?viewer=${encodeURIComponent(q.viewer)}&cols=${q.cols}&rows=${q.rows}&active=${q.active ? 1 : 0}` : ''}`, device),
  transcript: (device: string, name: string) =>
    req('GET', `/sessions/${encodeURIComponent(name)}/transcript`, device),
  previewBye: (device: string, name: string, viewer: string) =>
    req('POST', `/sessions/${encodeURIComponent(name)}/preview-bye`, device, { viewer }),
  sendKeys: (device: string, name: string, body: { keys?: string; special?: string[] }) =>
    req('POST', `/sessions/${encodeURIComponent(name)}/keys`, device, body),
  resize: (device: string, name: string, cols: number, rows: number) =>
    req('POST', `/sessions/${encodeURIComponent(name)}/resize`, device, { cols, rows }),
  enableRc: (device: string, name: string): Promise<{ ok: boolean; url?: string; message?: string }> =>
    req('POST', `/sessions/${encodeURIComponent(name)}/enable-rc`, device),
  start: (device: string, body: unknown) => req('POST', '/start', device, body),
  stop: (device: string, name: string, opts?: { external?: boolean; pid?: number }) =>
    req('POST', '/stop', device, opts?.external ? { external: true, pid: opts.pid } : { name }),
  restart: (device: string, name: string) => req('POST', '/restart', device, { name }),
  unstick: (device: string, name: string) => req('POST', '/unstick', device, { name }),
  stopAll: (device: string) => req('POST', '/stop-all', device),
  resumeList: (device: string) => req('GET', '/resume/sessions', device),
  resumeStart: (device: string, body: unknown) => req('POST', '/resume/start', device, body),
  schedCreate: (device: string, body: unknown) => req('POST', '/schedules', device, body),
  schedUpdate: (device: string, body: unknown) => req('POST', '/schedules/update', device, body),
  schedDelete: (device: string, id: string) => req('POST', '/schedules/delete', device, { id }),
  schedFire: (device: string, id: string) => req('POST', '/schedules/fire', device, { id }),
  schedInstructions: (device: string, id: string) => req('GET', `/schedules/${encodeURIComponent(id)}/instructions`, device),
  tunnelStatus: () => req('GET', '/tunnel/status'),
  tunnelStart: () => req('POST', '/tunnel/start'),
  tunnelStop: () => req('POST', '/tunnel/stop'),
  updateCheck: () => req('GET', '/update-check'),
  // Hub's own launcher version + the claude_version its `claude` binary
  // reports (may be null/absent on an older backend or when claude isn't
  // installed) — shown next to the version badge in the header.
  version: (): Promise<{ version: string; claude_version: string | null }> => req('GET', '/version'),
  update: (body?: { confirm?: string }): Promise<UpdateResult> => req('POST', '/update', undefined, body ?? {}),
  // Device registry lives on the hub — never proxied, so no device arg.
  deviceRename: (id: string, name: string) => req('POST', '/devices/rename', undefined, { id, name }),
};
