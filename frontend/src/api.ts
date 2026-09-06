export interface UpdateResult {
  ok: boolean;
  message: string;
  remote_sha?: string;
  pending_commits?: string[];
}

// Per-device config parity report (configreport.py's collect_config_report).
export interface ConfigReport {
  claude_version: string | null;
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
    settings_symlinked: boolean;
    skills_symlinked: boolean;
    sha256: string | null;
  };
  effective_model: string | null;
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
