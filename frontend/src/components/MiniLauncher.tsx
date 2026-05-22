// MiniLauncher.tsx — in-panel launcher with progressive disclosure.
import { useState, useEffect, useCallback, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { btn } from './btn';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';

// Mode select value → backend mode string
type ModeLabel = 'STANDARD' | 'TEAMMATE' | 'SAFE';
type ModelLabel = '1' | '2' | '3';

function mapMode(label: ModeLabel): string {
  if (label === 'STANDARD') return 'c';
  if (label === 'TEAMMATE') return 'ci';
  return 'safe';
}

interface ProjectsResult {
  projects: string[];
  default: string;
  default_name: string;
}

export interface MiniLauncherProps {
  deviceId: string;
  deviceName: string;
  mobile?: boolean;
  onLaunched: () => void;
}

export function MiniLauncher({ deviceId, deviceName, mobile = false, onLaunched }: MiniLauncherProps) {
  // Core fields (minimal row)
  const [workdir, setWorkdir] = useState('');
  const [mode, setMode] = useState<ModeLabel>('STANDARD');

  // Options fields (expanded area)
  const [showOptions, setShowOptions] = useState(false);
  const [model, setModel] = useState<ModelLabel | ''>('');
  const [name, setName] = useState('');
  const [sandbox, setSandbox] = useState(false);
  const [showBrowser, setShowBrowser] = useState(false);

  // State
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  // Load default workdir from /rc/projects on mount
  const loadProjects = useCallback(async () => {
    try {
      const data = await api.projects(deviceId) as ProjectsResult;
      if (!mounted.current) return;
      if (data?.default) setWorkdir(data.default);
    } catch {/* ignore */}
  }, [deviceId]);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  const handleLaunch = async () => {
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const body: Record<string, unknown> = {
        mode: mapMode(mode),
        workdir,
      };
      if (name.trim()) body.name = name.trim();
      if (model) body.model = model;
      if (sandbox) body.sandbox = sandbox;

      const res = await api.start(deviceId, body) as { ok?: boolean; message?: string };
      if (!mounted.current) return;
      if (res && res.ok === false) {
        setError(res.message ?? 'Launch failed.');
      } else {
        setName('');
        onLaunched();
      }
    } catch {
      if (!mounted.current) return;
      setError('Network error — could not reach device.');
    } finally {
      if (mounted.current) setPending(false);
    }
  };

  // Shared field style (mono, matches prototype)
  const fieldStyle: React.CSSProperties = {
    background: RT.panel,
    color: RT.text,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    padding: '7px 9px',
    fontFamily: FONT_MONO,
    fontSize: 11,
    outline: 'none',
  };

  return (
    <div style={{ position: 'relative', flex: 'none', padding: '12px 14px', borderBottom: `1px solid ${RT.border}`, background: RT.bg }}>
      {/* Label row */}
      <div style={{
        fontSize: 9,
        color: RT.textLow,
        letterSpacing: '.14em',
        textTransform: 'uppercase',
        fontFamily: FONT_MONO,
        marginBottom: 8,
      }}>
        Launch on {deviceName}
      </div>

      {/* Minimal row — workdir + mode + Launch */}
      <div style={{ display: 'flex', gap: 6, flexWrap: mobile ? 'wrap' : 'nowrap' }}>
        <input
          value={workdir}
          onChange={(e) => setWorkdir(e.target.value)}
          placeholder="/path/to/project"
          style={{
            ...fieldStyle,
            flex: 1,
            minWidth: mobile ? '100%' : 0,
          }}
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as ModeLabel)}
          style={fieldStyle}
        >
          <option value="STANDARD">STANDARD</option>
          <option value="TEAMMATE">TEAMMATE</option>
          <option value="SAFE">SAFE</option>
        </select>
        <button
          onClick={handleLaunch}
          disabled={pending}
          style={{
            background: pending ? RT.panel : RT.text,
            color: pending ? RT.textDim : RT.bg,
            border: 'none',
            padding: '7px 13px',
            borderRadius: 6,
            cursor: pending ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
            fontSize: 11,
            fontWeight: 600,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            flex: mobile ? 1 : 'none',
            justifyContent: 'center',
            transition: 'background .15s, color .15s',
          }}
        >
          {pending
            ? <><Icons.spinner size={10} stroke={RT.textDim} /> Launching…</>
            : <><Icons.play size={10} stroke={RT.bg} /> Launch</>
          }
        </button>
      </div>

      {/* Options toggle */}
      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <button
          onClick={() => setShowOptions((v) => !v)}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '0 2px',
            fontSize: 10,
            color: RT.textLow,
            fontFamily: FONT_MONO,
            letterSpacing: '.04em',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          {showOptions
            ? <Icons.chevDown size={10} stroke={RT.textLow} />
            : <Icons.chevRight size={10} stroke={RT.textLow} />
          }
          {showOptions ? 'Hide options' : 'Options'}
        </button>
      </div>

      {/* Expanded options area */}
      {showOptions && (
        <div style={{
          marginTop: 8,
          padding: '10px 12px',
          background: RT.panel,
          border: `1px solid ${RT.border}`,
          borderRadius: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}>
          {/* Model select */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>
              Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value as ModelLabel | '')}
              style={{ ...fieldStyle, flex: 1, fontSize: 10, padding: '5px 8px' }}
            >
              <option value="">Default (Opus)</option>
              <option value="2">Sonnet 4.6</option>
              <option value="3">Haiku 4.5</option>
            </select>
          </div>

          {/* Name input */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>
              Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="auto-generated"
              style={{ ...fieldStyle, flex: 1, fontSize: 10, padding: '5px 8px' }}
            />
          </div>

          {/* Workdir + Browse button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>
              Workdir
            </label>
            <input
              value={workdir}
              onChange={(e) => setWorkdir(e.target.value)}
              placeholder="/path/to/project"
              style={{ ...fieldStyle, flex: 1, fontSize: 10, padding: '5px 8px' }}
            />
            <button
              onClick={() => setShowBrowser(true)}
              style={{
                ...btn('tinyText'),
                fontSize: 10,
                padding: '5px 10px',
                gap: 4,
                flex: 'none',
              }}
            >
              <Icons.folder size={11} stroke={RT.textDim} />
              Browse…
            </button>
          </div>

          {/* Sandbox checkbox */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ flex: 'none', width: 64 }} />
            <label style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              cursor: 'pointer',
              fontSize: 10,
              color: RT.textDim,
              fontFamily: FONT_MONO,
              userSelect: 'none',
            }}>
              <input
                type="checkbox"
                checked={sandbox}
                onChange={(e) => setSandbox(e.target.checked)}
                style={{ accentColor: RT.text, width: 13, height: 13, cursor: 'pointer' }}
              />
              Sandbox mode
            </label>
          </div>
        </div>
      )}

      {/* Inline error */}
      {error && (
        <div style={{
          marginTop: 6,
          fontSize: 10,
          color: RT.red,
          fontFamily: FONT_MONO,
          lineHeight: 1.4,
        }}>
          {error}
        </div>
      )}

      {/* Directory browser overlay */}
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
