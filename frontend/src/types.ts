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
