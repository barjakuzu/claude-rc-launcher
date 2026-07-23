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
  sendKeys: (device: string, name: string, body: { keys?: string; special?: string[] }) =>
    req('POST', `/sessions/${encodeURIComponent(name)}/keys`, device, body),
  resize: (device: string, name: string, cols: number, rows: number) =>
    req('POST', `/sessions/${encodeURIComponent(name)}/resize`, device, { cols, rows }),
  start: (device: string, body: unknown) => req('POST', '/start', device, body),
  stop: (device: string, name: string) => req('POST', '/stop', device, { name }),
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
  update: () => req('POST', '/update'),
  // Device registry lives on the hub — never proxied, so no device arg.
  deviceRename: (id: string, name: string) => req('POST', '/devices/rename', undefined, { id, name }),
};
