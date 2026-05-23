// V5Launcher.tsx — horizontal launcher bar with segmented mode pills + progressive disclosure.
import { useState, useEffect, useCallback, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';

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

export interface V5LauncherProps {
  deviceId: string;
  deviceName: string;
  mobile?: boolean;
  onLaunched: () => void;
}

export function V5Launcher({ deviceId, deviceName, mobile = false, onLaunched }: V5LauncherProps) {
  const [workdir, setWorkdir] = useState('');
  const [mode, setMode] = useState<ModeLabel>('STANDARD');
  const [showOptions, setShowOptions] = useState(false);
  const [model, setModel] = useState<ModelLabel | ''>('');
  const [name, setName] = useState('');
  const [sandbox, setSandbox] = useState(false);
  const [showBrowser, setShowBrowser] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

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
      const body: Record<string, unknown> = { mode: mapMode(mode), workdir };
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

  const optionFieldStyle: React.CSSProperties = {
    background: RT.panel, color: RT.text,
    border: `1px solid ${RT.border}`, borderRadius: 6,
    padding: '6px 9px', fontFamily: FONT_MONO, fontSize: 11, outline: 'none',
  };

  return (
    <div style={{
      position: 'relative', flex: 'none',
      padding: mobile ? '12px 14px' : '14px 28px',
      borderBottom: `1px solid ${RT.border}`, background: RT.bgRaised,
    }}>
      {/* Label */}
      <div style={{
        fontSize: 9.5, color: RT.textLow, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 9,
      }}>
        Launch new session on <span style={{ color: RT.textDim }}>{deviceName}</span>
      </div>

      {/* Main row */}
      <div style={{ display: 'flex', gap: 8, flexWrap: mobile ? 'wrap' : 'nowrap', alignItems: 'center' }}>
        {/* Workdir input */}
        <div style={{
          flex: mobile ? '1 1 100%' : '1 1 auto', minWidth: mobile ? '100%' : 200,
          background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 8,
          padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <Icons.folder size={12} stroke={RT.textLow} />
          <input
            value={workdir}
            onChange={(e) => setWorkdir(e.target.value)}
            placeholder="/path/to/project"
            style={{
              flex: 1, background: 'transparent', color: RT.text,
              border: 'none', outline: 'none', fontFamily: FONT_MONO, fontSize: 12,
            }}
          />
        </div>

        {/* Segmented mode pills */}
        <div style={{ display: 'flex', gap: 0, flex: mobile ? '1 1 100%' : 'none' }}>
          {(['STANDARD', 'TEAMMATE', 'SAFE'] as ModeLabel[]).map((m, i) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                background: mode === m ? RT.text : RT.panel,
                color: mode === m ? RT.bg : RT.textDim,
                border: `1px solid ${mode === m ? RT.text : RT.border}`,
                borderRadius: i === 0 ? '8px 0 0 8px' : i === 2 ? '0 8px 8px 0' : '0',
                marginLeft: i > 0 ? -1 : 0,
                padding: '9px 12px', cursor: 'pointer',
                fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 600,
                letterSpacing: '.04em',
                flex: mobile ? 1 : 'none',
                zIndex: mode === m ? 1 : 0,
                position: 'relative',
              }}
            >
              {m}
            </button>
          ))}
        </div>

        {/* Options ghost button */}
        <button
          onClick={() => setShowOptions((v) => !v)}
          style={{
            background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
            padding: '9px 12px', color: RT.text, fontSize: 12, fontWeight: 500,
            cursor: 'pointer', fontFamily: 'inherit',
            display: 'inline-flex', alignItems: 'center', gap: 6,
            flex: 'none',
          }}
        >
          <Icons.chevDown size={11} stroke={RT.textDim} />
          Options
        </button>

        {/* Launch button */}
        <button
          onClick={handleLaunch}
          disabled={pending}
          style={{
            background: pending ? RT.panel : RT.text,
            color: pending ? RT.textDim : RT.bg,
            border: 'none', padding: '10px 18px', borderRadius: 8,
            cursor: pending ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit', fontSize: 12.5, fontWeight: 600,
            display: 'inline-flex', alignItems: 'center', gap: 7,
            flex: mobile ? '1 1 100%' : 'none', justifyContent: 'center',
            transition: 'background .15s, color .15s',
          }}
        >
          {pending
            ? <><Icons.spinner size={11} stroke={RT.textDim} /> Launching…</>
            : <><Icons.play size={11} stroke={RT.bg} /> Launch session</>
          }
        </button>
      </div>

      {/* Expanded options */}
      {showOptions && (
        <div style={{
          marginTop: 10,
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
            <label style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>Model</label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value as ModelLabel | '')}
              style={{ ...optionFieldStyle, flex: 1 }}
            >
              <option value="">Default (Opus)</option>
              <option value="2">Sonnet 4.6</option>
              <option value="3">Haiku 4.5</option>
            </select>
          </div>

          {/* Name input */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="auto-generated"
              style={{ ...optionFieldStyle, flex: 1 }}
            />
          </div>

          {/* Workdir + Browse */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, flex: 'none', width: 64 }}>Workdir</label>
            <input
              value={workdir}
              onChange={(e) => setWorkdir(e.target.value)}
              placeholder="/path/to/project"
              style={{ ...optionFieldStyle, flex: 1 }}
            />
            <button
              onClick={() => setShowBrowser(true)}
              style={{
                background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 6,
                padding: '6px 10px', color: RT.text, fontSize: 11, fontWeight: 500,
                cursor: 'pointer', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 4, flex: 'none',
              }}
            >
              <Icons.folder size={12} stroke={RT.textDim} /> Browse…
            </button>
          </div>

          {/* Sandbox checkbox */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ flex: 'none', width: 64 }} />
            <label style={{
              display: 'flex', alignItems: 'center', gap: 6,
              cursor: 'pointer', fontSize: 11, color: RT.textDim, fontFamily: FONT_MONO, userSelect: 'none',
            }}>
              <input
                type="checkbox" checked={sandbox}
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
        <div style={{ marginTop: 6, fontSize: 11, color: RT.red, fontFamily: FONT_MONO, lineHeight: 1.4 }}>
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
