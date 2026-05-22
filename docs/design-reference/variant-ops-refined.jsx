// variant-ops-refined.jsx — V4 + Mobile: refined Ops Console.
// Same architecture as V3, but with:
//   • subtle, professional palette (low-chroma device hues)
//   • top machine selector dropdown (primary nav)
//   • responsive: same component renders desktop OR mobile based on width

const { useState, useEffect, useRef } = React;

// Refined dark palette — quieter, more professional.
const RT = {
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
  accent:    'oklch(0.70 0.10 250)',    // subtle blue
  green:     'oklch(0.66 0.10 150)',
  amber:     'oklch(0.72 0.09 78)',
  red:       'oklch(0.62 0.12 25)',
};

// Low-chroma device tints — almost neutral with a hint of hue.
const tintFor   = (hue, L = 0.66, C = 0.07) => `oklch(${L} ${C} ${hue})`;
const tintSoft  = (hue) => `oklch(0.66 0.07 ${hue} / 0.14)`;
const tintEdge  = (hue) => `oklch(0.66 0.07 ${hue} / 0.32)`;

// Container width tracking → "desktop" | "tablet" | "mobile".
function useLayout(ref) {
  const [w, setW] = useState(1200);
  useEffect(() => {
    if (!ref.current) return;
    // Seed from current size synchronously — ResizeObserver's initial callback
    // is unreliable inside focus-overlay re-mounts.
    setW(ref.current.getBoundingClientRect().width);
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { width: w, mobile: w < 720, tablet: w >= 720 && w < 1100, desktop: w >= 1100 };
}

// ─── Root ───────────────────────────────────────────────────────────────────
function OpsRefined({ initialOpenId = null }) {
  const rootRef = useRef(null);
  const L = useLayout(rootRef);
  const [openId, setOpenId] = useState(initialOpenId);
  const [tab, setTab] = useState('running');
  const open = window.DEVICES.find((d) => d.id === openId);

  return (
    <div ref={rootRef} style={{
      width: '100%', height: '100%', background: RT.bg, color: RT.text,
      fontFamily: window.FONT_SANS,
      display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative',
    }}>
      <RHeader RT={RT} L={L} openId={openId} setOpenId={setOpenId} />

      {!L.mobile && <RStrip RT={RT} L={L} />}

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>
        <RGrid RT={RT} L={L} onOpen={setOpenId} openId={openId} />

        {/* Side panel — desktop slides from right; mobile becomes full-screen overlay */}
        {open && (
          L.mobile
            ? <RMobileDetail  RT={RT} device={open} onClose={() => setOpenId(null)} tab={tab} setTab={setTab} />
            : <RSidePanel     RT={RT} device={open} onClose={() => setOpenId(null)} tab={tab} setTab={setTab} L={L} />
        )}
      </div>

      {!L.mobile && <RFooter RT={RT} />}
    </div>
  );
}

// ─── Header ─────────────────────────────────────────────────────────────────
function RHeader({ RT, L, openId, setOpenId }) {
  return (
    <div style={{
      flex: 'none',
      height: L.mobile ? 52 : 48,
      borderBottom: `1px solid ${RT.border}`,
      background: RT.bgRaised,
      display: 'flex', alignItems: 'center',
      padding: L.mobile ? '0 14px' : '0 18px',
      gap: L.mobile ? 10 : 14,
    }}>
      {/* Mark */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <div style={{
          width: 22, height: 22, borderRadius: 5,
          border: `1px solid ${RT.borderHi}`,
          background: RT.panel,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: window.FONT_MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.02em',
          color: RT.text,
        }}>rc</div>
        {!L.mobile && (
          <>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em' }}>Claude RC</div>
            <div style={{ fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textLow, letterSpacing: '.06em' }}>v2.0</div>
          </>
        )}
      </div>

      {!L.mobile && <div style={{ width: 1, height: 18, background: RT.border, marginInline: 4 }} />}

      {/* Machine selector — primary nav */}
      <MachineSelector RT={RT} L={L} openId={openId} setOpenId={setOpenId} />

      <div style={{ flex: 1 }} />

      {!L.mobile && (
        <div style={{
          background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 6,
          padding: '5px 10px', display: 'flex', alignItems: 'center', gap: 8, width: 260,
          fontFamily: window.FONT_MONO, fontSize: 11, color: RT.textLow,
        }}>
          <window.Icons.search size={11} stroke={RT.textLow} />
          <span style={{ flex: 1 }}>Search sessions, tasks…</span>
          <span style={{ padding: '0px 5px', border: `1px solid ${RT.border}`, borderRadius: 3, fontSize: 10 }}>⌘K</span>
        </div>
      )}

      <button style={btn(RT, 'icon')}><window.Icons.refresh size={12} stroke={RT.textDim} /></button>
      {!L.mobile && <button style={btn(RT, 'icon')}><window.Icons.power size={11} stroke={RT.textDim} /></button>}
    </div>
  );
}

function MachineSelector({ RT, L, openId, setOpenId }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const off = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const current = window.DEVICES.find((d) => d.id === openId);
  const label   = current ? current.name : 'All devices';
  const hueColor = current ? tintFor(current.hue, 0.70, 0.10) : RT.textDim;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen((o) => !o)} style={{
        background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
        padding: L.mobile ? '7px 11px' : '6px 11px',
        cursor: 'pointer', color: RT.text, fontFamily: 'inherit',
        display: 'inline-flex', alignItems: 'center', gap: 9,
        fontSize: 12, fontWeight: 500,
        minWidth: L.mobile ? 200 : 220,
      }}>
        {current
          ? <window.Dot color={hueColor} size={7} pulse={current.online} />
          : <div style={{ width: 14, height: 10, position: 'relative' }}>
              <div style={{ position: 'absolute', inset: 0, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1 }}>
                {window.DEVICES.slice(0, 3).map((d) => <div key={d.id} style={{ background: tintFor(d.hue, 0.68, 0.10), borderRadius: 1 }} />)}
              </div>
            </div>}
        <span style={{ flex: 1, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
        {current && <span style={{ fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textLow }}>{current.region}</span>}
        <window.Icons.chevDown size={12} stroke={RT.textDim} />
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 6,
          background: RT.panel, border: `1px solid ${RT.borderHi}`, borderRadius: 10,
          width: L.mobile ? 'calc(100vw - 28px)' : 340,
          maxWidth: 'calc(100vw - 28px)',
          padding: 6, zIndex: 30,
          boxShadow: '0 12px 36px rgba(0,0,0,.4)',
        }}>
          <DropItem RT={RT} active={openId === null} onClick={() => { setOpenId(null); setOpen(false); }}
            primary="All devices" secondary={`${window.onlineCount} online · ${window.fmtK(window.totalTokens)} total`}
            badge={window.DEVICES.length}
          />
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          {window.DEVICES.map((d) => (
            <DropItem
              key={d.id} RT={RT}
              active={openId === d.id}
              onClick={() => { setOpenId(d.id); setOpen(false); }}
              hue={d.hue} online={d.online}
              primary={d.name} secondary={`${d.hostname} · ${d.region}`}
              right={
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontFamily: window.FONT_MONO, fontSize: 11 }}>{window.fmtK(d.tokens)}</div>
                  <div style={{ fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textLow }}>{d.sessions} sess</div>
                </div>
              }
            />
          ))}
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          <DropItem RT={RT} icon={<window.Icons.plus size={13} stroke={RT.textDim} />}
            primary="Add a device…" secondary="npx claude-rc connect" muted />
        </div>
      )}
    </div>
  );
}

function DropItem({ RT, active, onClick, hue, online, primary, secondary, badge, right, icon, muted }) {
  const hueColor = hue != null ? tintFor(hue, 0.70, 0.10) : RT.textDim;
  return (
    <button onClick={onClick} style={{
      width: '100%', textAlign: 'left',
      background: active ? RT.bgRaised : 'transparent',
      border: 'none', borderRadius: 6,
      padding: '8px 9px', cursor: 'pointer', color: RT.text, fontFamily: 'inherit',
      display: 'flex', alignItems: 'center', gap: 10,
    }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = RT.bgRaised; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
    >
      <div style={{ width: 16, display: 'flex', justifyContent: 'center' }}>
        {icon || (hue != null
          ? <window.Dot color={hueColor} size={7} pulse={online} />
          : <div style={{ width: 8, height: 8, borderRadius: 2, background: RT.borderHi }} />)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: muted ? RT.textDim : RT.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{primary}</div>
        {secondary && <div style={{ fontSize: 10, fontFamily: window.FONT_MONO, color: RT.textLow, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{secondary}</div>}
      </div>
      {right}
      {badge != null && (
        <span style={{ fontSize: 10, fontFamily: window.FONT_MONO, color: RT.textDim, background: RT.bgRaised, border: `1px solid ${RT.border}`, padding: '1px 6px', borderRadius: 4 }}>{badge}</span>
      )}
    </button>
  );
}

// ─── Aggregate strip ────────────────────────────────────────────────────────
function RStrip({ RT, L }) {
  const cells = [
    { label: 'Online',   value: `${window.onlineCount}/${window.DEVICES.length}`, sub: `${window.offlineCount} offline`, dot: RT.green },
    { label: 'Sessions', value: window.totalSessions, sub: 'active' },
    { label: 'Tokens',   value: window.fmtK(window.totalTokens), sub: `of ${window.fmtK(window.totalTokenCap)}` },
    { label: 'Load',     value: `${Math.round((window.totalTokens / window.totalTokenCap) * 100)}%`, bar: (window.totalTokens / window.totalTokenCap) * 100 },
  ];
  return (
    <div style={{
      flex: 'none', borderBottom: `1px solid ${RT.border}`, background: RT.bg,
      display: 'grid', gridTemplateColumns: `repeat(${cells.length}, 1fr)`,
      padding: '12px 0',
    }}>
      {cells.map((c, i) => (
        <div key={c.label} style={{ padding: '0 22px', borderLeft: i === 0 ? 'none' : `1px solid ${RT.border}` }}>
          <div style={{ fontSize: 9, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 6 }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
            {c.dot && <window.Dot color={c.dot} size={6} pulse />}
            <div style={{ fontSize: 20, fontWeight: 500, letterSpacing: '-.02em', fontFamily: window.FONT_MONO, lineHeight: 1 }}>{c.value}</div>
            {c.sub && <div style={{ fontSize: 10, color: RT.textLow, fontFamily: window.FONT_MONO }}>{c.sub}</div>}
          </div>
          {c.bar != null && <div style={{ marginTop: 7 }}><window.CapBar pct={c.bar} height={3} bg="rgba(255,255,255,.04)" color={RT.accent} /></div>}
        </div>
      ))}
    </div>
  );
}

// ─── Grid ──────────────────────────────────────────────────────────────────
function RGrid({ RT, L, onOpen, openId }) {
  const cols = L.mobile ? 1 : L.tablet ? 2 : 3;
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: L.mobile ? 14 : 18 }}>
      {/* Mobile mini-strip (since full strip is hidden) */}
      {L.mobile && <RMobileStrip RT={RT} />}

      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 10 }}>
        <div style={{ fontSize: 10, color: RT.textDim, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO }}>Devices · {window.DEVICES.length}</div>
        {!L.mobile && <div style={{ fontSize: 10, color: RT.textLow, fontFamily: window.FONT_MONO }}>sorted by activity</div>}
        <div style={{ flex: 1 }} />
        <button style={btn(RT, 'tinyText')}>{L.mobile ? '+' : '+ Add'}</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: L.mobile ? 10 : 12 }}>
        {window.DEVICES.map((d) => (
          <RDeviceCard key={d.id} RT={RT} L={L} d={d} active={openId === d.id} onOpen={() => onOpen(d.id)} />
        ))}
      </div>
    </div>
  );
}

function RMobileStrip({ RT }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 14,
      background: RT.card, border: `1px solid ${RT.border}`, borderRadius: 10, padding: 12,
    }}>
      {[
        { label: 'Online',  value: `${window.onlineCount}/${window.DEVICES.length}`, dot: RT.green },
        { label: 'Sessns',  value: window.totalSessions },
        { label: 'Tokens',  value: window.fmtK(window.totalTokens) },
        { label: 'Load',    value: `${Math.round((window.totalTokens / window.totalTokenCap) * 100)}%` },
      ].map((c) => (
        <div key={c.label}>
          <div style={{ fontSize: 8, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 3 }}>
            {c.dot && <window.Dot color={c.dot} size={5} pulse />}
            <div style={{ fontFamily: window.FONT_MONO, fontSize: 14, fontWeight: 500 }}>{c.value}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function RDeviceCard({ RT, L, d, active, onOpen }) {
  const KindIcon = window.Icons[d.kind] || window.Icons.server;
  const hueColor = tintFor(d.hue, 0.66, 0.08);
  const hueAccent = tintFor(d.hue, 0.74, 0.11);
  const capPct = (d.tokens / d.tokenCap) * 100;
  const [hover, setHover] = useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onClick={onOpen}
      style={{
        cursor: 'pointer',
        background: active ? RT.cardHi : RT.card,
        border: `1px solid ${active ? tintEdge(d.hue) : (hover ? RT.borderHi : RT.border)}`,
        borderRadius: 10, padding: L.mobile ? '14px 14px 12px' : '13px 14px 12px',
        position: 'relative', overflow: 'hidden',
        display: 'flex', flexDirection: 'column', gap: 10,
        transition: 'border-color .12s, background .12s',
        opacity: d.online ? 1 : 0.65,
      }}>
      {/* Subtle left edge accent */}
      <div style={{
        position: 'absolute', top: 10, bottom: 10, left: 0, width: 2,
        background: hueColor, borderRadius: 2, opacity: active ? 1 : 0.6,
      }} />

      {/* Row 1 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 28, height: 28, borderRadius: 7, flex: 'none',
          background: tintSoft(d.hue), color: hueAccent,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={14} stroke={hueAccent} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.name}</div>
            <window.Dot color={d.online ? RT.green : RT.textLow} size={6} pulse={d.online} />
          </div>
          <div style={{ fontSize: 10, color: RT.textLow, fontFamily: window.FONT_MONO, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 2 }}>{d.hostname}</div>
        </div>
      </div>

      {/* Meta */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textDim, flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
          <window.Icons.globe size={9} stroke={RT.textLow} /> {d.region}
        </span>
        <span style={{ color: RT.textLow }}>·</span>
        <span>{d.lastActivity}</span>
      </div>

      {/* Sparkline + tokens */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10 }}>
        <div style={{ flex: 1, color: hueColor }}>
          <window.Sparkline data={d.spark} w={200} h={28} color={hueColor} fillOpacity={0.10} dotEnd />
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: window.FONT_MONO, fontSize: 14, fontWeight: 500, letterSpacing: '-.01em' }}>{window.fmtK(d.tokens)}</div>
          <div style={{ fontSize: 9, color: RT.textLow, fontFamily: window.FONT_MONO, letterSpacing: '.06em' }}>TOKENS</div>
        </div>
      </div>

      {/* Cap + sessions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <window.CapBar pct={capPct} height={3} bg="rgba(255,255,255,.04)" color={hueColor} />
        <div style={{ fontSize: 10, fontFamily: window.FONT_MONO, color: RT.textDim, whiteSpace: 'nowrap' }}>
          {Math.round(capPct)}% · {d.sessions} sess
        </div>
      </div>
    </div>
  );
}

// ─── Side panel (desktop) ──────────────────────────────────────────────────
function RSidePanel({ RT, device, onClose, tab, setTab, L }) {
  const hueColor = tintFor(device.hue, 0.70, 0.10);
  const sessions = window.SESSIONS[device.id] || [];
  const scheduled = window.SCHEDULED.filter((s) => s.device === device.id);
  return (
    <div style={{
      flex: 'none', width: L.tablet ? 360 : 420,
      borderLeft: `1px solid ${RT.border}`, background: RT.bgRaised,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <RPanelHeader RT={RT} device={device} hueColor={hueColor} onClose={onClose} />
      <RMiniLauncher RT={RT} device={device} />
      <RPanelTabs RT={RT} tab={tab} setTab={setTab} sessions={sessions} scheduled={scheduled} />
      <RPanelBody RT={RT} device={device} tab={tab} sessions={sessions} scheduled={scheduled} />
    </div>
  );
}

// ─── Mobile detail (full-screen) ───────────────────────────────────────────
function RMobileDetail({ RT, device, onClose, tab, setTab }) {
  const hueColor = tintFor(device.hue, 0.70, 0.10);
  const sessions = window.SESSIONS[device.id] || [];
  const scheduled = window.SCHEDULED.filter((s) => s.device === device.id);
  return (
    <div style={{
      position: 'absolute', inset: 0, background: RT.bg,
      display: 'flex', flexDirection: 'column', zIndex: 5,
    }}>
      <div style={{ flex: 'none', padding: '12px 14px', borderBottom: `1px solid ${RT.border}`, background: RT.bgRaised, display: 'flex', alignItems: 'center', gap: 10 }}>
        <button onClick={onClose} style={{ ...btn(RT, 'icon'), width: 30, height: 30 }}>
          <window.Icons.back size={14} stroke={RT.textDim} />
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <window.Dot color={device.online ? RT.green : RT.textLow} size={7} pulse={device.online} />
            <div style={{ fontSize: 14, fontWeight: 600 }}>{device.name}</div>
          </div>
          <div style={{ fontSize: 11, color: RT.textLow, fontFamily: window.FONT_MONO, marginTop: 1 }}>{device.hostname} · {device.region}</div>
        </div>
        <button style={btn(RT, 'icon')}><window.Icons.more size={14} stroke={RT.textDim} /></button>
      </div>

      <RMiniLauncher RT={RT} device={device} mobile />
      <RPanelTabs RT={RT} tab={tab} setTab={setTab} sessions={sessions} scheduled={scheduled} />
      <RPanelBody RT={RT} device={device} tab={tab} sessions={sessions} scheduled={scheduled} />
    </div>
  );
}

function RPanelHeader({ RT, device, hueColor, onClose }) {
  return (
    <div style={{ flex: 'none', padding: '14px 16px', borderBottom: `1px solid ${RT.border}`, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8, flex: 'none',
        background: tintSoft(device.hue), color: hueColor,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {React.createElement(window.Icons[device.kind] || window.Icons.server, { size: 16, stroke: hueColor })}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.005em' }}>{device.name}</div>
          <window.Dot color={device.online ? RT.green : RT.textLow} size={6} pulse={device.online} />
        </div>
        <div style={{ fontSize: 10, color: RT.textLow, fontFamily: window.FONT_MONO, marginTop: 2 }}>
          {device.hostname} · {device.region}
        </div>
      </div>
      <button onClick={onClose} style={{ ...btn(RT, 'icon'), width: 24, height: 24, color: RT.textDim, fontSize: 12 }}>✕</button>
    </div>
  );
}

function RMiniLauncher({ RT, device, mobile }) {
  return (
    <div style={{ flex: 'none', padding: '12px 14px', borderBottom: `1px solid ${RT.border}`, background: RT.bg }}>
      <div style={{ fontSize: 9, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: window.FONT_MONO, marginBottom: 8 }}>
        Launch on {device.name}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: mobile ? 'wrap' : 'nowrap' }}>
        <input defaultValue="/var/www/rc-launcher" style={{
          flex: 1, minWidth: mobile ? '100%' : 0,
          background: RT.panel, color: RT.text,
          border: `1px solid ${RT.border}`, borderRadius: 6,
          padding: '7px 9px', fontFamily: window.FONT_MONO, fontSize: 11, outline: 'none',
        }} />
        <select style={{
          background: RT.panel, color: RT.text,
          border: `1px solid ${RT.border}`, borderRadius: 6,
          padding: '7px 9px', fontFamily: window.FONT_MONO, fontSize: 11, outline: 'none',
        }}>
          <option>STANDARD</option><option>TEAMMATE</option><option>SAFE</option>
        </select>
        <button style={{
          background: RT.text, color: RT.bg, border: 'none',
          padding: '7px 13px', borderRadius: 6, cursor: 'pointer',
          fontFamily: 'inherit', fontSize: 11, fontWeight: 600,
          display: 'inline-flex', alignItems: 'center', gap: 5,
          flex: mobile ? 1 : 'none', justifyContent: 'center',
        }}>
          <window.Icons.play size={10} stroke={RT.bg} /> Launch
        </button>
      </div>
    </div>
  );
}

function RPanelTabs({ RT, tab, setTab, sessions, scheduled }) {
  return (
    <div style={{ flex: 'none', display: 'flex', borderBottom: `1px solid ${RT.border}`, padding: '0 12px' }}>
      {[
        ['running',   'Sessions',  sessions.length],
        ['scheduled', 'Scheduled', scheduled.length],
        ['logs',      'Logs',      null],
      ].map(([id, label, count]) => (
        <button key={id} onClick={() => setTab(id)} style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          padding: '11px 12px',
          color: tab === id ? RT.text : RT.textLow,
          borderBottom: `2px solid ${tab === id ? RT.accent : 'transparent'}`,
          fontFamily: window.FONT_MONO, fontSize: 11, fontWeight: 500,
          letterSpacing: '.04em', textTransform: 'uppercase',
        }}>
          {label}{count != null && <span style={{ color: RT.textLow, marginLeft: 4 }}>{count}</span>}
        </button>
      ))}
    </div>
  );
}

function RPanelBody({ RT, device, tab, sessions, scheduled }) {
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '10px 12px' }}>
      {tab === 'running' && (
        sessions.length === 0
          ? <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>{device.online ? 'No active sessions.' : 'Device offline.'}</div>
          : <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {sessions.map((s) => <RSessionRow key={s.sessionId} RT={RT} hue={device.hue} s={s} />)}
            </div>
      )}
      {tab === 'scheduled' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {scheduled.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>No scheduled tasks.</div>
            : scheduled.map((s) => <RScheduledRow key={s.id} RT={RT} hue={device.hue} s={s} />)}
        </div>
      )}
      {tab === 'logs' && (
        <pre style={{ margin: 0, fontFamily: window.FONT_MONO, fontSize: 11, color: RT.textDim, lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
{`[12:04:18] ${device.name} heartbeat ok · cpu=${Math.round(device.cpuLoad * 100)}%
[12:04:09] session.tick · ${device.sessions} active
[12:03:55] tokens=${window.fmtK(device.tokens)} (${Math.round(device.tokens / device.tokenCap * 100)}%)
[12:03:32] reconnect ok · last_lag=312ms
[12:03:01] daemon healthy
[12:00:00] daily token reset`}
        </pre>
      )}
    </div>
  );
}

function RSessionRow({ RT, hue, s }) {
  const hueColor = tintFor(hue, 0.66, 0.08);
  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`,
      borderRadius: 8, padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 600, flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
        <RStatusPill RT={RT} status={s.status} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, fontFamily: window.FONT_MONO, color: RT.textDim }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
          <window.Icons.folder size={9} stroke={RT.textLow} /> {s.dir}
        </span>
        <window.CapBar pct={s.pct} height={2} bg="rgba(255,255,255,.04)" color={hueColor} />
        <span style={{ whiteSpace: 'nowrap', color: RT.textDim }}>{s.tokensK}K</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ flex: 1, fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textLow, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {s.sessionId.slice(0, 28)}…
        </div>
        <button style={btn(RT, 'mini')}><window.Icons.copy size={10} stroke={RT.textDim} /></button>
        <button style={btn(RT, 'mini')}><window.Icons.link size={10} stroke={RT.textDim} /></button>
        <button style={btn(RT, 'mini')}><window.Icons.refresh size={10} stroke={RT.green} /></button>
        <button style={btn(RT, 'mini')}><window.Icons.stop size={9} stroke={RT.red} /></button>
      </div>
    </div>
  );
}

function RScheduledRow({ RT, hue, s }) {
  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`,
      borderRadius: 8, padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{s.name}</div>
        <span style={{
          fontSize: 9, fontFamily: window.FONT_MONO, letterSpacing: '.06em', textTransform: 'uppercase',
          color: s.enabled ? RT.green : RT.textLow,
          padding: '1px 6px', borderRadius: 3,
          background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
        }}>{s.enabled ? 'ENABLED' : 'PAUSED'}</span>
      </div>
      <div style={{ fontSize: 10, fontFamily: window.FONT_MONO, color: RT.textDim, display: 'flex', alignItems: 'center', gap: 6 }}>
        <window.Icons.clock size={9} stroke={RT.textLow} /> {s.cron}
        <span style={{ color: RT.textLow }}>({s.schedule})</span>
      </div>
    </div>
  );
}

function RStatusPill({ RT, status }) {
  const m = ({
    running:  { label: 'running',  color: RT.green,  pulse: true },
    thinking: { label: 'thinking', color: RT.amber,  pulse: true },
    idle:     { label: 'idle',     color: RT.textLow, pulse: false },
    stopped:  { label: 'stopped',  color: RT.red,    pulse: false },
  })[status] || { label: status, color: RT.textLow, pulse: false };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 9, color: m.color, letterSpacing: '.06em', textTransform: 'uppercase', fontFamily: window.FONT_MONO }}>
      <window.Dot color={m.color} size={5} pulse={m.pulse} />
      {m.label}
    </span>
  );
}

// ─── Footer ────────────────────────────────────────────────────────────────
function RFooter({ RT }) {
  return (
    <div style={{
      flex: 'none', height: 28, borderTop: `1px solid ${RT.border}`,
      background: RT.bgRaised, display: 'flex', alignItems: 'center',
      padding: '0 18px', gap: 14,
      fontFamily: window.FONT_MONO, fontSize: 10, color: RT.textLow,
      letterSpacing: '.04em',
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: RT.green }}>
        <window.Dot color={RT.green} size={5} pulse /> connected
      </span>
      <span>relay.claude-rc.io</span>
      <span>last sync 12s ago</span>
      <div style={{ flex: 1 }} />
      <span>v2.0.0</span>
    </div>
  );
}

// ─── Button helper ─────────────────────────────────────────────────────────
function btn(RT, kind) {
  const base = {
    fontFamily: 'inherit', cursor: 'pointer',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  };
  if (kind === 'icon') return {
    ...base, background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 6,
    width: 26, height: 26, padding: 0, color: RT.textDim,
  };
  if (kind === 'mini') return {
    ...base, background: 'transparent', color: RT.textDim,
    border: `1px solid ${RT.border}`, borderRadius: 4,
    width: 22, height: 22, padding: 0,
  };
  if (kind === 'tinyText') return {
    ...base, background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 6,
    padding: '5px 10px', color: RT.text, fontSize: 11, fontWeight: 500, gap: 5,
  };
  return base;
}

Object.assign(window, { OpsRefined, OpsRefinedMobileDemo: () => <OpsRefined initialOpenId="gpu" /> });
