# Mobile Bottom Nav — V5 Cross-Device Tab Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add iOS/Android-style bottom navigation to the mobile layout with four tabs — Devices, Sessions, Scheduled, Activity — each backed by cross-device aggregated data polled via Promise.allSettled.

**Architecture:** `mTab` state lives in App.tsx (persisted to localStorage), controlling which full-screen view renders in place of the existing OverviewGrid/DeviceDetail. Two new hooks (`useAllSessions`, `useAllSchedules`) poll per-device endpoints in parallel and flatten results. Activity derives from schedule history already present in the schedules payload. Desktop/tablet layout is entirely unchanged.

**Tech Stack:** React 18, TypeScript strict, inline styles matching RT design tokens (`tokens.ts`), no new npm packages, existing `api.ts` for all fetches.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `frontend/src/types.ts` | Add `history` field to `Schedule` interface |
| Create | `frontend/src/useCrossDevice.ts` | `useAllSessions` + `useAllSchedules` hooks |
| Create | `frontend/src/components/MobileNav.tsx` | Bottom navigation bar (V5MobileNav) |
| Create | `frontend/src/components/MobileHeader.tsx` | Shared cross-device page header (V5MobileHeader) |
| Create | `frontend/src/components/AllSessions.tsx` | Flattened sessions list with device chip |
| Create | `frontend/src/components/AllScheduled.tsx` | Flattened schedules list with edit/run actions |
| Create | `frontend/src/components/Activity.tsx` | Timeline from schedule history events |
| Modify | `frontend/src/App.tsx` | Wire `mTab` state, render mobile tab views, swap Footer/MobileNav |

---

### Task 1: Extend Schedule type with optional history

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add the `history` field to the `Schedule` interface**

Open `frontend/src/types.ts`. The current `Schedule` interface ends at `device?: string`. Add one optional field:

```typescript
export interface ScheduleHistoryEntry {
  timestamp: string;
  status: string;
  message?: string;
  duration_minutes?: number;
}

export interface Schedule {
  id: string; name: string; cron: string; enabled: boolean;
  prompt?: string; instructions_file?: string; mode?: string; model?: string; workdir?: string; next_run?: string; device?: string;
  schedule_label?: string;
  history?: ScheduleHistoryEntry[];
}
```

Note: also add `schedule_label?: string` which is referenced in AllScheduled subtitle rendering.

- [ ] **Step 2: Verify TypeScript accepts the change**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: no errors (or only pre-existing unrelated errors — confirm there are 0 new ones).

- [ ] **Step 3: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/types.ts
git commit -m "feat: add history and schedule_label fields to Schedule type"
```

---

### Task 2: Create useCrossDevice.ts hooks

**Files:**
- Create: `frontend/src/useCrossDevice.ts`

These hooks are called from AllSessions, AllScheduled, and Activity. They poll every 5 s only while `active` is true (passed as a boolean prop so callers can gate polling on the active tab).

- [ ] **Step 1: Create the file with both hooks**

```typescript
// useCrossDevice.ts — cross-device data aggregation hooks.
// Each hook polls every 5 s when `active === true`.
// Uses Promise.allSettled so one slow/offline device never blocks the rest.
import { useState, useEffect } from 'react';
import { api } from './api';
import type { DeviceCard, Session, Schedule } from './types';

// ─── useAllSessions ────────────────────────────────────────────────────────────

export interface DeviceSession {
  device: DeviceCard;
  session: Session;
}

export interface AllSessionsResult {
  items: DeviceSession[];
  loading: boolean;
  error: string | null;
}

export function useAllSessions(cards: DeviceCard[], active: boolean): AllSessionsResult {
  const [items, setItems] = useState<DeviceSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let isMounted = true;

    const fetch = async () => {
      const online = cards.filter((c) => c.online);
      if (online.length === 0) {
        if (isMounted) { setItems([]); setLoading(false); }
        return;
      }
      const results = await Promise.allSettled(
        online.map((d) => api.sessions(d.id).then((r) => ({ device: d, sessions: (r as Session[]) || [] })))
      );
      if (!isMounted) return;
      const flat: DeviceSession[] = [];
      let anyError = false;
      for (const r of results) {
        if (r.status === 'fulfilled') {
          for (const s of r.value.sessions) flat.push({ device: r.value.device, session: s });
        } else {
          anyError = true;
        }
      }
      setItems(flat);
      setLoading(false);
      setError(anyError ? 'Some devices unavailable' : null);
    };

    fetch();
    const id = setInterval(fetch, 5000);
    return () => { isMounted = false; clearInterval(id); };
  }, [cards, active]);

  return { items, loading, error };
}

// ─── useAllSchedules ───────────────────────────────────────────────────────────

export interface DeviceSchedule {
  device: DeviceCard;
  schedule: Schedule;
}

export interface AllSchedulesResult {
  items: DeviceSchedule[];
  loading: boolean;
  error: string | null;
}

export function useAllSchedules(cards: DeviceCard[], active: boolean): AllSchedulesResult {
  const [items, setItems] = useState<DeviceSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let isMounted = true;

    const fetch = async () => {
      const online = cards.filter((c) => c.online);
      if (online.length === 0) {
        if (isMounted) { setItems([]); setLoading(false); }
        return;
      }
      const results = await Promise.allSettled(
        online.map((d) =>
          api.schedules(d.id).then((r) => {
            // API returns { schedules: Schedule[] } or Schedule[]
            const arr: Schedule[] = Array.isArray(r) ? r : (r?.schedules ?? []);
            return { device: d, schedules: arr };
          })
        )
      );
      if (!isMounted) return;
      const flat: DeviceSchedule[] = [];
      let anyError = false;
      for (const r of results) {
        if (r.status === 'fulfilled') {
          for (const s of r.value.schedules) flat.push({ device: r.value.device, schedule: s });
        } else {
          anyError = true;
        }
      }
      setItems(flat);
      setLoading(false);
      setError(anyError ? 'Some devices unavailable' : null);
    };

    fetch();
    const id = setInterval(fetch, 5000);
    return () => { isMounted = false; clearInterval(id); };
  }, [cards, active]);

  return { items, loading, error };
}
```

- [ ] **Step 2: Verify TypeScript accepts the file**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/useCrossDevice.ts
git commit -m "feat: add useAllSessions and useAllSchedules cross-device hooks"
```

---

### Task 3: Create MobileHeader.tsx

**Files:**
- Create: `frontend/src/components/MobileHeader.tsx`

This is a simple presentational component used as the top header on all three cross-device tab views.

- [ ] **Step 1: Create the component**

```typescript
// MobileHeader.tsx — shared header for cross-device mobile views (V5MobileHeader).
import type { ReactNode } from 'react';
import { RT, FONT_MONO } from '../tokens';

export interface MobileHeaderProps {
  title: string;
  subtitle: string;
  right?: ReactNode;
}

export function MobileHeader({ title, subtitle, right }: MobileHeaderProps) {
  return (
    <div style={{
      flex: 'none',
      padding: '16px 16px 12px',
      borderBottom: `1px solid ${RT.border}`,
      background: RT.bg,
      display: 'flex',
      alignItems: 'flex-end',
      gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 9.5,
          color: RT.textLow,
          letterSpacing: '.14em',
          textTransform: 'uppercase',
          fontFamily: FONT_MONO,
          marginBottom: 4,
        }}>
          {subtitle}
        </div>
        <div style={{
          fontSize: 22,
          fontWeight: 600,
          letterSpacing: '-.02em',
        }}>
          {title}
        </div>
      </div>
      {right}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -20
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/MobileHeader.tsx
git commit -m "feat: add MobileHeader component for cross-device views"
```

---

### Task 4: Create MobileNav.tsx

**Files:**
- Create: `frontend/src/components/MobileNav.tsx`

Bottom navigation bar with 4 tabs. Badge dot on Sessions tab when count > 0.

- [ ] **Step 1: Create the component**

```typescript
// MobileNav.tsx — V5 mobile bottom navigation bar.
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';

export type MTab = 'devices' | 'sessions' | 'scheduled' | 'activity';

export interface MobileNavProps {
  active: MTab;
  onChange: (t: MTab) => void;
  counts: {
    devices: number;
    sessions: number;
    scheduled: number;
  };
}

interface NavItem {
  id: MTab;
  label: string;
  icon: (props: { size: number; stroke: string }) => JSX.Element;
  count?: number;
  dot?: boolean;
}

export function MobileNav({ active, onChange, counts }: MobileNavProps) {
  const items: NavItem[] = [
    { id: 'devices',   label: 'Devices',   icon: Icons.server,   count: counts.devices },
    { id: 'sessions',  label: 'Sessions',  icon: Icons.terminal, count: counts.sessions, dot: true },
    { id: 'scheduled', label: 'Scheduled', icon: Icons.clock,    count: counts.scheduled },
    { id: 'activity',  label: 'Activity',  icon: Icons.share },
  ];

  return (
    <div style={{
      flex: 'none',
      borderTop: `1px solid ${RT.border}`,
      background: RT.bgRaised,
      paddingBottom: 'max(8px, env(safe-area-inset-bottom))',
      paddingTop: 6,
      display: 'grid',
      gridTemplateColumns: `repeat(${items.length}, 1fr)`,
    }}>
      {items.map((it) => {
        const isActive = active === it.id;
        const color = isActive ? RT.text : RT.textLow;
        const Icon = it.icon;
        return (
          <button
            key={it.id}
            onClick={() => onChange(it.id)}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              padding: '6px 4px 4px',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 3,
              color,
              fontFamily: 'inherit',
              minHeight: 52,
              position: 'relative',
            }}
          >
            {/* Active indicator bar at top */}
            {isActive && (
              <div style={{
                position: 'absolute',
                top: 0,
                left: '50%',
                transform: 'translateX(-50%)',
                width: 24,
                height: 2,
                background: RT.text,
                borderRadius: 2,
              }} />
            )}

            {/* Icon + badge */}
            <div style={{ position: 'relative', display: 'flex' }}>
              <Icon size={20} stroke={color} />
              {it.dot && it.count !== undefined && it.count > 0 && (
                <span style={{
                  position: 'absolute',
                  top: -3,
                  right: -6,
                  minWidth: 14,
                  height: 14,
                  padding: '0 4px',
                  borderRadius: 7,
                  background: RT.green,
                  color: RT.bg,
                  fontFamily: FONT_MONO,
                  fontSize: 9,
                  fontWeight: 700,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: `1.5px solid ${RT.bgRaised}`,
                }}>
                  {it.count}
                </span>
              )}
            </div>

            {/* Label */}
            <div style={{
              fontSize: 10,
              fontWeight: isActive ? 600 : 500,
              letterSpacing: '.01em',
            }}>
              {it.label}
            </div>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Check Icons exports to confirm the icon names used exist**

```bash
grep -n 'server\|terminal\|clock\|share' /var/www/rc-launcher/frontend/src/components/primitives.tsx | head -20
```

Expected: lines for `server`, `terminal`, `clock`, `share` in the Icons object. If any are missing, use an existing icon with a similar purpose (e.g., `Icons.refresh` for activity). Adjust the import accordingly.

- [ ] **Step 3: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/MobileNav.tsx
git commit -m "feat: add MobileNav bottom navigation component"
```

---

### Task 5: Create AllSessions.tsx

**Files:**
- Create: `frontend/src/components/AllSessions.tsx`

Flattened sessions across all online devices. Each row shows: session name + status pill, device chip button (taps to open device detail), dir + token count, three action buttons (Preview / Resume / Stop).

- [ ] **Step 1: Check that V5StatusPill is exported from SessionRow.tsx**

```bash
grep 'export.*V5StatusPill' /var/www/rc-launcher/frontend/src/components/SessionRow.tsx
```

If not exported, we need to export it in SessionRow.tsx. If it's not exported, add `export` before the function declaration. (It's currently an internal function.)

- [ ] **Step 2: Export V5StatusPill from SessionRow.tsx**

In `frontend/src/components/SessionRow.tsx`, find line:
```
function V5StatusPill({ status }: { status: string }) {
```
Change it to:
```
export function V5StatusPill({ status }: { status: string }) {
```

- [ ] **Step 3: Create AllSessions.tsx**

```typescript
// AllSessions.tsx — flattened sessions across all online devices (V5AllSessions).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot } from './primitives';
import { MobileHeader } from './MobileHeader';
import { V5StatusPill } from './SessionRow';
import { useAllSessions } from '../useCrossDevice';
import { api } from '../api';
import type { DeviceCard } from '../types';
import { PreviewModal } from './PreviewModal';

export interface AllSessionsProps {
  cards: DeviceCard[];
  onOpenDevice: (id: string) => void;
}

function mobileActionBtn(accent?: string): React.CSSProperties {
  return {
    background: RT.panel,
    color: accent ?? RT.text,
    border: `1px solid ${RT.border}`,
    borderRadius: 7,
    padding: '8px 12px',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11.5,
    fontWeight: 500,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    minHeight: 36,
  };
}

export function AllSessions({ cards, onOpenDevice }: AllSessionsProps) {
  const { items, loading } = useAllSessions(cards, true);
  const [pending, setPending] = useState<string | null>(null);
  const [previewDevice, setPreviewDevice] = useState<string | null>(null);
  const [previewSession, setPreviewSession] = useState<string | null>(null);

  const handlePreview = (deviceId: string, sessionName: string) => {
    setPreviewDevice(deviceId);
    setPreviewSession(sessionName);
  };

  const handleResume = async (deviceId: string, name: string) => {
    const key = deviceId + ':' + name;
    if (pending === key) return;
    setPending(key);
    try { await api.restart(deviceId, name); } catch {/* ignore */}
    finally { setPending(null); }
  };

  const handleStop = async (deviceId: string, name: string) => {
    const key = deviceId + ':' + name;
    if (pending === key) return;
    setPending(key);
    try { await api.stop(deviceId, name); } catch {/* ignore */}
    finally { setPending(null); }
  };

  const subtitle = loading
    ? 'loading…'
    : `${items.length} active · across ${cards.filter((c) => c.online).length} devices`;

  return (
    <>
      <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
        <MobileHeader
          subtitle={subtitle}
          title="Sessions"
        />
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {!loading && items.length === 0 && (
            <div style={{
              background: RT.card, border: `1px solid ${RT.border}`,
              borderRadius: 10, padding: 24, textAlign: 'center',
              color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12,
            }}>
              No active sessions.
            </div>
          )}
          {items.map(({ device: d, session: s }) => {
            const hue = hueForId(d.id);
            const hueColor = tintFor(hue, 0.70, 0.10);
            const key = d.id + ':' + (s.name ?? s.sessionId ?? '');
            const isP = pending === key;
            const dir = s.workdir
              ? (s.workdir.replace(/\/$/, '').split('/').pop() || s.workdir)
              : '—';
            const tokensK = `${Math.round((s.tokens ?? 0) / 1000)}K`;

            return (
              <div key={key} style={{
                background: RT.card, border: `1px solid ${RT.border}`,
                borderRadius: 10, padding: 12,
                display: 'flex', flexDirection: 'column', gap: 8,
              }}>
                {/* Row 1: name + status pill */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{
                    flex: 1, fontSize: 14, fontWeight: 600,
                    letterSpacing: '-.005em',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {s.name}
                  </div>
                  <V5StatusPill status={s.status ?? 'idle'} />
                </div>

                {/* Row 2: device chip */}
                <button
                  onClick={() => onOpenDevice(d.id)}
                  style={{
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    color: hueColor, fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 500,
                    letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start',
                  }}
                >
                  <Dot color={hueColor} size={6} pulse={d.online} />
                  {d.name}
                  <Icons.chevRight size={10} stroke={hueColor} />
                </button>

                {/* Row 3: dir + tokens */}
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow,
                }}>
                  <Icons.folder size={10} stroke={RT.textLow} />
                  <span>{dir}</span>
                  <span style={{ color: RT.borderHi }}>·</span>
                  <span>{tokensK}</span>
                </div>

                {/* Row 4: actions */}
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    style={mobileActionBtn()}
                    disabled={isP}
                    onClick={() => handlePreview(d.id, s.name)}
                  >
                    <Icons.link size={13} stroke={RT.textDim} /> Preview
                  </button>
                  <button
                    style={mobileActionBtn(RT.green)}
                    disabled={isP}
                    onClick={() => handleResume(d.id, s.name)}
                  >
                    <Icons.refresh size={13} stroke={RT.green} /> Resume
                  </button>
                  <button
                    style={{ ...mobileActionBtn(), marginLeft: 'auto', minWidth: 36, padding: '8px 10px' }}
                    disabled={isP}
                    onClick={() => handleStop(d.id, s.name)}
                  >
                    <Icons.stop size={12} stroke={RT.red} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {previewDevice && previewSession && (
        <PreviewModal
          deviceId={previewDevice}
          sessionName={previewSession}
          onClose={() => { setPreviewDevice(null); setPreviewSession(null); }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 4: Check Icons.chevRight exists in primitives**

```bash
grep 'chevRight\|chevron' /var/www/rc-launcher/frontend/src/components/primitives.tsx | head -10
```

If `chevRight` doesn't exist, look for the actual arrow icon name and replace in the code above.

- [ ] **Step 5: Check PreviewModal props interface**

```bash
grep -n 'export.*PreviewModal\|PreviewModalProps\|interface.*Props' /var/www/rc-launcher/frontend/src/components/PreviewModal.tsx | head -10
```

Verify `deviceId` and `sessionName` are the correct prop names.

- [ ] **Step 6: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/AllSessions.tsx frontend/src/components/SessionRow.tsx
git commit -m "feat: add AllSessions cross-device flattened sessions view"
```

---

### Task 6: Create AllScheduled.tsx

**Files:**
- Create: `frontend/src/components/AllScheduled.tsx`

Flattened schedules across all online devices. Each row: name + enabled/paused badge, device chip, cron + schedule_label, Run now / Edit actions. Edit opens ScheduleModal prefilled.

- [ ] **Step 1: Check ScheduleModal props signature**

```bash
grep -n 'ScheduleModalProps\|export.*ScheduleModal\|deviceId\|initial\|onClose\|onSaved' /var/www/rc-launcher/frontend/src/components/ScheduleModal.tsx | head -20
```

Confirm: `ScheduleModal` takes `{ deviceId: string; initial?: Schedule | null; onClose: () => void; onSaved: () => void }`.

- [ ] **Step 2: Create AllScheduled.tsx**

```typescript
// AllScheduled.tsx — flattened schedules across all online devices (V5AllScheduled).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot } from './primitives';
import { MobileHeader } from './MobileHeader';
import { useAllSchedules } from '../useCrossDevice';
import { ScheduleModal } from './ScheduleModal';
import { api } from '../api';
import type { DeviceCard, Schedule } from '../types';

export interface AllScheduledProps {
  cards: DeviceCard[];
}

function mobileActionBtn(): React.CSSProperties {
  return {
    background: RT.panel,
    color: RT.text,
    border: `1px solid ${RT.border}`,
    borderRadius: 7,
    padding: '8px 12px',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11.5,
    fontWeight: 500,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    minHeight: 36,
  };
}

export function AllScheduled({ cards }: AllScheduledProps) {
  const { items, loading, refetch } = useAllSchedules(cards, true);
  const [pending, setPending] = useState<string | null>(null);
  const [editSched, setEditSched] = useState<{ deviceId: string; schedule: Schedule } | null>(null);

  const handleRunNow = async (deviceId: string, scheduleId: string) => {
    if (pending === scheduleId) return;
    setPending(scheduleId);
    try { await api.schedFire(deviceId, scheduleId); } catch {/* ignore */}
    finally { setPending(null); }
  };

  const subtitle = loading
    ? 'loading…'
    : `${items.length} tasks across ${cards.filter((c) => c.online).length} devices`;

  return (
    <>
      <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
        <MobileHeader subtitle={subtitle} title="Scheduled" />
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {!loading && items.length === 0 && (
            <div style={{
              background: RT.card, border: `1px solid ${RT.border}`,
              borderRadius: 10, padding: 24, textAlign: 'center',
              color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12,
            }}>
              No scheduled tasks.
            </div>
          )}
          {items.map(({ device: d, schedule: s }) => {
            const hue = hueForId(d.id);
            const hueColor = tintFor(hue, 0.70, 0.10);
            const isP = pending === s.id;

            return (
              <div key={d.id + ':' + s.id} style={{
                background: RT.card, border: `1px solid ${RT.border}`,
                borderRadius: 10, padding: 12,
                display: 'flex', flexDirection: 'column', gap: 7,
              }}>
                {/* Row 1: name + enabled badge */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{
                    flex: 1, fontSize: 14, fontWeight: 600,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {s.name}
                  </div>
                  <span style={{
                    fontSize: 9, fontFamily: FONT_MONO,
                    letterSpacing: '.06em', textTransform: 'uppercase',
                    color: s.enabled ? RT.green : RT.textLow,
                    padding: '2px 6px', borderRadius: 4,
                    background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
                  }}>
                    {s.enabled ? 'enabled' : 'paused'}
                  </span>
                </div>

                {/* Row 2: device chip */}
                <div style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  color: hueColor, fontFamily: FONT_MONO,
                  fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase',
                  alignSelf: 'flex-start',
                }}>
                  <Dot color={hueColor} size={5} pulse={d.online} />
                  {d.name}
                </div>

                {/* Row 3: cron + schedule_label */}
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow,
                }}>
                  <Icons.clock size={10} stroke={RT.textLow} />
                  <span>{s.cron}</span>
                  {s.schedule_label && (
                    <span style={{ color: RT.borderHi }}>({s.schedule_label})</span>
                  )}
                </div>

                {/* Row 4: actions */}
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    style={mobileActionBtn()}
                    disabled={isP}
                    onClick={() => handleRunNow(d.id, s.id)}
                  >
                    <Icons.play size={12} stroke={RT.green} /> Run now
                  </button>
                  <button
                    style={mobileActionBtn()}
                    onClick={() => setEditSched({ deviceId: d.id, schedule: s })}
                  >
                    <Icons.terminal size={13} stroke={RT.textDim} /> Edit
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {editSched && (
        <ScheduleModal
          deviceId={editSched.deviceId}
          initial={editSched.schedule}
          onClose={() => setEditSched(null)}
          onSaved={() => setEditSched(null)}
        />
      )}
    </>
  );
}
```

Note: `refetch` doesn't exist yet on `useAllSchedules` — remove it from the destructure (the hook auto-polls every 5s anyway).

- [ ] **Step 3: Fix: remove `refetch` from destructure in AllScheduled.tsx**

The hook returns `{ items, loading, error }` — not `refetch`. The `const { items, loading, refetch }` line must be `const { items, loading }` instead.

- [ ] **Step 4: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: 0 errors.

- [ ] **Step 5: Check Icons.play and Icons.clock exist**

```bash
grep 'play\|clock' /var/www/rc-launcher/frontend/src/components/primitives.tsx | head -10
```

If any are missing, substitute with existing icons.

- [ ] **Step 6: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/AllScheduled.tsx
git commit -m "feat: add AllScheduled cross-device flattened schedules view"
```

---

### Task 7: Create Activity.tsx

**Files:**
- Create: `frontend/src/components/Activity.tsx`

Timeline derived from `schedule.history[]` across all devices. Sort by timestamp DESC, take latest 30. Color-code by status: success/completed → green, error/failed → red, else → amber/textDim.

- [ ] **Step 1: Create Activity.tsx**

```typescript
// Activity.tsx — timeline of schedule history events across all devices (V5Activity).
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { MobileHeader } from './MobileHeader';
import { useAllSchedules } from '../useCrossDevice';
import type { DeviceCard } from '../types';

export interface ActivityProps {
  cards: DeviceCard[];
}

interface ActivityEvent {
  timestamp: string;
  deviceId: string;
  deviceName: string;
  deviceHue: number;
  deviceOnline: boolean;
  scheduleName: string;
  status: string;
  message?: string;
}

function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'success' || s === 'completed') return RT.green;
  if (s === 'error' || s === 'failed') return RT.red;
  return RT.amber;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function Activity({ cards }: ActivityProps) {
  const { items: schedItems, loading } = useAllSchedules(cards, true);

  // Flatten history entries from all schedules
  const events: ActivityEvent[] = [];
  for (const { device: d, schedule: s } of schedItems) {
    if (!s.history) continue;
    for (const h of s.history) {
      events.push({
        timestamp: h.timestamp,
        deviceId: d.id,
        deviceName: d.name,
        deviceHue: hueForId(d.id),
        deviceOnline: d.online,
        scheduleName: s.name,
        status: h.status,
        message: h.message,
      });
    }
  }
  events.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  const shown = events.slice(0, 30);

  const subtitle = loading
    ? 'loading…'
    : shown.length > 0
      ? `last ${shown.length} events across devices`
      : 'last 24 hours';

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader subtitle={subtitle} title="Activity" />
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 0 }}>
        {!loading && shown.length === 0 && (
          <div style={{
            background: RT.card, border: `1px solid ${RT.border}`,
            borderRadius: 10, padding: 24, textAlign: 'center',
            color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12,
          }}>
            No recent activity.
          </div>
        )}
        {shown.map((e, i) => {
          const hueColor = tintFor(e.deviceHue, 0.70, 0.10);
          const kindColor = statusColor(e.status);
          const isLast = i === shown.length - 1;
          return (
            <div key={e.timestamp + e.deviceId + e.scheduleName + i} style={{
              display: 'flex', gap: 12, position: 'relative',
              paddingBottom: isLast ? 0 : 14,
            }}>
              {/* Timeline spine */}
              <div style={{
                flex: 'none', width: 14,
                display: 'flex', flexDirection: 'column', alignItems: 'center',
              }}>
                <span style={{
                  width: 8, height: 8, borderRadius: 4,
                  background: hueColor, marginTop: 5,
                  border: `2px solid ${RT.bg}`,
                  boxShadow: `0 0 0 1.5px ${hueColor}`,
                  flex: 'none',
                }} />
                {!isLast && (
                  <span style={{
                    flex: 1, width: 1, background: RT.border, marginTop: 4,
                  }} />
                )}
              </div>

              {/* Event content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: RT.text, lineHeight: 1.4 }}>
                  {e.scheduleName}
                  {e.message && (
                    <span style={{ color: RT.textDim }}> — {e.message}</span>
                  )}
                </div>
                <div style={{
                  marginTop: 4, fontSize: 10.5, fontFamily: FONT_MONO,
                  color: RT.textLow, display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span style={{ color: hueColor }}>{e.deviceName}</span>
                  <span style={{ color: RT.borderHi }}>·</span>
                  <span>{relativeTime(e.timestamp)}</span>
                  <span style={{ color: RT.borderHi }}>·</span>
                  <span style={{
                    color: kindColor, letterSpacing: '.06em', textTransform: 'uppercase',
                  }}>
                    {e.status}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/Activity.tsx
git commit -m "feat: add Activity timeline component from schedule history"
```

---

### Task 8: Wire mTab state and mobile views in App.tsx

**Files:**
- Modify: `frontend/src/App.tsx`

This is the final integration step. Add `mTab` state, `pickMTab`, and the conditional rendering logic.

- [ ] **Step 1: Add imports at the top of App.tsx**

After the existing imports (after `import type { PanelTab }...`), add:

```typescript
import { MobileNav } from './components/MobileNav';
import type { MTab } from './components/MobileNav';
import { AllSessions } from './components/AllSessions';
import { AllScheduled } from './components/AllScheduled';
import { Activity } from './components/Activity';
```

- [ ] **Step 2: Add mTab state and pickMTab inside the App function**

After the existing `const [tab, setTab] = useState<PanelTab>('running');` line, add:

```typescript
const [mTab, setMTab] = useState<MTab>(() => {
  const saved = localStorage.getItem('rc_mtab');
  return (saved as MTab | null) ?? 'devices';
});

const pickMTab = (t: MTab) => {
  setMTab(t);
  if (t !== 'devices') setOpenId(null);
  localStorage.setItem('rc_mtab', t);
};
```

- [ ] **Step 3: Compute session count for badge**

After the `const openCard` line (around line 47), add:

```typescript
const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);
```

- [ ] **Step 4: Replace the body/footer rendering in App.tsx**

The current return JSX has this structure at the bottom:

```jsx
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {/* Left rail: only when a device is open and not mobile */}
        {openCard && !layout.mobile && (
          <DeviceRail cards={cards} openId={openId} setOpenId={handleOpen} />
        )}

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {openCard ? (
            // Device detail — full main area
            <DeviceDetail
              device={openCard}
              tab={tab}
              setTab={setTab}
              onClose={() => handleOpen(null)}
              layout={layout}
            />
          ) : (
            // Overview grid — big cards
            <OverviewGrid
              cards={cards}
              layout={layout}
              onOpen={handleOpen}
            />
          )}
        </div>
      </div>
    </div>
```

Replace with:

```jsx
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {/* Left rail: only when a device is open and not mobile */}
        {openCard && !layout.mobile && (
          <DeviceRail cards={cards} openId={openId} setOpenId={handleOpen} />
        )}

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {layout.mobile ? (
            // ── Mobile tab content ────────────────────────────────────────────
            mTab === 'devices' ? (
              openCard ? (
                <DeviceDetail
                  device={openCard}
                  tab={tab}
                  setTab={setTab}
                  onClose={() => handleOpen(null)}
                  layout={layout}
                />
              ) : (
                <OverviewGrid
                  cards={cards}
                  layout={layout}
                  onOpen={handleOpen}
                />
              )
            ) : mTab === 'sessions' ? (
              <AllSessions
                cards={cards}
                onOpenDevice={(id) => { handleOpen(id); pickMTab('devices'); }}
              />
            ) : mTab === 'scheduled' ? (
              <AllScheduled cards={cards} />
            ) : (
              <Activity cards={cards} />
            )
          ) : (
            // ── Desktop / tablet ───────────────────────────────────────────────
            openCard ? (
              <DeviceDetail
                device={openCard}
                tab={tab}
                setTab={setTab}
                onClose={() => handleOpen(null)}
                layout={layout}
              />
            ) : (
              <OverviewGrid
                cards={cards}
                layout={layout}
                onOpen={handleOpen}
              />
            )
          )}
        </div>
      </div>

      {/* Footer: mobile bottom nav OR desktop footer (Strip already above on desktop) */}
      {layout.mobile ? (
        <MobileNav
          active={mTab}
          onChange={pickMTab}
          counts={{ devices: cards.length, sessions: totalSessions, scheduled: 0 }}
        />
      ) : null}
    </div>
```

Note: `scheduled: 0` because we don't want to force-poll schedules on all tabs. Badge on Scheduled is intentionally absent in this iteration.

- [ ] **Step 5: Verify TypeScript**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -40
```

Expected: 0 errors.

- [ ] **Step 6: Build**

```bash
cd /var/www/rc-launcher/frontend && npm run build 2>&1 | tail -20
```

Expected: `✓ built in X.XXs` with no errors.

- [ ] **Step 7: Check that strip is still rendered on desktop (not mobile)**

In App.tsx confirm line `{!layout.mobile && <Strip cards={cards} />}` is still present and unchanged. The Strip component is only shown on desktop/tablet — this was already the case in the original code.

- [ ] **Step 8: Commit all App.tsx changes**

```bash
cd /var/www/rc-launcher && git add frontend/src/App.tsx
git commit -m "feat: wire mTab state and mobile tab views in App.tsx"
```

---

### Task 9: Check Icon names and fix any mismatches

**Files:**
- Modify: (any component files that use Icons)

Icons from `primitives.tsx` may have different property names than what the design reference used.

- [ ] **Step 1: List all exported icon names**

```bash
grep -oP '(?<=Icons\.)[\w]+' /var/www/rc-launcher/frontend/src/components/primitives.tsx | sort -u
```

Also check:
```bash
grep -n 'export.*Icons\|Icons = {' /var/www/rc-launcher/frontend/src/components/primitives.tsx | head -5
```

- [ ] **Step 2: Cross-check against names used in new components**

Icons used in new files:
- `Icons.server` → MobileNav
- `Icons.terminal` → MobileNav, AllScheduled
- `Icons.clock` → MobileNav, AllScheduled
- `Icons.share` → MobileNav
- `Icons.chevRight` → AllSessions
- `Icons.folder` → AllSessions
- `Icons.link` → AllSessions
- `Icons.refresh` → AllSessions
- `Icons.stop` → AllSessions
- `Icons.play` → AllScheduled

For any name that doesn't exist, substitute with the closest match found in Step 1. Common aliases:
- `chevRight` might be `arrowRight` or `chevron`
- `share` might be `activity` or `clock`
- `play` might be `run` or `arrow`
- `server` might be `device` or `cpu`

- [ ] **Step 3: Update files with corrected icon names**

Edit each affected component file to use the correct icon name. Example if `chevRight` is actually `chevron`:
In `AllSessions.tsx`, replace `Icons.chevRight` with `Icons.chevron`.

- [ ] **Step 4: Final TypeScript and build check**

```bash
cd /var/www/rc-launcher/frontend && npx tsc -b --noEmit 2>&1 | head -30
cd /var/www/rc-launcher/frontend && npm run build 2>&1 | tail -20
```

Expected: 0 errors, green build.

- [ ] **Step 5: Commit icon fixes**

```bash
cd /var/www/rc-launcher && git add frontend/src/components/
git commit -m "fix: correct icon names in mobile nav components"
```

---

### Task 10: Final integration check and dist build

**Files:**
- Modify: `static/dist/` (output from build)

- [ ] **Step 1: Run full build and copy to static/dist**

```bash
cd /var/www/rc-launcher/frontend && npm run build 2>&1
```

Expected: clean build with no TypeScript errors.

- [ ] **Step 2: Verify dist files were updated**

```bash
ls -la /var/www/rc-launcher/static/dist/ | head -10
```

Confirm the `index.html` and JS/CSS bundle timestamps updated.

- [ ] **Step 3: Verify desktop layout is unchanged by reading App.tsx**

Confirm the desktop branch still has: `DeviceRail` (on non-mobile with openCard), `DeviceDetail` (when openCard), `OverviewGrid` (otherwise), `Strip` (non-mobile above body), no MobileNav.

- [ ] **Step 4: Final commit with built assets**

```bash
cd /var/www/rc-launcher && git add frontend/src static/dist
git commit -m "feat: V5 mobile bottom nav with Devices/Sessions/Scheduled/Activity cross-device views"
```

---

## Summary

This plan adds ~5 new files and modifies 3, totaling ~550 lines of new code. No new npm dependencies. The hooks poll only when the respective tab is active, avoiding unnecessary network traffic. The desktop layout is entirely unchanged — only the mobile branch receives new routing logic.
