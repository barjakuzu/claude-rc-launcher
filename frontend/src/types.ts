export interface DeviceCard {
  id: string; name: string; online: boolean; hostname: string;
  sessions: number; tokens: number; loadPct: number; os: string; spark: number[];
  /** Process user on the device, e.g. "alice" or "root". May be empty if device on older code. */
  user?: string;
  /** Home directory on the device, e.g. "/home/alice" or "/root". May be empty. */
  home_dir?: string;
  /** Claude Code version running on this device, from compat.claude_version(). May be absent on an older backend or when claude isn't installed. */
  claude_version?: string;
}
export interface Session {
  name: string; mode: string; url?: string; status?: string;
  tokens?: number; workdir?: string; sessionId?: string; pct?: number;
  state?: 'starting' | 'busy' | 'idle' | 'needs_attention' | 'ended';
  kind?: 'external';
  external?: boolean;
  session_id?: string;
  waiting_for?: string | null;
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
