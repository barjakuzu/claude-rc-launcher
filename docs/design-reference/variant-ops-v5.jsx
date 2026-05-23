// variant-ops-v5.jsx — V5: layout that adapts to device count.
//
// Problem in V4: with only 2 devices and a side panel open, the main area
// is mostly empty space and the session rows are crammed into a 420px column
// with 22px action buttons.
//
// Solution: inverted layout. When a device is selected, the device list
// becomes a compact left rail (240px), and the device detail — launcher,
// sessions, scheduled, logs — takes the entire main area. Session rows
// breathe; action buttons are proper hit targets (32px); the launcher
// becomes a horizontal bar that uses its width instead of stacking.
//
// When NO device is selected, devices fill the main area as larger tiles
// (1 row of 2 when count=2, 2x2 when count=4, etc) with more visible metadata.

const { useState, useEffect, useRef } = React;

const VT = {
  bg:        'oklch(0.155 0.004 80)',
  bgRaised:  'oklch(0.195 0.006 80)',
  panel:     'oklch(0.215 0.006 80)',
  card:      'oklch(0.225 0.006 80)',
  cardHi:    'oklch(0.255 0.008 80)',
  border:    'oklch(0.28 0.007 80)',
  borderHi:  'oklch(0.36 0.009 80)',
  text:      'oklch(0.96 0.004 80)',
  textDim:   'oklch(0.72 0.006 80)',
  textLow:   'oklch(0.52 0.007 80)',
  accent:    'oklch(0.70 0.10 250)',
  green:     'oklch(0.66 0.10 150)',
  amber:     'oklch(0.72 0.09 78)',
  red:       'oklch(0.62 0.12 25)',
};

const vTint  = (hue, L = 0.66, C = 0.07) => `oklch(${L} ${C} ${hue})`;
const vSoft  = (hue) => `oklch(0.66 0.07 ${hue} / 0.14)`;
const vEdge  = (hue) => `oklch(0.66 0.07 ${hue} / 0.32)`;

// Use the user's real device count (2): pick first 2 from DEVICES but rename
// to match their setup — this prototype demonstrates the layout, not the data.
const V5_DEVICES = [
  { ...window.DEVICES[0], id: 'vm',   name: 'This machine (VM)', hostname: 'local', region: 'local', location: 'Local · Ubuntu 24.04.4 LTS', kind: 'vm',     hue: 250, cpuLoad: 0.24, sessions: 5, tokens: 1400000, tokenCap: 2000000, lastActivity: 'just now' },
  { ...window.DEVICES[1], id: 'home', name: 'Home (home-box)',    hostname: 'home-box.example.ts.net', region: 'tailnet', location: 'Brooklyn · tailscale', kind: 'server', hue: 30,  cpuLoad: 0,    sessions: 1, tokens: 0,       tokenCap: 2000000, online: true, lastActivity: '4m ago' },
];
const V5_SESSIONS = {
  vm: [
    { name: 'rc-Travel2',                       dir: 'root',         tokensK: 154, pct: 8,  mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01PdGuCB4Bsbv7JmcSWtKEhA', status: 'running' },
    { name: 'rc-Viewlogic',                     dir: 'root',         tokensK: 458, pct: 23, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_013RMteDkWHb2ALEZB6so4V1', status: 'running' },
    { name: 'rc-launcher',                      dir: 'rc-launcher',  tokensK: 431, pct: 22, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01KaYegnnfrYXnUTepogPP8F', status: 'running' },
    { name: 'rc-neonspace',                     dir: 'neonspace',    tokensK: 319, pct: 16, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01FBFPHGAQNBGdWCC1SLiFHM', status: 'running' },
    { name: 'rc-sched-apply-in-tech-jobs-0515', dir: 'root',         tokensK: 66,  pct: 3,  mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_014pbkVqFFyCs2qpPzsAP83z', status: 'running' },
  ],
  home: [
    { name: 'rc-home-cleanup', dir: 'root', tokensK: 12, pct: 1, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01homeCleanupAA', status: 'idle' },
  ],
};

function OpsV5({ initialOpenId = 'vm', initialMTab = 'devices' }) {
  const rootRef = useRef(null);
  const L = useV5Layout(rootRef);
  const [openId, setOpenId] = useState(initialOpenId);
  const [tab, setTab] = useState('running');
  // Mobile bottom-nav: which top-level section is active.
  const [mTab, setMTab] = useState(initialMTab);
  const open = V5_DEVICES.find((d) => d.id === openId);

  // Picking a non-devices tab clears any open device so the nav doesn't feel
  // stuck inside a detail view when the user jumps to e.g. Sessions.
  const pickMTab = (t) => {
    setMTab(t);
    if (t !== 'devices') setOpenId(null);
  };

  return (
    <div ref={rootRef} style={{
      width: '100%', height: '100%', background: VT.bg, color: VT.text,
      fontFamily: window.FONT_SANS,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <V5Header L={L} openId={openId} setOpenId={setOpenId} />

      {!L.mobile && <V5Strip />}

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {/* Left rail: compact device list (only shown when a device is selected, OR on tablet+) */}
        {open && !L.mobile && <V5DeviceRail openId={openId} setOpenId={setOpenId} />}

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {L.mobile
            ? (mTab === 'devices'
                ? (open
                    ? <V5DeviceDetail device={open} L={L} tab={tab} setTab={setTab} onClose={() => setOpenId(null)} />
                    : <V5DeviceGrid L={L} onOpen={setOpenId} />)
                : mTab === 'sessions'  ? <V5AllSessions  L={L} onOpenDevice={(id) => { setOpenId(id); setMTab('devices'); }} />
                : mTab === 'scheduled' ? <V5AllScheduled L={L} />
                : mTab === 'activity'  ? <V5Activity     L={L} />
                : null)
            : (open
                ? <V5DeviceDetail device={open} L={L} tab={tab} setTab={setTab} onClose={() => setOpenId(null)} />
                : <V5DeviceGrid L={L} onOpen={setOpenId} />)
          }
        </div>
      </div>

      {L.mobile
        ? <V5MobileNav active={mTab} onChange={pickMTab} />
        : <V5Footer />}
    </div>
  );
}

function useV5Layout(ref) {
  const [w, setW] = useState(1400);
  useEffect(() => {
    if (!ref.current) return;
    setW(ref.current.getBoundingClientRect().width);
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { width: w, mobile: w < 720, tablet: w >= 720 && w < 1100, desktop: w >= 1100 };
}

// ─── Header ─────────────────────────────────────────────────────────────────
function V5Header({ L, openId, setOpenId }) {
  return (
    <div style={{
      flex: 'none', height: L.mobile ? 52 : 50,
      borderBottom: `1px solid ${VT.border}`, background: VT.bgRaised,
      display: 'flex', alignItems: 'center',
      padding: L.mobile ? '0 14px' : '0 18px', gap: L.mobile ? 10 : 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <div style={{
          width: 24, height: 24, borderRadius: 6,
          border: `1px solid ${VT.borderHi}`, background: VT.panel,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: window.FONT_MONO, fontSize: 10, fontWeight: 600,
        }}>rc</div>
        {!L.mobile && (
          <>
            <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.005em' }}>Claude RC</div>
            <span style={{
              fontFamily: window.FONT_MONO, fontSize: 9.5, color: VT.amber,
              background: 'oklch(0.72 0.09 78 / 0.12)', border: `1px solid oklch(0.72 0.09 78 / 0.3)`,
              padding: '2px 6px', borderRadius: 4, letterSpacing: '.04em', textTransform: 'uppercase',
            }}>v1.5.1 · update</span>
          </>
        )}
      </div>

      {!L.mobile && <div style={{ width: 1, height: 20, background: VT.border, marginInline: 4 }} />}

      <V5MachineSelector L={L} openId={openId} setOpenId={setOpenId} />

      <div style={{ flex: 1 }} />

      {!L.mobile && (
        <div style={{
          background: VT.panel, border: `1px solid ${VT.border}`, borderRadius: 7,
          padding: '6px 11px', display: 'flex', alignItems: 'center', gap: 8, width: 280,
          fontFamily: window.FONT_MONO, fontSize: 11, color: VT.textLow,
        }}>
          <window.Icons.search size={11} stroke={VT.textLow} />
          <span style={{ flex: 1 }}>Search sessions, tasks…</span>
          <span style={{ padding: '1px 6px', border: `1px solid ${VT.border}`, borderRadius: 3, fontSize: 10 }}>⌘K</span>
        </div>
      )}

      <button style={vBtn('icon')}><window.Icons.share size={12} stroke={VT.textDim} /></button>
      <button style={vBtn('icon')}><window.Icons.more size={13} stroke={VT.textDim} /></button>
    </div>
  );
}

function V5MachineSelector({ L, openId, setOpenId }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const off = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const current = V5_DEVICES.find((d) => d.id === openId);
  const label = current ? current.name : 'All devices';
  const hueColor = current ? vTint(current.hue, 0.70, 0.10) : VT.textDim;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen((o) => !o)} style={{
        background: VT.panel, border: `1px solid ${VT.border}`, borderRadius: 8,
        padding: L.mobile ? '8px 12px' : '7px 12px', cursor: 'pointer',
        color: VT.text, fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 9,
        fontSize: 12, fontWeight: 500, minWidth: L.mobile ? 200 : 250,
      }}>
        {current
          ? <window.Dot color={hueColor} size={7} pulse={current.online} />
          : <div style={{ display: 'flex', gap: 2 }}>
              {V5_DEVICES.map((d) => <div key={d.id} style={{ width: 4, height: 8, background: vTint(d.hue, 0.68, 0.10), borderRadius: 1 }} />)}
            </div>}
        <span style={{ flex: 1, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
        <window.Icons.chevDown size={12} stroke={VT.textDim} />
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 6,
          background: VT.panel, border: `1px solid ${VT.borderHi}`, borderRadius: 10,
          width: L.mobile ? 'calc(100vw - 28px)' : 360, maxWidth: 'calc(100vw - 28px)',
          padding: 6, zIndex: 30, boxShadow: '0 12px 36px rgba(0,0,0,.4)',
        }}>
          <V5DropItem active={openId === null} onClick={() => { setOpenId(null); setOpen(false); }}
            primary="All devices" secondary={`${V5_DEVICES.length} connected · ${V5_DEVICES.filter((d) => d.online).length} online`}
          />
          <div style={{ height: 1, background: VT.border, margin: '4px 6px' }} />
          {V5_DEVICES.map((d) => (
            <V5DropItem key={d.id} active={openId === d.id}
              onClick={() => { setOpenId(d.id); setOpen(false); }}
              hue={d.hue} online={d.online}
              primary={d.name} secondary={d.hostname}
              right={<div style={{ textAlign: 'right' }}>
                <div style={{ fontFamily: window.FONT_MONO, fontSize: 11 }}>{window.fmtK(d.tokens)}</div>
                <div style={{ fontFamily: window.FONT_MONO, fontSize: 10, color: VT.textLow }}>{d.sessions} sess</div>
              </div>}
            />
          ))}
          <div style={{ height: 1, background: VT.border, margin: '4px 6px' }} />
          <V5DropItem icon={<window.Icons.plus size={13} stroke={VT.textDim} />}
            primary="Add a device…" secondary="npx claude-rc connect" muted onClick={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

function V5DropItem({ active, onClick, hue, online, primary, secondary, right, icon, muted }) {
  const hueColor = hue != null ? vTint(hue, 0.70, 0.10) : VT.textDim;
  return (
    <button onClick={onClick} style={{
      width: '100%', textAlign: 'left',
      background: active ? VT.bgRaised : 'transparent',
      border: 'none', borderRadius: 6, padding: '9px 10px',
      cursor: 'pointer', color: VT.text, fontFamily: 'inherit',
      display: 'flex', alignItems: 'center', gap: 10,
    }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = VT.bgRaised; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}>
      <div style={{ width: 16, display: 'flex', justifyContent: 'center' }}>
        {icon || (hue != null
          ? <window.Dot color={hueColor} size={7} pulse={online} />
          : <div style={{ width: 8, height: 8, borderRadius: 2, background: VT.borderHi }} />)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 500, color: muted ? VT.textDim : VT.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{primary}</div>
        {secondary && <div style={{ fontSize: 10, fontFamily: window.FONT_MONO, color: VT.textLow, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{secondary}</div>}
      </div>
      {right}
    </button>
  );
}

// ─── Top aggregate strip ────────────────────────────────────────────────────
function V5Strip() {
  const totalT = V5_DEVICES.reduce((s, d) => s + d.tokens, 0);
  const totalC = V5_DEVICES.reduce((s, d) => s + d.tokenCap, 0);
  const totalS = V5_DEVICES.reduce((s, d) => s + d.sessions, 0);
  const onlineN = V5_DEVICES.filter((d) => d.online).length;
  const cells = [
    { label: 'Online',   value: `${onlineN}/${V5_DEVICES.length}`, sub: `${V5_DEVICES.length - onlineN} offline`, dot: VT.green },
    { label: 'Sessions', value: totalS, sub: 'active' },
    { label: 'Tokens',   value: window.fmtK(totalT), sub: `of ${window.fmtK(totalC)}` },
    { label: 'Load',     value: `${Math.round((totalT / totalC) * 100)}%`, bar: (totalT / totalC) * 100 },
  ];
  return (
    <div style={{
      flex: 'none', borderBottom: `1px solid ${VT.border}`, background: VT.bg,
      display: 'grid', gridTemplateColumns: `repeat(${cells.length}, 1fr)`,
      padding: '14px 0',
    }}>
      {cells.map((c, i) => (
        <div key={c.label} style={{ padding: '0 24px', borderLeft: i === 0 ? 'none' : `1px solid ${VT.border}` }}>
          <div style={{ fontSize: 9, color: VT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 6 }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
            {c.dot && <window.Dot color={c.dot} size={6} pulse />}
            <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: '-.02em', fontFamily: window.FONT_MONO, lineHeight: 1 }}>{c.value}</div>
            {c.sub && <div style={{ fontSize: 10.5, color: VT.textLow, fontFamily: window.FONT_MONO }}>{c.sub}</div>}
          </div>
          {c.bar != null && <div style={{ marginTop: 8 }}><window.CapBar pct={c.bar} height={3} bg="rgba(255,255,255,.04)" color={VT.accent} /></div>}
        </div>
      ))}
    </div>
  );
}

// ─── Left rail (compact device switcher) ───────────────────────────────────
function V5DeviceRail({ openId, setOpenId }) {
  return (
    <div style={{
      flex: 'none', width: 240, borderRight: `1px solid ${VT.border}`,
      background: VT.bgRaised, display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{ flex: 'none', padding: '14px 14px 8px', display: 'flex', alignItems: 'center' }}>
        <div style={{ fontSize: 9.5, color: VT.textDim, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO }}>Devices · {V5_DEVICES.length}</div>
        <div style={{ flex: 1 }} />
        <button style={{ ...vBtn('icon'), width: 22, height: 22 }} title="Add device">
          <window.Icons.plus size={11} stroke={VT.textDim} />
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px 10px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {V5_DEVICES.map((d) => <V5RailItem key={d.id} d={d} active={openId === d.id} onClick={() => setOpenId(d.id)} />)}
      </div>
      {/* Aggregate "all devices" row */}
      <button onClick={() => setOpenId(null)} style={{
        flex: 'none', margin: '0 8px 12px', padding: '10px 11px',
        background: 'transparent', border: `1px dashed ${VT.border}`, borderRadius: 7,
        color: VT.textDim, fontFamily: 'inherit', fontSize: 11.5, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <window.Icons.back size={11} stroke={VT.textDim} /> Back to overview
      </button>
    </div>
  );
}

function V5RailItem({ d, active, onClick }) {
  const KindIcon = window.Icons[d.kind] || window.Icons.server;
  const hueColor = vTint(d.hue, 0.70, 0.10);
  const capPct = (d.tokens / d.tokenCap) * 100;
  return (
    <button onClick={onClick} style={{
      width: '100%', textAlign: 'left',
      background: active ? VT.cardHi : 'transparent',
      border: `1px solid ${active ? vEdge(d.hue) : 'transparent'}`,
      borderRadius: 8, padding: '9px 10px', cursor: 'pointer',
      color: VT.text, fontFamily: 'inherit',
      display: 'flex', flexDirection: 'column', gap: 6, position: 'relative',
    }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = VT.card; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}>
      {active && <div style={{ position: 'absolute', left: -1, top: 8, bottom: 8, width: 2, background: hueColor, borderRadius: 2 }} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 22, height: 22, borderRadius: 5, background: vSoft(d.hue), display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
          <KindIcon size={11} stroke={hueColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.name}</div>
        </div>
        <window.Dot color={d.online ? VT.green : VT.textLow} size={6} pulse={d.online} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontFamily: window.FONT_MONO, fontSize: 10, color: VT.textLow }}>
        <window.CapBar pct={capPct} height={2} bg="rgba(255,255,255,.04)" color={hueColor} />
        <span style={{ whiteSpace: 'nowrap' }}>{d.sessions} sess</span>
      </div>
    </button>
  );
}

// ─── Device detail (MAIN area) ─────────────────────────────────────────────
function V5DeviceDetail({ device, L, tab, setTab, onClose }) {
  const hueColor = vTint(device.hue, 0.70, 0.10);
  const sessions = V5_SESSIONS[device.id] || [];
  const scheduled = window.SCHEDULED.filter((s) => s.device === device.id || (device.id === 'vm' && ['cce', 'red'].includes(s.id)));
  const capPct = (device.tokens / device.tokenCap) * 100;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      {/* Device hero */}
      <div style={{
        flex: 'none', padding: L.mobile ? '14px' : '20px 28px',
        borderBottom: `1px solid ${VT.border}`, background: VT.bg,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 }}>
          {L.mobile && (
            <button onClick={onClose} style={{ ...vBtn('icon'), width: 30, height: 30 }}>
              <window.Icons.back size={14} stroke={VT.textDim} />
            </button>
          )}
          <div style={{
            width: L.mobile ? 36 : 44, height: L.mobile ? 36 : 44, borderRadius: 10, flex: 'none',
            background: vSoft(device.hue), color: hueColor,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {React.createElement(window.Icons[device.kind] || window.Icons.server, { size: L.mobile ? 18 : 22, stroke: hueColor })}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ fontSize: L.mobile ? 17 : 22, fontWeight: 600, letterSpacing: '-.015em' }}>{device.name}</div>
              <window.Dot color={device.online ? VT.green : VT.textLow} size={7} pulse={device.online} />
              <span style={{
                fontSize: 10, fontFamily: window.FONT_MONO, color: device.online ? VT.green : VT.textLow,
                letterSpacing: '.06em', textTransform: 'uppercase',
              }}>{device.online ? 'online' : 'offline'}</span>
            </div>
            <div style={{ fontSize: 11.5, color: VT.textDim, fontFamily: window.FONT_MONO, marginTop: 4, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <span>{device.hostname}</span>
              <span style={{ color: VT.borderHi }}>·</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <window.Icons.globe size={11} stroke={VT.textLow} /> {device.location || device.region}
              </span>
              <span style={{ color: VT.borderHi }}>·</span>
              <span>{Math.round(device.cpuLoad * 100)}% cpu</span>
            </div>
          </div>
          {!L.mobile && (
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={vBtn('ghost')}><window.Icons.refresh size={12} stroke={VT.textDim} /> Restart all</button>
              <button style={vBtn('ghost')}><window.Icons.terminal size={12} stroke={VT.textDim} /> SSH</button>
            </div>
          )}
        </div>

        {/* Mini-stats row inside the hero — uses the empty horizontal space */}
        <div style={{ display: 'grid', gridTemplateColumns: L.mobile ? '1fr 1fr' : `repeat(4, 1fr)`, gap: L.mobile ? 10 : 0, padding: L.mobile ? 0 : 0 }}>
          <V5Stat label="Token usage" value={`${window.fmtK(device.tokens)} / ${window.fmtK(device.tokenCap)}`} bar={capPct} barColor={hueColor} mobile={L.mobile} />
          <V5Stat label="Sessions"     value={device.sessions} sub="active"            divider={!L.mobile} mobile={L.mobile} />
          <V5Stat label="Last activity"value={device.lastActivity} sub={device.region} divider={!L.mobile} mobile={L.mobile} />
          <V5Stat label="CPU load"     value={`${Math.round(device.cpuLoad * 100)}%`}  bar={device.cpuLoad * 100} barColor={device.cpuLoad > 0.85 ? VT.red : device.cpuLoad > 0.6 ? VT.amber : hueColor} divider={!L.mobile} mobile={L.mobile} />
        </div>
      </div>

      {/* Launcher — full-width horizontal bar */}
      <V5Launcher device={device} L={L} />

      {/* Tabs + body */}
      <V5DetailTabs tab={tab} setTab={setTab} sessions={sessions} scheduled={scheduled} />
      <V5DetailBody device={device} tab={tab} sessions={sessions} scheduled={scheduled} L={L} />
    </div>
  );
}

function V5Stat({ label, value, sub, bar, barColor, divider, mobile }) {
  return (
    <div style={{ padding: mobile ? 0 : '0 22px', borderLeft: !mobile && divider ? `1px solid ${VT.border}` : 'none' }}>
      <div style={{ fontSize: 9, color: VT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: mobile ? 14 : 16, fontWeight: 500, letterSpacing: '-.015em', fontFamily: window.FONT_MONO }}>{value}</div>
      {bar != null && <div style={{ marginTop: 6 }}><window.CapBar pct={bar} height={3} bg="rgba(255,255,255,.05)" color={barColor} /></div>}
      {sub && <div style={{ fontSize: 10, color: VT.textLow, marginTop: 4, fontFamily: window.FONT_MONO }}>{sub}</div>}
    </div>
  );
}

// ─── Launcher (full-width horizontal bar) ──────────────────────────────────
function V5Launcher({ device, L }) {
  const [mode, setMode] = useState('STANDARD');
  return (
    <div style={{
      flex: 'none', padding: L.mobile ? '12px 14px' : '14px 28px',
      borderBottom: `1px solid ${VT.border}`, background: VT.bgRaised,
    }}>
      <div style={{ fontSize: 9.5, color: VT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 9 }}>
        Launch new session on <span style={{ color: VT.textDim }}>{device.name}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: L.mobile ? 'wrap' : 'nowrap', alignItems: 'center' }}>
        <div style={{
          flex: L.mobile ? '1 1 100%' : '1 1 auto', minWidth: L.mobile ? '100%' : 200,
          background: VT.panel, border: `1px solid ${VT.border}`, borderRadius: 8,
          padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <window.Icons.folder size={12} stroke={VT.textLow} />
          <input defaultValue="/root" style={{
            flex: 1, background: 'transparent', color: VT.text, border: 'none',
            outline: 'none', fontFamily: window.FONT_MONO, fontSize: 12,
          }} />
        </div>
        <div style={{ display: 'flex', gap: 8, flex: L.mobile ? '1 1 100%' : 'none' }}>
          {['STANDARD', 'TEAMMATE', 'SAFE'].map((m) => (
            <button key={m} onClick={() => setMode(m)} style={{
              background: mode === m ? VT.text : VT.panel,
              color: mode === m ? VT.bg : VT.textDim,
              border: `1px solid ${mode === m ? VT.text : VT.border}`,
              borderRadius: 8, padding: '9px 12px', cursor: 'pointer',
              fontFamily: window.FONT_MONO, fontSize: 10.5, fontWeight: 600,
              letterSpacing: '.04em',
              flex: L.mobile ? 1 : 'none',
            }}>{m}</button>
          ))}
        </div>
        <button style={vBtn('ghost')} disabled>
          <window.Icons.chevDown size={11} stroke={VT.textDim} /> Options
        </button>
        <button style={{
          background: VT.text, color: VT.bg, border: 'none',
          padding: '10px 18px', borderRadius: 8, cursor: 'pointer',
          fontFamily: 'inherit', fontSize: 12.5, fontWeight: 600,
          display: 'inline-flex', alignItems: 'center', gap: 7,
          flex: L.mobile ? '1 1 100%' : 'none', justifyContent: 'center',
        }}>
          <window.Icons.play size={11} stroke={VT.bg} /> Launch session
        </button>
      </div>
    </div>
  );
}

// ─── Tabs + body ───────────────────────────────────────────────────────────
function V5DetailTabs({ tab, setTab, sessions, scheduled }) {
  return (
    <div style={{ flex: 'none', display: 'flex', borderBottom: `1px solid ${VT.border}`, padding: '0 20px', background: VT.bg }}>
      {[
        ['running',   'Sessions',  sessions.length],
        ['scheduled', 'Scheduled', scheduled.length],
        ['logs',      'Logs',      null],
        ['settings',  'Settings',  null],
      ].map(([id, label, count]) => (
        <button key={id} onClick={() => setTab(id)} style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          padding: '13px 16px',
          color: tab === id ? VT.text : VT.textLow,
          borderBottom: `2px solid ${tab === id ? VT.text : 'transparent'}`,
          fontFamily: window.FONT_MONO, fontSize: 11, fontWeight: 500,
          letterSpacing: '.06em', textTransform: 'uppercase',
        }}>
          {label}{count != null && <span style={{ color: VT.textLow, marginLeft: 5 }}>{count}</span>}
        </button>
      ))}
      <div style={{ flex: 1 }} />
      <button style={{ ...vBtn('ghost'), alignSelf: 'center', marginBottom: 8 }}><window.Icons.refresh size={11} stroke={VT.textDim} /> Resume</button>
    </div>
  );
}

function V5DetailBody({ device, tab, sessions, scheduled, L }) {
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: L.mobile ? 12 : '16px 20px', background: VT.bg }}>
      {tab === 'running' && (
        sessions.length === 0
          ? <V5Empty text={device.online ? `No active sessions on ${device.name}. Launch one above.` : 'Device offline.'} />
          : <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {sessions.map((s) => <V5SessionRow key={s.sessionId} hue={device.hue} s={s} L={L} />)}
            </div>
      )}
      {tab === 'scheduled' && (
        scheduled.length === 0
          ? <V5Empty text="No scheduled tasks on this device." />
          : <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {scheduled.map((s) => <V5ScheduledRow key={s.id} hue={device.hue} s={s} L={L} />)}
            </div>
      )}
      {tab === 'logs' && (
        <pre style={{ margin: 0, fontFamily: window.FONT_MONO, fontSize: 12, color: VT.textDim, lineHeight: 1.6, whiteSpace: 'pre-wrap', background: VT.card, border: `1px solid ${VT.border}`, borderRadius: 10, padding: 16 }}>
{`[12:04:18] ${device.name} heartbeat ok · cpu=${Math.round(device.cpuLoad * 100)}%
[12:04:09] session.tick · ${device.sessions} active
[12:03:55] tokens=${window.fmtK(device.tokens)} (${Math.round(device.tokens / device.tokenCap * 100)}%)
[12:03:32] reconnect ok · last_lag=312ms
[12:03:01] daemon healthy
[12:00:00] daily token reset`}
        </pre>
      )}
      {tab === 'settings' && <V5Empty text="Device settings coming soon." />}
    </div>
  );
}

function V5Empty({ text }) {
  return <div style={{ padding: 60, textAlign: 'center', color: VT.textLow, fontSize: 13, border: `1px dashed ${VT.border}`, borderRadius: 10, background: VT.card }}>{text}</div>;
}

// ─── Session row — full-width, breathable, REAL buttons ────────────────────
function V5SessionRow({ hue, s, L }) {
  const hueColor = vTint(hue, 0.70, 0.10);
  return (
    <div style={{
      background: VT.card, border: `1px solid ${VT.border}`,
      borderRadius: 10, padding: L.mobile ? 14 : '14px 18px',
      display: 'grid',
      gridTemplateColumns: L.mobile ? '1fr' : 'minmax(220px, 1.4fr) minmax(180px, 1fr) auto',
      gap: L.mobile ? 12 : 18, alignItems: 'center',
    }}>
      {/* Name + dir */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 5 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0 }}>{s.name}</div>
          <V5StatusPill status={s.status} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: window.FONT_MONO, fontSize: 11, color: VT.textLow }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <window.Icons.folder size={10} stroke={VT.textLow} /> {s.dir}
          </span>
          <span style={{ color: VT.borderHi }}>·</span>
          <span>{s.mode}</span>
          <span style={{ color: VT.borderHi }}>·</span>
          <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 220 }}>{s.sessionId.slice(0, 30)}…</span>
        </div>
      </div>

      {/* Tokens + bar */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 5 }}>
          <span style={{ fontFamily: window.FONT_MONO, fontSize: 14, fontWeight: 500 }}>{s.tokensK}K</span>
          <span style={{ fontFamily: window.FONT_MONO, fontSize: 11, color: VT.textLow }}>tokens · {s.pct}%</span>
        </div>
        <window.CapBar pct={s.pct} height={4} bg="rgba(255,255,255,.04)" color={hueColor} />
      </div>

      {/* Actions — proper hit targets */}
      <div style={{ display: 'flex', gap: 6, justifyContent: L.mobile ? 'space-between' : 'flex-end' }}>
        <V5IconButton label="Copy session ID"><window.Icons.copy size={14} stroke={VT.textDim} /></V5IconButton>
        <V5IconButton label="Open preview"><window.Icons.link size={14} stroke={VT.textDim} /></V5IconButton>
        <V5IconButton label="Resume" accent={VT.green}><window.Icons.refresh size={14} /></V5IconButton>
        <V5IconButton label="Stop" accent={VT.red}><window.Icons.stop size={12} /></V5IconButton>
        <V5IconButton label="More"><window.Icons.more size={14} stroke={VT.textDim} /></V5IconButton>
      </div>
    </div>
  );
}

function V5IconButton({ children, accent, label }) {
  const [hover, setHover] = useState(false);
  return (
    <button title={label}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        background: hover ? VT.cardHi : VT.panel,
        color: accent || VT.textDim,
        border: `1px solid ${VT.border}`, borderRadius: 7,
        width: 34, height: 34, padding: 0, cursor: 'pointer',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background .12s, border-color .12s',
        borderColor: hover && accent ? accent : VT.border,
      }}>
      {/* The icon component renders with stroke="currentColor" by default; setting color
          on the button lets accent (green/red) take effect. */}
      {children && React.cloneElement(children, accent ? { stroke: accent } : {})}
    </button>
  );
}

function V5StatusPill({ status }) {
  const m = ({
    running:  { label: 'running',  color: VT.green,  pulse: true },
    thinking: { label: 'thinking', color: VT.amber,  pulse: true },
    idle:     { label: 'idle',     color: VT.textLow, pulse: false },
    stopped:  { label: 'stopped',  color: VT.red,    pulse: false },
  })[status] || { label: status, color: VT.textLow, pulse: false };
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontSize: 9.5, color: m.color, letterSpacing: '.08em', textTransform: 'uppercase', fontFamily: window.FONT_MONO,
      padding: '2px 7px', borderRadius: 4,
      background: 'oklch(0.66 0.10 150 / 0)',
      border: `1px solid ${m.color === VT.textLow ? VT.border : 'currentColor'}`,
      color: m.color, opacity: m.color === VT.textLow ? 0.7 : 1,
    }}>
      <window.Dot color={m.color} size={5} pulse={m.pulse} />
      {m.label}
    </span>
  );
}

function V5ScheduledRow({ hue, s, L }) {
  return (
    <div style={{
      background: VT.card, border: `1px solid ${VT.border}`,
      borderRadius: 10, padding: L.mobile ? 14 : '14px 18px',
      display: 'grid', gridTemplateColumns: L.mobile ? '1fr' : '1.5fr 1fr auto', gap: 16, alignItems: 'center',
    }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 5 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.005em' }}>{s.name}</div>
          <span style={{
            fontSize: 9.5, fontFamily: window.FONT_MONO, letterSpacing: '.06em', textTransform: 'uppercase',
            color: s.enabled ? VT.green : VT.textLow,
            padding: '2px 7px', borderRadius: 4,
            background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
          }}>{s.enabled ? 'enabled' : 'paused'}</span>
        </div>
        <div style={{ fontSize: 11, fontFamily: window.FONT_MONO, color: VT.textLow, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><window.Icons.clock size={10} stroke={VT.textLow} /> {s.cron}</span>
          <span>({s.schedule})</span>
          <span style={{ color: VT.borderHi }}>·</span>
          <span>{s.mode}</span>
        </div>
      </div>
      <div style={{ fontSize: 11, fontFamily: window.FONT_MONO, color: VT.textDim, display: 'flex', alignItems: 'center', gap: 6 }}>
        <window.Icons.folder size={10} stroke={VT.textLow} />
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.dir}</span>
        <span style={{ color: VT.borderHi }}>·</span>
        <span>last run {s.lastRunDaysAgo}d ago</span>
      </div>
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <V5IconButton label="Run now" accent={VT.green}><window.Icons.play size={12} /></V5IconButton>
        <V5IconButton label="Edit"><window.Icons.terminal size={13} stroke={VT.textDim} /></V5IconButton>
        <V5IconButton label="Delete" accent={VT.red}><window.Icons.stop size={11} /></V5IconButton>
      </div>
    </div>
  );
}

// ─── Devices grid (when no device selected) — large rich tiles ─────────────
function V5DeviceGrid({ L, onOpen }) {
  // Adapt columns to count: 2 devices → 2 wide (max 660px each); 3 → 3; 4+ → 3.
  const n = V5_DEVICES.length;
  const cols = L.mobile ? 1 : L.tablet ? Math.min(2, n) : Math.min(3, n);
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: L.mobile ? 14 : 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 16, gap: 10 }}>
        <div style={{ fontSize: 11, color: VT.textDim, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO }}>Devices · {n}</div>
        <div style={{ fontSize: 11, color: VT.textLow, fontFamily: window.FONT_MONO }}>sorted by activity</div>
        <div style={{ flex: 1 }} />
        <button style={vBtn('ghost')}><window.Icons.plus size={12} stroke={VT.textDim} /> Add device</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 14, maxWidth: cols === 2 ? 1200 : 'none' }}>
        {V5_DEVICES.map((d) => <V5BigCard key={d.id} d={d} onClick={() => onOpen(d.id)} />)}
      </div>
    </div>
  );
}

function V5BigCard({ d, onClick }) {
  const KindIcon = window.Icons[d.kind] || window.Icons.server;
  const hueColor = vTint(d.hue, 0.70, 0.10);
  const capPct = (d.tokens / d.tokenCap) * 100;
  const [hover, setHover] = useState(false);
  return (
    <div onClick={onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        cursor: 'pointer',
        background: hover ? VT.cardHi : VT.card,
        border: `1px solid ${hover ? vEdge(d.hue) : VT.border}`,
        borderRadius: 12, padding: 18,
        display: 'flex', flexDirection: 'column', gap: 14,
        transition: 'border-color .12s, background .12s',
        opacity: d.online ? 1 : 0.7, position: 'relative',
      }}>
      <div style={{ position: 'absolute', left: 0, top: 14, bottom: 14, width: 2, background: hueColor, borderRadius: 2, opacity: 0.7 }} />
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 9, flex: 'none',
          background: vSoft(d.hue), color: hueColor,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={18} stroke={hueColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-.01em' }}>{d.name}</div>
            <window.Dot color={d.online ? VT.green : VT.textLow} size={7} pulse={d.online} />
          </div>
          <div style={{ fontSize: 11.5, color: VT.textLow, fontFamily: window.FONT_MONO, marginTop: 3 }}>{d.hostname}</div>
        </div>
        <window.Icons.chevRight size={16} stroke={VT.textLow} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 16 }}>
        <V5Stat label="Tokens"   value={window.fmtK(d.tokens)} bar={capPct} barColor={hueColor} />
        <V5Stat label="Sessions" value={d.sessions} sub="active" />
        <V5Stat label="CPU"      value={`${Math.round(d.cpuLoad * 100)}%`} sub={d.lastActivity} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, color: hueColor }}>
          <window.Sparkline data={d.spark} w={300} h={32} color={hueColor} fillOpacity={0.10} dotEnd />
        </div>
        <button onClick={(e) => { e.stopPropagation(); onClick(); }} style={{
          background: VT.panel, color: VT.text, border: `1px solid ${VT.border}`,
          borderRadius: 7, padding: '8px 12px', cursor: 'pointer',
          fontFamily: 'inherit', fontSize: 11.5, fontWeight: 500,
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          Open <window.Icons.chevRight size={11} stroke={VT.text} />
        </button>
      </div>
    </div>
  );
}

// ─── Footer ────────────────────────────────────────────────────────────────
function V5Footer() {
  return (
    <div style={{
      flex: 'none', height: 30, borderTop: `1px solid ${VT.border}`,
      background: VT.bgRaised, display: 'flex', alignItems: 'center',
      padding: '0 18px', gap: 14,
      fontFamily: window.FONT_MONO, fontSize: 10, color: VT.textLow, letterSpacing: '.04em',
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: VT.green }}>
        <window.Dot color={VT.green} size={5} pulse /> connected
      </span>
      <span>relay.claude-rc.io</span>
      <span>last sync 12s ago</span>
      <div style={{ flex: 1 }} />
      <span>v1.5.1</span>
    </div>
  );
}

// ─── Button helper ─────────────────────────────────────────────────────────
function vBtn(kind) {
  const base = { fontFamily: 'inherit', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' };
  if (kind === 'icon')  return { ...base, background: VT.panel, border: `1px solid ${VT.border}`, borderRadius: 7, width: 30, height: 30, padding: 0, color: VT.textDim };
  if (kind === 'ghost') return { ...base, background: VT.panel, border: `1px solid ${VT.border}`, borderRadius: 7, padding: '7px 12px', color: VT.text, fontSize: 12, fontWeight: 500, gap: 6 };
  return base;
}

// ─── Mobile bottom navigation (iOS/Android-style) ──────────────────────────
function V5MobileNav({ active, onChange }) {
  const totalS  = V5_DEVICES.reduce((s, d) => s + d.sessions, 0);
  const tasksN  = window.SCHEDULED.length;
  const items = [
    { id: 'devices',   label: 'Devices',   icon: window.Icons.server, count: V5_DEVICES.length },
    { id: 'sessions',  label: 'Sessions',  icon: window.Icons.terminal, count: totalS, dot: true },
    { id: 'scheduled', label: 'Scheduled', icon: window.Icons.clock,    count: tasksN },
    { id: 'activity',  label: 'Activity',  icon: window.Icons.share },
  ];
  return (
    <div style={{
      flex: 'none',
      borderTop: `1px solid ${VT.border}`,
      background: VT.bgRaised,
      // Bottom safe-area inset for notched devices; falls back to 8px.
      paddingBottom: 'max(8px, env(safe-area-inset-bottom))',
      paddingTop: 6,
      display: 'grid', gridTemplateColumns: `repeat(${items.length}, 1fr)`,
    }}>
      {items.map((it) => {
        const isActive = active === it.id;
        const color = isActive ? VT.text : VT.textLow;
        const Icon = it.icon;
        return (
          <button key={it.id} onClick={() => onChange(it.id)} style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            padding: '6px 4px 4px', display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: 3, color,
            fontFamily: 'inherit', minHeight: 52, position: 'relative',
          }}>
            <div style={{ position: 'relative', display: 'flex' }}>
              <Icon size={20} stroke={color} sw={isActive ? 2 : 1.6} />
              {it.dot && it.count > 0 && (
                <span style={{
                  position: 'absolute', top: -3, right: -6,
                  minWidth: 14, height: 14, padding: '0 4px',
                  borderRadius: 7, background: VT.green, color: VT.bg,
                  fontFamily: window.FONT_MONO, fontSize: 9, fontWeight: 700,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  border: `1.5px solid ${VT.bgRaised}`,
                }}>{it.count}</span>
              )}
            </div>
            <div style={{
              fontSize: 10, fontWeight: isActive ? 600 : 500,
              letterSpacing: '.01em',
            }}>{it.label}</div>
            {isActive && (
              <div style={{
                position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)',
                width: 24, height: 2, background: VT.text, borderRadius: 2,
              }} />
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─── Mobile top-level views ────────────────────────────────────────────────
function V5MobileHeader({ title, subtitle, right }) {
  return (
    <div style={{
      flex: 'none', padding: '16px 16px 12px',
      borderBottom: `1px solid ${VT.border}`, background: VT.bg,
      display: 'flex', alignItems: 'flex-end', gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 9.5, color: VT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 4 }}>{subtitle}</div>
        <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-.02em' }}>{title}</div>
      </div>
      {right}
    </div>
  );
}

function V5AllSessions({ L, onOpenDevice }) {
  // Flatten sessions across devices with device meta attached for chips.
  const all = V5_DEVICES.flatMap((d) =>
    (V5_SESSIONS[d.id] || []).map((s) => ({ s, d }))
  );
  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <V5MobileHeader
        subtitle={`${all.length} active · across ${V5_DEVICES.length} devices`}
        title="Sessions"
        right={<button style={{ ...vBtn('icon'), width: 34, height: 34 }}><window.Icons.filter size={14} stroke={VT.textDim} /></button>}
      />
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {all.map(({ s, d }) => {
          const hueColor = vTint(d.hue, 0.70, 0.10);
          return (
            <div key={d.id + s.sessionId} style={{
              background: VT.card, border: `1px solid ${VT.border}`,
              borderRadius: 10, padding: 12,
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, fontSize: 14, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
                <V5StatusPill status={s.status} />
              </div>
              <button onClick={() => onOpenDevice(d.id)} style={{
                background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                display: 'inline-flex', alignItems: 'center', gap: 6,
                color: hueColor, fontFamily: window.FONT_MONO, fontSize: 10.5, fontWeight: 500,
                letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start',
              }}>
                <window.Dot color={hueColor} size={6} pulse={d.online} />
                {d.name}
                <window.Icons.chevRight size={10} stroke={hueColor} />
              </button>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: window.FONT_MONO, fontSize: 11, color: VT.textLow }}>
                <window.Icons.folder size={10} stroke={VT.textLow} />
                <span>{s.dir}</span>
                <span style={{ color: VT.borderHi }}>·</span>
                <span>{s.tokensK}K</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button style={mobileActionBtn()}><window.Icons.link size={13} stroke={VT.textDim} /> Preview</button>
                <button style={mobileActionBtn()}><window.Icons.refresh size={13} stroke={VT.green} /> Resume</button>
                <button style={{ ...vBtn('icon'), width: 36, height: 36, marginLeft: 'auto' }}>
                  <window.Icons.stop size={12} stroke={VT.red} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function V5AllScheduled({ L }) {
  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <V5MobileHeader
        subtitle={`${window.SCHEDULED.length} tasks across devices`}
        title="Scheduled"
        right={<button style={{ ...vBtn('icon'), width: 34, height: 34 }}><window.Icons.plus size={14} stroke={VT.textDim} /></button>}
      />
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {window.SCHEDULED.map((s) => {
          const d = V5_DEVICES.find((x) => x.id === s.device) || V5_DEVICES[0];
          const hueColor = vTint(d.hue, 0.70, 0.10);
          return (
            <div key={s.id} style={{
              background: VT.card, border: `1px solid ${VT.border}`,
              borderRadius: 10, padding: 12, display: 'flex', flexDirection: 'column', gap: 7,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
                <span style={{
                  fontSize: 9, fontFamily: window.FONT_MONO, letterSpacing: '.06em', textTransform: 'uppercase',
                  color: s.enabled ? VT.green : VT.textLow,
                  padding: '2px 6px', borderRadius: 4,
                  background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
                }}>{s.enabled ? 'enabled' : 'paused'}</span>
              </div>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: hueColor, fontFamily: window.FONT_MONO, fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start' }}>
                <window.Dot color={hueColor} size={5} pulse={d.online} /> {d.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: window.FONT_MONO, fontSize: 11, color: VT.textLow }}>
                <window.Icons.clock size={10} stroke={VT.textLow} /> {s.cron}
                <span>({s.schedule})</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button style={mobileActionBtn()}><window.Icons.play size={12} stroke={VT.green} /> Run now</button>
                <button style={mobileActionBtn()}><window.Icons.terminal size={13} stroke={VT.textDim} /> Edit</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function V5Activity({ L }) {
  const events = [
    { t: 'just now',  dev: 'vm',   text: 'rc-Travel2 token usage crossed 150K', kind: 'info' },
    { t: '2m ago',    dev: 'vm',   text: 'rc-launcher resumed after 4s reconnect', kind: 'ok' },
    { t: '5m ago',    dev: 'home', text: 'Heartbeat ok · cpu=0% · idle', kind: 'info' },
    { t: '12m ago',   dev: 'vm',   text: 'Scheduled task chaincentral-cold-email ran', kind: 'ok' },
    { t: '34m ago',   dev: 'vm',   text: 'rc-neonspace tokens=319K (16%)', kind: 'info' },
    { t: '1h ago',    dev: 'home', text: 'Device came online', kind: 'ok' },
    { t: '2h ago',    dev: 'vm',   text: 'Daemon restart after update v1.5.0 → v1.5.1', kind: 'warn' },
  ];
  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <V5MobileHeader
        subtitle="last 24 hours"
        title="Activity"
        right={<button style={{ ...vBtn('icon'), width: 34, height: 34 }}><window.Icons.filter size={14} stroke={VT.textDim} /></button>}
      />
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 0 }}>
        {events.map((e, i) => {
          const d = V5_DEVICES.find((x) => x.id === e.dev) || V5_DEVICES[0];
          const hueColor = vTint(d.hue, 0.70, 0.10);
          const kindColor = e.kind === 'ok' ? VT.green : e.kind === 'warn' ? VT.amber : VT.textDim;
          return (
            <div key={i} style={{ display: 'flex', gap: 12, position: 'relative', paddingBottom: i === events.length - 1 ? 0 : 14 }}>
              <div style={{ flex: 'none', width: 14, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <span style={{ width: 8, height: 8, borderRadius: 4, background: hueColor, marginTop: 5, border: `2px solid ${VT.bg}`, boxShadow: `0 0 0 1.5px ${hueColor}` }} />
                {i < events.length - 1 && <span style={{ flex: 1, width: 1, background: VT.border, marginTop: 4 }} />}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: VT.text, lineHeight: 1.4 }}>{e.text}</div>
                <div style={{ marginTop: 4, fontSize: 10.5, fontFamily: window.FONT_MONO, color: VT.textLow, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: hueColor }}>{d.name}</span>
                  <span style={{ color: VT.borderHi }}>·</span>
                  <span>{e.t}</span>
                  <span style={{ color: VT.borderHi }}>·</span>
                  <span style={{ color: kindColor, letterSpacing: '.06em', textTransform: 'uppercase' }}>{e.kind}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function mobileActionBtn() {
  return {
    background: VT.panel, color: VT.text,
    border: `1px solid ${VT.border}`, borderRadius: 7,
    padding: '8px 12px', cursor: 'pointer',
    fontFamily: 'inherit', fontSize: 11.5, fontWeight: 500,
    display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 36,
  };
}

Object.assign(window, {
  OpsV5,
  OpsV5Overview:         () => React.createElement(OpsV5, { initialOpenId: null }),
  OpsV5MobileSessions:   () => React.createElement(OpsV5, { initialOpenId: null, initialMTab: 'sessions' }),
  OpsV5MobileScheduled:  () => React.createElement(OpsV5, { initialOpenId: null, initialMTab: 'scheduled' }),
  OpsV5MobileActivity:   () => React.createElement(OpsV5, { initialOpenId: null, initialMTab: 'activity' }),
});
