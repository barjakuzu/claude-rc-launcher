// ScheduleModal.tsx — create / edit a schedule.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { btn } from './btn';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';
import type { Schedule } from '../types';

// ── Cron presets ──────────────────────────────────────────────────────────────
const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Choose a preset…', value: '' },
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
type ModelKey = 'DEFAULT' | 'SONNET' | 'HAIKU';

const MODE_TO_API: Record<ModeKey, string>  = { STANDARD: 'c', TEAMMATE: 'ci', SAFE: 'safe' };
const API_TO_MODE: Record<string, ModeKey>  = { c: 'STANDARD', ci: 'TEAMMATE', safe: 'SAFE' };
const MODEL_TO_API: Record<ModelKey, string> = { DEFAULT: '', SONNET: '2', HAIKU: '3' };
const API_TO_MODEL: Record<string, ModelKey> = { '': 'DEFAULT', '2': 'SONNET', '3': 'HAIKU' };

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

// ── Component ─────────────────────────────────────────────────────────────────
export interface ScheduleModalProps {
  deviceId: string;
  initial?: Schedule | null;
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

  const [preset,       setPreset]       = useState('');
  const [pending,      setPending]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [showBrowser,  setShowBrowser]  = useState(false);

  // Preset → fill cron input
  function handlePreset(value: string) {
    setPreset(value);
    if (value) setCron(value);
  }

  async function handleSave() {
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const body = {
        name,
        cron,
        prompt,
        instructions_file: instructionsFile || undefined,
        workdir,
        mode:    MODE_TO_API[mode],
        model:   MODEL_TO_API[model],
        enabled,
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
        zIndex: 50,
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
          maxHeight: 'calc(100vh - 40px)',
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
              <input
                value={cron}
                onChange={(e) => { setCron(e.target.value); setPreset(''); }}
                placeholder="0 9 * * *"
                style={{ ...fieldStyle, flex: 1 }}
              />
              <select
                value={preset}
                onChange={(e) => handlePreset(e.target.value)}
                style={{
                  ...fieldStyle,
                  width: 'auto',
                  flex: 'none',
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
              placeholder="/root/.claude-rc/jobs/my-task/instructions.md"
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
                <option value="DEFAULT">Default</option>
                <option value="SONNET">Sonnet</option>
                <option value="HAIKU">Haiku</option>
              </select>
            </div>
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
          justifyContent: 'flex-end',
        }}>
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
            disabled={pending}
            style={{
              background: RT.text,
              border: 'none',
              borderRadius: 6,
              padding: '7px 16px',
              cursor: pending ? 'wait' : 'pointer',
              color: RT.bg,
              fontSize: 13,
              fontFamily: 'inherit',
              fontWeight: 600,
              opacity: pending ? 0.6 : 1,
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
