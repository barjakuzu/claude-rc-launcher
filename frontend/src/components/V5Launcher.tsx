// V5Launcher.tsx — horizontal launcher bar with segmented mode pills + progressive disclosure.
import { useState, useEffect, useCallback, useRef } from 'react';
import { RT, FONT_MONO, Z } from '../tokens';
import { Icons } from './primitives';
import { api } from '../api';
import { DirBrowser } from './DirBrowser';

type ModeLabel = 'STANDARD' | 'TEAMMATE' | 'SAFE';
type ModelLabel = '1' | '2' | '3' | '4';

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

interface BrowseResult {
  path: string;
  parent: string | null;
  dirs: string[];
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
  const [nameFocus, setNameFocus] = useState(false);
  const [dirFocus, setDirFocus] = useState(false);
  const [dirOpen, setDirOpen] = useState(false);
  const [dirData, setDirData] = useState<BrowseResult | null>(null);
  const mounted = useRef(true);
  const lastBase = useRef('');

  useEffect(() => () => { mounted.current = false; }, []);

  // Path combobox: base = everything up to the last '/', tail filters entries.
  const browseBase = (v: string) => {
    const i = v.lastIndexOf('/');
    return i >= 0 ? v.slice(0, i + 1) : '/';
  };

  const loadDirs = useCallback(async (base: string) => {
    lastBase.current = base;
    try {
      const d = await api.browse(deviceId, base || '/') as BrowseResult;
      if (mounted.current && lastBase.current === base) setDirData(d);
    } catch {/* ignore */}
  }, [deviceId]);

  const handleDirChange = (v: string) => {
    setWorkdir(v);
    setDirOpen(true);
    const base = browseBase(v);
    if (base !== lastBase.current) loadDirs(base);
  };

  const descendInto = (dir: string) => {
    if (!dirData) return;
    const root = dirData.path === '/' ? '' : dirData.path;
    const next = `${root}/${dir}/`;
    setWorkdir(next);
    loadDirs(next);
  };

  const goUp = () => {
    if (!dirData?.parent) return;
    const p = dirData.parent.endsWith('/') ? dirData.parent : dirData.parent + '/';
    setWorkdir(p);
    loadDirs(p);
  };

  const dirTail = workdir.slice(browseBase(workdir).length).toLowerCase();
  const dirEntries = (dirData?.dirs ?? []).filter(
    (d) => !dirTail || d.toLowerCase().includes(dirTail),
  );

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

  const dirItemStyle: React.CSSProperties = {
    width: '100%', textAlign: 'left', background: 'transparent',
    border: 'none', borderRadius: 4, padding: '7px 9px', cursor: 'pointer',
    color: RT.text, fontFamily: FONT_MONO, fontSize: 12,
    display: 'flex', alignItems: 'center', gap: 7,
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
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
        {/* Name input — inline, Enter launches */}
        <div style={{
          flex: mobile ? '1 1 100%' : '1 1 auto', minWidth: mobile ? '100%' : 140,
          background: RT.panel, borderRadius: 8,
          border: `1px solid ${nameFocus ? RT.textDim : RT.border}`,
          transition: 'border-color .15s',
          padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <Icons.edit size={12} stroke={RT.textLow} />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleLaunch(); }}
            onFocus={() => setNameFocus(true)}
            onBlur={() => setNameFocus(false)}
            placeholder="session name · ↵ to launch"
            style={{
              flex: 1, background: 'transparent', color: RT.text,
              border: 'none', outline: 'none', fontFamily: FONT_MONO, fontSize: 12,
            }}
          />
        </div>

        {/* Workdir input — focus opens a folder dropdown */}
        <div style={{
          flex: mobile ? '1 1 100%' : '1.4 1 auto', minWidth: mobile ? '100%' : 200,
          background: RT.panel, borderRadius: 8,
          border: `1px solid ${dirFocus ? RT.textDim : RT.border}`,
          transition: 'border-color .15s',
          padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8,
          position: 'relative',
        }}>
          <Icons.folder size={12} stroke={RT.textLow} />
          <input
            value={workdir}
            onChange={(e) => handleDirChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { setDirOpen(false); handleLaunch(); }
              else if (e.key === 'Escape') setDirOpen(false);
            }}
            onFocus={() => { setDirFocus(true); setDirOpen(true); loadDirs(browseBase(workdir)); }}
            onBlur={() => { setDirFocus(false); setDirOpen(false); }}
            placeholder="/path/to/project"
            style={{
              flex: 1, background: 'transparent', color: RT.text,
              border: 'none', outline: 'none', fontFamily: FONT_MONO, fontSize: 12,
            }}
          />
          {dirOpen && dirData && (
            <div
              /* keep input focus while interacting with the list */
              onMouseDown={(e) => e.preventDefault()}
              style={{
                position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
                background: RT.panel, border: `1px solid ${RT.borderHi}`,
                borderRadius: 8, padding: 4, zIndex: Z.menu,
                boxShadow: '0 8px 24px rgba(0,0,0,.4)',
                maxHeight: 260, overflowY: 'auto',
              }}
            >
              <div style={{
                padding: '5px 9px', fontSize: 10, color: RT.textLow,
                fontFamily: FONT_MONO, letterSpacing: '.08em', textTransform: 'uppercase',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {dirData.path}
              </div>
              {dirData.parent !== null && (
                <button onClick={goUp} style={dirItemStyle}>
                  <Icons.back size={11} stroke={RT.textLow} /> ..
                </button>
              )}
              {dirEntries.map((d) => (
                <button key={d} onClick={() => descendInto(d)} style={dirItemStyle}>
                  <Icons.folder size={11} stroke={RT.textDim} /> {d}
                </button>
              ))}
              {dirEntries.length === 0 && (
                <div style={{ padding: '7px 9px', fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>
                  no matching subfolders
                </div>
              )}
            </div>
          )}
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
                zIndex: mode === m ? Z.raised : 0,
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
              <option value="">Default (Opus 4.8)</option>
              <option value="2">Sonnet 5</option>
              <option value="3">Haiku 4.5</option>
              <option value="4">Fable 5</option>
            </select>
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
