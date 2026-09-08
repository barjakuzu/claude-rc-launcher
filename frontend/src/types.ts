// Per-device live effective-token reading, derived from /api/fleet's
// per-session usage rather than the old TUI-scrape `DeviceCard.tokens`
// field (Round 2 of the limits/mobile lane: that field is null for every
// session now, so summing it across cards produced a stale number on one
// device and a lying flat 0 on every other). `effective: null` means
// "cannot vouch for this yet" and must render as a placeholder, never a
// 0, see deviceEffectiveTokens in tokens.ts. `partial` mirrors
// CostView.tsx's usagePartialFor marker (same underlying device-level
// convergence signal, reused here for the same reason).
export interface DeviceUsage {
  effective: number | null;
  partial: boolean | undefined;
}

export interface DeviceCard {
  id: string; name: string; online: boolean; hostname: string;
  sessions: number; tokens: number; loadPct: number; os: string; spark: number[];
  /** Process user on the device, e.g. "alice" or "root". May be empty if device on older code. */
  user?: string;
  /** Home directory on the device, e.g. "/home/alice" or "/root". May be empty. */
  home_dir?: string;
  /** Claude Code version running on this device, from compat.claude_version(). May be absent on an older backend or when claude isn't installed. */
  claude_version?: string;
  /** This device's own launcher version (from /rc/stats' "version" field). May be absent on an older backend. */
  version?: string;
}
export interface Session {
  name: string; mode: string; url?: string; status?: string;
  tokens?: number; workdir?: string; sessionId?: string; pct?: number;
  state?: 'starting' | 'busy' | 'idle' | 'needs_attention' | 'ended';
  kind?: 'external';
  external?: boolean;
  session_id?: string;
  pid?: number;
  waiting_for?: string | null;
  /** Working directory of an external session (sessions.py's "cwd"). */
  cwd?: string;
  /** Tmux pane this external session was adopted into, or null/absent if
   * it isn't running in a tmux pane the launcher can find (e.g.
   * Terminal.app, VS Code's integrated terminal) — no Preview/terminal
   * access is possible without this. */
  tmux?: { session_name: string; pane_id: string } | null;
  /** Remote Control URL for an adopted external session, from an OSC 8
   * hyperlink target in its pane (get_url_with_source's "osc8" source
   * only) — null until Remote Control has been enabled and shown a link. */
  rc_url?: string | null;
}
export interface ScheduleHistoryEntry {
  timestamp: string;
  status: string;
  message?: string;
  duration_minutes?: number;
}

export interface Schedule {
  id: string; name: string; cron: string | null; enabled: boolean;
  prompt?: string; instructions_file?: string; mode?: string; model?: string; workdir?: string; next_run?: string; device?: string;
  schedule_label?: string;
  concurrency?: string;
  history?: ScheduleHistoryEntry[];
}
