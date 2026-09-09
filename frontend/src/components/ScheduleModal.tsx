// ScheduleModal.tsx — create / edit a schedule.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO, Z } from '../tokens';
import { btn } from './btn';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';
import type { Schedule } from '../types';

// ── Cron presets ──────────────────────────────────────────────────────────────
const MANUAL_PRESET = '__manual__';
// task-l3: fires once when a Claude usage window resets, instead of on a
// cron schedule. Slotted into the same preset dropdown as Manual, since
// it is a third mutually-exclusive way to decide "when does this run"
// (matching the existing modal idiom rather than adding a parallel control).
const LIMIT_RESET_PRESET = '__limit_reset__';

// A schedule must send a 5-field cron or an explicit Manual (cron: null) —
// an empty/blank cron string 400s server-side. Cheap client-side check
// (field count only; the server still validates each field's contents).
export function isValidCronString(value: string): boolean {
  return value.trim().split(/\s+/).filter(Boolean).length === 5;
}

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Choose a preset…', value: '' },
  { label: 'Manual (run on demand)', value: MANUAL_PRESET },
  { label: 'When my limit resets', value: LIMIT_RESET_PRESET },
  { label: 'Every hour',          value: '0 * * * *' },
  { label: 'Every 2 hours',       value: '0 */2 * * *' },
  { label: 'Every 6 hours',       value: '0 */6 * * *' },
  { label: 'Daily at 9 AM',       value: '0 9 * * *' },
  { label: 'Daily at noon',       value: '0 12 * * *' },
  { label: 'Daily at midnight',   value: '0 0 * * *' },
  { label: 'Weekdays at 9 AM',    value: '0 9 * * 1-5' },
  { label: 'Weekly on Monday',    value: '0 9 * * 1' },
  { label: 'Monthly on the 1st',  value: '0 0 1 * *' },
];

// ── Mode / model maps ─────────────────────────────────────────────────────────
type ModeKey = 'STANDARD' | 'TEAMMATE' | 'SAFE';
type ModelKey = 'DEFAULT' | 'SONNET' | 'HAIKU' | 'FABLE';

const MODE_TO_API: Record<ModeKey, string>  = { STANDARD: 'c', TEAMMATE: 'ci', SAFE: 'safe' };
const API_TO_MODE: Record<string, ModeKey>  = { c: 'STANDARD', ci: 'TEAMMATE', safe: 'SAFE' };
const MODEL_TO_API: Record<ModelKey, string> = { DEFAULT: '', SONNET: '2', HAIKU: '3', FABLE: '4' };
const API_TO_MODEL: Record<string, ModelKey> = { '': 'DEFAULT', '2': 'SONNET', '3': 'HAIKU', '4': 'FABLE' };

// ── Shared field style ────────────────────────────────────────────────────────
const fieldStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  background: RT.panel,
  border: `1px solid ${RT.border}`,
  borderRadius: 6,
  padding: '7px 10px',
  color: RT.text,
  fontFamily: FONT_MONO,
  fontSize: 13,
  outline: 'none',
};

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: RT.textDim,
  marginBottom: 4,
  display: 'block',
};

// ── limit_reset trigger (task-l3) ───────────────────────────────────────────
// `trigger` isn't on the shared Schedule type (types.ts is owned by a
// parallel lane for this task), so it is declared locally and intersected
// in, same pattern as any other optional field a single component needs.
interface LimitResetTrigger {
  kind: 'limit_reset';
  window: 'five_hour' | 'seven_day';
  delay_minutes?: number;
  catch_up?: 'latest' | 'none';
}
type ScheduleWithTrigger = Schedule & { trigger?: LimitResetTrigger | null };

const RESET_WINDOW_LABEL: Record<'five_hour' | 'seven_day', string> = {
  five_hour: '5-hour window',
  seven_day: 'Weekly window',
};

// ── Component ─────────────────────────────────────────────────────────────────
export interface ScheduleModalProps {
  deviceId: string;
  initial?: ScheduleWithTrigger | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ScheduleModal({ deviceId, initial, onClose, onSaved }: ScheduleModalProps) {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  // Form state — prefilled from `initial` when editing
  const [name,             setName]             = useState(initial?.name             ?? '');
  const [cron,             setCron]             = useState(initial?.cron             ?? '');
  const [prompt,           setPrompt]           = useState(initial?.prompt           ?? '');
  const [instructionsFile, setInstructionsFile] = useState(initial?.instructions_file ?? '');
  const [workdir,          setWorkdir]          = useState(initial?.workdir          ?? '');
  const [mode,    setMode]    = useState<ModeKey>(
    API_TO_MODE[initial?.mode ?? ''] ?? 'STANDARD',
  );
  const [model,   setModel]   = useState<ModelKey>(
    API_TO_MODEL[initial?.model ?? ''] ?? 'DEFAULT',
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [concurrency, setConcurrency] = useState<'skip' | 'kill'>(
    initial?.concurrency === 'kill' ? 'kill' : 'skip',
  );

  const [preset,       setPreset]       = useState(() => {
    if (initial?.trigger?.kind === 'limit_reset') return LIMIT_RESET_PRESET;
    if (initial && !initial.cron) return MANUAL_PRESET;
    return '';
  });
  const [resetWindow,  setResetWindow]  = useState<'five_hour' | 'seven_day'>(
    initial?.trigger?.window === 'seven_day' ? 'seven_day' : 'five_hour',
  );
  const [delayMinutes, setDelayMinutes] = useState<number>(
    typeof initial?.trigger?.delay_minutes === 'number' ? initial.trigger.delay_minutes : 0,
  );
  const [pending,      setPending]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [showBrowser,  setShowBrowser]  = useState(false);

  // Manual and "when my limit resets" both always send cron: null (valid).
  // Otherwise a preset or a typed cron must resolve to a real 5-field
  // expression before Save is allowed.
  const cronOk =
    preset === MANUAL_PRESET || preset === LIMIT_RESET_PRESET || isValidCronString(cron);

  // Preset -> fill cron input. Switching to Manual or "when my limit
  // resets" deliberately does NOT blank `cron` here: only one trigger can
  // ever drive a task, so a cron typed in stays out of the save payload
  // either way (handleSave always sends cron: null for both), but the
  // text itself is kept around rather than silently discarded - it
  // reappears if the user switches back to a cron preset, and the note
  // below makes the exclusivity visible instead of leaving it implicit.
  function handlePreset(value: string) {
    setPreset(value);
    if (value && value !== MANUAL_PRESET && value !== LIMIT_RESET_PRESET) {
      setCron(value);
    }
  }

  // Launch a live Claude session on this device using the schedule's
  // workdir / mode / model — useful for finalizing/testing the prompt.
  const [launching, setLaunching] = useState(false);
  async function handleOpenSession() {
    if (launching) return;
    setError(null);
    setLaunching(true);
    try {
      const body: Record<string, unknown> = {
        mode: MODE_TO_API[mode],
        workdir: workdir || undefined,
      };
      const modelApi = MODEL_TO_API[model];
      if (modelApi) body.model = modelApi;
      // Give the launched session a recognizable name tied to the schedule.
      if (name) body.name = `finalize-${name}`.slice(0, 60).replace(/[^A-Za-z0-9_-]/g, '-');
      const res = await api.start(deviceId, body);
      if (res && res.ok === false) throw new Error(res.message ?? 'launch failed');
      if (mounted.current) {
        onClose();
      }
    } catch (e) {
      if (mounted.current) setError(`Failed to open session: ${(e as Error).message}`);
    } finally {
      if (mounted.current) setLaunching(false);
    }
  }

  async function handleSave() {
    if (pending || !cronOk) return;
    setError(null);
    setPending(true);
    try {
      const isLimitReset = preset === LIMIT_RESET_PRESET;
      const body = {
        name,
        cron: (preset === MANUAL_PRESET || isLimitReset) ? null : cron,
        prompt,
        instructions_file: instructionsFile || undefined,
        workdir,
        mode:    MODE_TO_API[mode],
        model:   MODEL_TO_API[model],
        concurrency,
        enabled,
        trigger: isLimitReset
          ? { kind: 'limit_reset', window: resetWindow, delay_minutes: delayMinutes }
          : null,
      };

      let result: { ok?: boolean; message?: string };
      if (initial) {
        result = await api.schedUpdate(deviceId, { id: initial.id, ...body }) as typeof result;
      } else {
        result = await api.schedCreate(deviceId, body) as typeof result;
      }

      if (!mounted.current) return;

      // Server may return { ok: false, message } on validation error
      if (result && result.ok === false) {
        setError(result.message ?? 'Unknown error');
        return;
      }

      onSaved();
    } catch (err: unknown) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      if (mounted.current) setPending(false);
    }
  }

  return (
    /* Backdrop */
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: Z.modal,
        background: 'rgba(0,0,0,.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px 16px',
      }}
    >
      {/* Card */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxWidth: 440,
          // Round 4: 100vh can exceed the pinned document's real visible
          // height (index.html pins body to the viewport and #root to
          // 100dvh), which would size this modal taller than the screen
          // and leave the bottom of it unreachable, since the page itself
          // no longer scrolls to reveal it.
          maxHeight: 'calc(100dvh - 40px)',
          overflow: 'auto',
          background: RT.panel,
          border: `1px solid ${RT.borderHi}`,
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
        }}
      >
        {/* Header */}
        <div style={{
          flex: 'none',
          padding: '14px 16px 12px',
          borderBottom: `1px solid ${RT.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <div style={{ flex: 1, fontSize: 14, fontWeight: 600 }}>
            {initial ? 'Edit schedule' : 'New schedule'}
          </div>
          <button onClick={onClose} style={{ ...btn('mini'), width: 22, height: 22, fontSize: 12 }}>
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Name */}
          <div>
            <label style={labelStyle}>Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My daily task"
              style={fieldStyle}
            />
          </div>

          {/* Cron + preset */}
          <div>
            <label style={labelStyle}>Cron expression</label>
            <div style={{ display: 'flex', gap: 6 }}>
              {preset !== MANUAL_PRESET && preset !== LIMIT_RESET_PRESET && (
                <input
                  value={cron}
                  onChange={(e) => { setCron(e.target.value); setPreset(''); }}
                  placeholder="0 9 * * *"
                  style={{ ...fieldStyle, flex: 1 }}
                />
              )}
              <select
                value={preset}
                onChange={(e) => handlePreset(e.target.value)}
                style={{
                  ...fieldStyle,
                  width: (preset === MANUAL_PRESET || preset === LIMIT_RESET_PRESET) ? '100%' : 'auto',
                  flex: (preset === MANUAL_PRESET || preset === LIMIT_RESET_PRESET) ? 1 : 'none',
                  cursor: 'pointer',
                  fontSize: 12,
                  paddingRight: 6,
                }}
              >
                {CRON_PRESETS.map((p) => (
                  <option key={p.value || '__placeholder'} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            {preset === MANUAL_PRESET && (
              <div style={{ fontSize: 11, color: RT.textLow, marginTop: 5, fontFamily: FONT_MONO }}>
                Manual task - runs only when triggered with "Run now".
              </div>
            )}
            {preset === LIMIT_RESET_PRESET && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
                <div style={{ display: 'flex', gap: 10 }}>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle}>Limit window</label>
                    <select
                      value={resetWindow}
                      onChange={(e) => setResetWindow(e.target.value as 'five_hour' | 'seven_day')}
                      style={{ ...fieldStyle, cursor: 'pointer' }}
                    >
                      <option value="five_hour">{RESET_WINDOW_LABEL.five_hour}</option>
                      <option value="seven_day">{RESET_WINDOW_LABEL.seven_day}</option>
                    </select>
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle}>Delay after reset (min)</label>
                    <input
                      type="number"
                      min={0}
                      max={240}
                      value={delayMinutes}
                      onChange={(e) => {
                        const n = Math.round(Number(e.target.value));
                        setDelayMinutes(Number.isFinite(n) ? Math.max(0, Math.min(240, n)) : 0);
                      }}
                      style={fieldStyle}
                    />
                  </div>
                </div>
                <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>
                  Fires once the account's {resetWindow === 'seven_day' ? 'weekly' : '5-hour'} usage
                  limit resets{delayMinutes > 0 ? `, delayed ${delayMinutes} minute${delayMinutes === 1 ? '' : 's'}` : ''},
                  useful for queuing work that should start the moment the window rolls over.
                </div>
              </div>
            )}
            {(preset === MANUAL_PRESET || preset === LIMIT_RESET_PRESET) && cron.trim() !== '' && (
              <div style={{ fontSize: 11, color: RT.amber, marginTop: 5, fontFamily: FONT_MONO }}>
                Cron "{cron.trim()}" won't be saved or run - only one trigger can drive a task, and
                this one is set to {preset === MANUAL_PRESET ? 'Manual' : 'When my limit resets'}.
                Switch back to Scheduled to use it.
              </div>
            )}
            {!cronOk && (
              <div style={{ fontSize: 11, color: RT.red, marginTop: 5, fontFamily: FONT_MONO }}>
                Pick a schedule preset, enter a 5-field cron, or choose Manual.
              </div>
            )}
          </div>

          {/* Prompt / task */}
          <div>
            <label style={labelStyle}>Task / prompt</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the task Claude should run…"
              rows={4}
              style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5 }}
            />
            <div style={{ fontSize: 11, color: RT.textLow, marginTop: 5, fontFamily: FONT_MONO }}>
              Task/prompt OR an instructions file path — the schedule runs whichever is set.
            </div>
          </div>

          {/* Instructions file */}
          <div>
            <label style={labelStyle}>Instructions file</label>
            <input
              value={instructionsFile}
              onChange={(e) => setInstructionsFile(e.target.value)}
              placeholder="~/.claude-rc/jobs/my-task/instructions.md"
              style={fieldStyle}
            />
          </div>

          {/* Workdir */}
          <div>
            <label style={labelStyle}>Working directory</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={workdir}
                onChange={(e) => setWorkdir(e.target.value)}
                placeholder="/home/user/project"
                style={{ ...fieldStyle, flex: 1 }}
              />
              <button
                onClick={() => setShowBrowser(true)}
                style={{ ...btn('mini'), width: 'auto', padding: '0 8px', fontSize: 10, whiteSpace: 'nowrap' }}
                title="Browse directories"
              >
                Browse…
              </button>
            </div>
          </div>

          {/* Mode + Model (side by side) */}
          <div style={{ display: 'flex', gap: 10 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Mode</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as ModeKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="STANDARD">STANDARD</option>
                <option value="TEAMMATE">TEAMMATE</option>
                <option value="SAFE">SAFE</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value as ModelKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="DEFAULT">Default (Opus 4.8)</option>
                <option value="SONNET">Sonnet 5</option>
                <option value="HAIKU">Haiku 4.5</option>
                <option value="FABLE">Fable 5</option>
              </select>
            </div>
          </div>

          {/* Concurrency */}
          <div>
            <label style={labelStyle}>If already running</label>
            <select
              value={concurrency}
              onChange={(e) => setConcurrency(e.target.value as 'skip' | 'kill')}
              style={{ ...fieldStyle, cursor: 'pointer' }}
            >
              <option value="skip">Skip this run</option>
              <option value="kill">Kill the running one, then start</option>
            </select>
          </div>

          {/* Enabled */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: RT.textDim }}>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 14, height: 14, cursor: 'pointer', accentColor: RT.green }}
            />
            Enabled
          </label>

          {/* Inline error */}
          {error && (
            <div style={{
              fontSize: 12,
              color: RT.red,
              background: 'oklch(0.62 0.12 25 / 0.10)',
              border: `1px solid oklch(0.62 0.12 25 / 0.30)`,
              borderRadius: 6,
              padding: '8px 10px',
              fontFamily: FONT_MONO,
            }}>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          flex: 'none',
          padding: '12px 16px',
          borderTop: `1px solid ${RT.border}`,
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}>
          <button
            onClick={handleOpenSession}
            disabled={launching || pending}
            title="Launch a live Claude session using this schedule's workdir / mode / model"
            style={{
              background: 'transparent',
              border: `1px solid ${RT.border}`,
              borderRadius: 6,
              padding: '7px 12px',
              cursor: (launching || pending) ? 'wait' : 'pointer',
              color: RT.green,
              fontSize: 13,
              fontFamily: 'inherit',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              opacity: (launching || pending) ? 0.6 : 1,
            }}
          >
            ▸ {launching ? 'Opening…' : 'Open session'}
          </button>
          <div style={{ flex: 1 }} />
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: `1px solid ${RT.border}`,
              borderRadius: 6,
              padding: '7px 14px',
              cursor: 'pointer',
              color: RT.textDim,
              fontSize: 13,
              fontFamily: 'inherit',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={pending || !cronOk}
            title={!cronOk ? 'Pick a schedule preset, enter a 5-field cron, or choose Manual' : undefined}
            style={{
              background: RT.text,
              border: 'none',
              borderRadius: 6,
              padding: '7px 16px',
              cursor: (pending || !cronOk) ? (pending ? 'wait' : 'not-allowed') : 'pointer',
              color: RT.bg,
              fontSize: 13,
              fontFamily: 'inherit',
              fontWeight: 600,
              opacity: (pending || !cronOk) ? 0.6 : 1,
            }}
          >
            {pending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* DirBrowser overlay */}
      {showBrowser && (
        <DirBrowser
          deviceId={deviceId}
          initialPath={workdir || '/'}
          onSelect={(path) => setWorkdir(path)}
          onClose={() => setShowBrowser(false)}
        />
      )}
    </div>
  );
}
