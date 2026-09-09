// ScheduleModal.tsx: create / edit a schedule.
//
// Redesign (2026-09-09-usability, task-m3): the previous layout led with
// a raw cron expression, showed a red validation error before the user
// had typed anything, and asked for a working directory and an
// instructions file with no visible relationship between them. This
// version is organized around what someone is actually deciding:
//   WHEN, a preset, a manual run, or "when my limit resets" (a raw
//         cron is one tap further in, never the first thing shown).
//   WHAT, a typed prompt OR an instructions file, mutually exclusive
//         (never both fields open at once).
//   WHERE, which device and which directory, grouped together.
// Mode/model/concurrency move into a collapsed "Advanced" disclosure so
// a new schedule's default screen is short on a 390px phone. No branch
// of WHEN ever starts in an invalid state, so no red text shows before
// the user has made a choice.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO, Z } from '../tokens';
import { btn } from './btn';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';
import type { Schedule } from '../types';

// ── Cron presets ──────────────────────────────────────────────────────────────
// The custom-cron escape hatch: chosen explicitly, or landed on when
// editing a schedule whose stored cron doesn't match any preset below.
const CUSTOM_PRESET = '__custom__';
const DEFAULT_PRESET_VALUE = '0 9 * * *'; // "Daily at 9 AM", filled in the instant WHEN switches to Scheduled, so that state is never blank/invalid.

// A schedule must send a 5-field cron or an explicit Manual (cron: null):
// an empty/blank cron string 400s server-side. Cheap client-side check
// (field count only; the server still validates each field's contents).
export function isValidCronString(value: string): boolean {
  return value.trim().split(/\s+/).filter(Boolean).length === 5;
}

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Every hour',          value: '0 * * * *' },
  { label: 'Every 2 hours',       value: '0 */2 * * *' },
  { label: 'Every 6 hours',       value: '0 */6 * * *' },
  { label: 'Daily at 9 AM',       value: '0 9 * * *' },
  { label: 'Daily at noon',       value: '0 12 * * *' },
  { label: 'Daily at midnight',   value: '0 0 * * *' },
  { label: 'Weekdays at 9 AM',    value: '0 9 * * 1-5' },
  { label: 'Weekly on Monday',    value: '0 9 * * 1' },
  { label: 'Monthly on the 1st',  value: '0 0 1 * *' },
  { label: 'Custom cron…',        value: CUSTOM_PRESET },
];

/** The dropdown value matching a stored cron string: the preset whose
 * value equals it verbatim, CUSTOM_PRESET for any other non-empty cron
 * (an expression typed by hand, or one this list doesn't happen to
 * carry), or '' for an empty/missing cron. */
function presetForCron(cron: string): string {
  if (!cron) return '';
  const known = CRON_PRESETS.find((p) => p.value === cron);
  return known ? known.value : CUSTOM_PRESET;
}

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
  padding: '9px 10px',
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

const sectionStyle: React.CSSProperties = {
  background: RT.card,
  border: `1px solid ${RT.border}`,
  borderRadius: 10,
  padding: 12,
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
};

const sectionHeadStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: RT.textLow,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: RT.textLow,
  fontFamily: FONT_MONO,
  lineHeight: 1.5,
};

// ── Segmented control ─────────────────────────────────────────────────────────
// A standard, familiar control for "pick exactly one of a few options",
// used for WHEN's three trigger kinds and WHAT's prompt/file choice.
function Segmented<T extends string>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div style={{ display: 'flex', gap: 4, background: RT.bg, border: `1px solid ${RT.border}`, borderRadius: 8, padding: 3 }}>
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            style={{
              flex: 1,
              minWidth: 0,
              padding: '8px 4px',
              borderRadius: 6,
              border: 'none',
              background: active ? RT.cardHi : 'transparent',
              color: active ? RT.text : RT.textDim,
              fontFamily: FONT_MONO,
              fontSize: 12,
              fontWeight: active ? 600 : 500,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

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

type WhenMode = 'manual' | 'cron' | 'limit_reset';
type WhatMode = 'prompt' | 'file';

// ── Component ─────────────────────────────────────────────────────────────────
export interface ScheduleModalProps {
  deviceId: string;
  initial?: ScheduleWithTrigger | null;
  onClose: () => void;
  onSaved: () => void;
  /** Optional: lets the WHERE section offer a device picker for a NEW
   * schedule. Omitted by DeviceDetail.tsx, whose device is already fixed
   * by the page the user is on; AllScheduled.tsx's "+" is the one place a
   * schedule can be created with no device already in view, and passes
   * this so the user can actually choose one instead of always landing
   * on whichever device happened to be first. Ignored while editing an
   * existing schedule (`initial` set): moving one to another device is
   * "Copy to…"/"Move to…" elsewhere, not a field in this form. */
  devices?: { id: string; name: string }[];
}

export function ScheduleModal({ deviceId, initial, onClose, onSaved, devices }: ScheduleModalProps) {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  // WHERE
  const [targetDevice, setTargetDevice] = useState(deviceId);
  const [workdir, setWorkdir] = useState(initial?.workdir ?? '');
  const [showBrowser, setShowBrowser] = useState(false);

  // WHEN
  const [whenMode, setWhenMode] = useState<WhenMode>(() => {
    if (initial?.trigger?.kind === 'limit_reset') return 'limit_reset';
    if (initial && initial.cron) return 'cron';
    return 'manual'; // also the default for a brand-new schedule, always a valid state.
  });
  const [cron, setCron] = useState(initial?.cron ?? '');
  const [cronPreset, setCronPreset] = useState<string>(() =>
    (initial?.trigger?.kind !== 'limit_reset' && initial?.cron) ? presetForCron(initial.cron) : '',
  );
  const [resetWindow, setResetWindow] = useState<'five_hour' | 'seven_day'>(
    initial?.trigger?.window === 'seven_day' ? 'seven_day' : 'five_hour',
  );
  const [delayMinutes, setDelayMinutes] = useState<number>(
    typeof initial?.trigger?.delay_minutes === 'number' ? initial.trigger.delay_minutes : 0,
  );
  // Fix round 1 (Important 5): the modal used to never send catch_up at
  // all, so every save through it silently reset a stored "none" back to
  // the server-side default "latest" (schedules.py normalizes a missing
  // key to "latest") - catch_up was configurable only by calling the API
  // directly, with no way to see or change it here. Now a real field.
  const [catchUp, setCatchUp] = useState<'latest' | 'none'>(
    initial?.trigger?.catch_up === 'none' ? 'none' : 'latest',
  );

  // WHAT
  const [whatMode, setWhatMode] = useState<WhatMode>(() =>
    (initial?.instructions_file && !initial?.prompt) ? 'file' : 'prompt',
  );
  const [prompt, setPrompt] = useState(initial?.prompt ?? '');
  const [instructionsFile, setInstructionsFile] = useState(initial?.instructions_file ?? '');

  // Everything else
  const [name, setName] = useState(initial?.name ?? '');
  const [mode, setMode] = useState<ModeKey>(API_TO_MODE[initial?.mode ?? ''] ?? 'STANDARD');
  const [model, setModel] = useState<ModelKey>(API_TO_MODEL[initial?.model ?? ''] ?? 'DEFAULT');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [concurrency, setConcurrency] = useState<'skip' | 'kill'>(
    initial?.concurrency === 'kill' ? 'kill' : 'skip',
  );
  // Advanced (mode/model/concurrency) starts collapsed for a fresh
  // schedule (the defaults are already sensible), but opens by default
  // when editing one that already deviates from them, so nothing a user
  // set earlier is hidden from them without a tap.
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(() => {
    if (!initial) return false;
    const modeIsDefault = (API_TO_MODE[initial.mode ?? ''] ?? 'STANDARD') === 'STANDARD';
    const modelIsDefault = (API_TO_MODEL[initial.model ?? ''] ?? 'DEFAULT') === 'DEFAULT';
    const concurrencyIsDefault = (initial.concurrency ?? 'skip') !== 'kill';
    return !(modeIsDefault && modelIsDefault && concurrencyIsDefault);
  });

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Manual and "when my limit resets" both always send cron: null (valid).
  // Scheduled is only invalid if the preset was explicitly cleared back
  // to nothing, or Custom was chosen without a usable 5-field expression.
  // Neither of those is ever the state WHEN starts in (see
  // handleWhenModeChange below), so this never shows red text before the
  // user has made a choice.
  const cronOk =
    whenMode !== 'cron' ||
    (cronPreset !== '' && (cronPreset !== CUSTOM_PRESET || isValidCronString(cron)));

  function handleWhenModeChange(next: WhenMode) {
    setWhenMode(next);
    // Switching INTO Scheduled with nothing chosen yet fills in a
    // sensible default immediately, rather than leaving the picker on
    // its placeholder. That placeholder state is exactly what used to
    // show a red validation error the instant this modal opened.
    if (next === 'cron' && !cronPreset) {
      setCronPreset(DEFAULT_PRESET_VALUE);
      setCron(DEFAULT_PRESET_VALUE);
    }
  }

  function handleCronPresetChange(value: string) {
    setCronPreset(value);
    if (value !== CUSTOM_PRESET) {
      setCron(value); // the preset IS the cron string.
    }
    // Custom: leave `cron` as whatever raw text is already there, the
    // user is about to type or edit it below.
  }

  // Launch a live Claude session on this device using the schedule's
  // workdir / mode / model, useful for finalizing/testing the prompt.
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
      const res = await api.start(targetDevice, body);
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
      const isLimitReset = whenMode === 'limit_reset';
      const isManual = whenMode === 'manual';
      const body = {
        name,
        cron: (isManual || isLimitReset) ? null : cron,
        // WHAT is exclusive: the inactive field is always sent explicitly
        // cleared, never left out of the payload. An omitted key means
        // "leave the stored value alone" server-side, which would let a
        // stale value from before a WHAT switch survive invisibly.
        prompt: whatMode === 'prompt' ? prompt : '',
        instructions_file: whatMode === 'file' ? instructionsFile : '',
        workdir,
        mode:    MODE_TO_API[mode],
        model:   MODEL_TO_API[model],
        concurrency,
        enabled,
        trigger: isLimitReset
          ? { kind: 'limit_reset', window: resetWindow, delay_minutes: delayMinutes, catch_up: catchUp }
          : null,
      };

      let result: { ok?: boolean; message?: string };
      if (initial) {
        result = await api.schedUpdate(targetDevice, { id: initial.id, ...body }) as typeof result;
      } else {
        result = await api.schedCreate(targetDevice, body) as typeof result;
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

  const canPickDevice = !initial && devices && devices.length > 0;

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

          {/* WHEN */}
          <div style={sectionStyle}>
            <div style={sectionHeadStyle}>When</div>
            <Segmented<WhenMode>
              value={whenMode}
              onChange={handleWhenModeChange}
              options={[
                { value: 'manual', label: 'Manual' },
                { value: 'cron', label: 'Schedule' },
                { value: 'limit_reset', label: 'On reset' },
              ]}
            />

            {whenMode === 'manual' && (
              <div style={hintStyle}>Runs only when you tap "Run now".</div>
            )}

            {whenMode === 'cron' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <select
                  value={cronPreset}
                  onChange={(e) => handleCronPresetChange(e.target.value)}
                  style={{ ...fieldStyle, cursor: 'pointer' }}
                >
                  <option value="">Choose a preset…</option>
                  {CRON_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
                {cronPreset === CUSTOM_PRESET && (
                  <input
                    value={cron}
                    onChange={(e) => setCron(e.target.value)}
                    placeholder="0 9 * * *"
                    style={fieldStyle}
                  />
                )}
                {!cronOk && (
                  <div style={{ ...hintStyle, color: RT.red }}>
                    {cronPreset === CUSTOM_PRESET
                      ? 'Enter a 5-field cron expression (minute hour day month weekday).'
                      : 'Choose a preset, or switch to Custom to type your own.'}
                  </div>
                )}
              </div>
            )}

            {whenMode === 'limit_reset' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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
                <div>
                  <label style={labelStyle}>If the hub was offline when the limit reset</label>
                  <select
                    value={catchUp}
                    onChange={(e) => setCatchUp(e.target.value as 'latest' | 'none')}
                    style={{ ...fieldStyle, cursor: 'pointer' }}
                  >
                    <option value="latest">Fire once, for the latest reset</option>
                    <option value="none">Skip it - only fire for a reset seen live</option>
                  </select>
                </div>
                <div style={hintStyle}>
                  Fires once the account's {resetWindow === 'seven_day' ? 'weekly' : '5-hour'} usage
                  limit resets{delayMinutes > 0 ? `, delayed ${delayMinutes} minute${delayMinutes === 1 ? '' : 's'}` : ''}.
                </div>
              </div>
            )}

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: RT.textDim }}>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                style={{ width: 14, height: 14, cursor: 'pointer', accentColor: RT.green }}
              />
              Enabled
            </label>
          </div>

          {/* WHAT */}
          <div style={sectionStyle}>
            <div style={sectionHeadStyle}>What</div>
            <Segmented<WhatMode>
              value={whatMode}
              onChange={setWhatMode}
              options={[
                { value: 'prompt', label: 'Prompt' },
                { value: 'file', label: 'Instructions file' },
              ]}
            />
            {whatMode === 'prompt' ? (
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Describe the task Claude should run…"
                rows={4}
                style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5 }}
              />
            ) : (
              <input
                value={instructionsFile}
                onChange={(e) => setInstructionsFile(e.target.value)}
                placeholder="~/.claude-rc/jobs/my-task/instructions.md"
                style={fieldStyle}
              />
            )}
          </div>

          {/* WHERE */}
          <div style={sectionStyle}>
            <div style={sectionHeadStyle}>Where</div>
            {canPickDevice && (
              <div>
                <label style={labelStyle}>Device</label>
                {devices!.length > 1 ? (
                  <select
                    value={targetDevice}
                    onChange={(e) => setTargetDevice(e.target.value)}
                    style={{ ...fieldStyle, cursor: 'pointer' }}
                  >
                    {devices!.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                ) : (
                  <div style={{ ...fieldStyle, color: RT.textDim }}>{devices![0].name}</div>
                )}
              </div>
            )}
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
                  style={{ ...btn('mini'), width: 'auto', padding: '0 10px', fontSize: 10, whiteSpace: 'nowrap' }}
                  title="Browse directories"
                >
                  Browse…
                </button>
              </div>
            </div>
          </div>

          {/* Advanced: mode / model / concurrency */}
          <details
            open={advancedOpen}
            onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
            style={{ ...sectionStyle, padding: 0, border: 'none', background: 'transparent' }}
          >
            <summary style={{ ...sectionHeadStyle, cursor: 'pointer', listStyle: 'none', padding: '2px 0' }}>
              Advanced
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
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
            </div>
          </details>

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
            title={!cronOk ? 'Choose a schedule preset, enter a 5-field cron, or pick Manual' : undefined}
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
          deviceId={targetDevice}
          initialPath={workdir || '/'}
          onSelect={(path) => setWorkdir(path)}
          onClose={() => setShowBrowser(false)}
        />
      )}
    </div>
  );
}
