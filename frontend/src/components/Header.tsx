// Header.tsx — RHeader + MachineSelector + DropItem.
// Ported from variant-ops-refined.jsx lines 82-237.
import { useState, useEffect, useRef } from 'react';
import type { DeviceCard } from '../types';
import type { Layout } from '../useLayout';
import { RT, FONT_MONO, FONT_SANS, tintFor, hueForId, fmtK } from '../tokens';
import { Dot, Icons } from './primitives';
import { btn } from './btn';
import { api } from '../api';
import { ShareTunnel } from './ShareTunnel';

// ─── Props ───────────────────────────────────────────────────────────────────

interface HeaderProps {
  cards: DeviceCard[];
  openId: string | null;
  setOpenId: (id: string | null) => void;
  onlineCount: number;
  totalTokens: number;
  layout: Layout;
  onRefresh: () => void;
}

interface MachineSelectorProps {
  cards: DeviceCard[];
  openId: string | null;
  setOpenId: (id: string | null) => void;
  layout: Layout;
}

interface DropItemProps {
  active?: boolean;
  onClick?: () => void;
  hue?: number;
  online?: boolean;
  primary: string;
  secondary?: string;
  badge?: number;
  right?: React.ReactNode;
  icon?: React.ReactNode;
  muted?: boolean;
}

// ─── DropItem ────────────────────────────────────────────────────────────────

function DropItem({ active, onClick, hue, online, primary, secondary, badge, right, icon, muted }: DropItemProps) {
  const hueColor = hue != null ? tintFor(hue, 0.70, 0.10) : RT.textDim;
  return (
    <button
      onClick={onClick}
      style={{
        width: '100%',
        textAlign: 'left',
        background: active ? RT.bgRaised : 'transparent',
        border: 'none',
        borderRadius: 6,
        padding: '8px 9px',
        cursor: 'pointer',
        color: RT.text,
        fontFamily: FONT_SANS,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}
      onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
      onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
    >
      <div style={{ width: 16, display: 'flex', justifyContent: 'center' }}>
        {icon || (hue != null
          ? <Dot color={hueColor} size={7} pulse={online} />
          : <div style={{ width: 8, height: 8, borderRadius: 2, background: RT.borderHi }} />)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: muted ? RT.textDim : RT.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{primary}</div>
        {secondary && (
          <div style={{ fontSize: 10, fontFamily: FONT_MONO, color: RT.textLow, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{secondary}</div>
        )}
      </div>
      {right}
      {badge != null && (
        <span style={{ fontSize: 10, fontFamily: FONT_MONO, color: RT.textDim, background: RT.bgRaised, border: `1px solid ${RT.border}`, padding: '1px 6px', borderRadius: 4 }}>{badge}</span>
      )}
    </button>
  );
}

// ─── MachineSelector ────────────────────────────────────────────────────────

function MachineSelector({ cards, openId, setOpenId, layout }: MachineSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const current = cards.find((c) => c.id === openId);
  const label = current ? current.name : 'All devices';
  const hue = current ? hueForId(current.id) : undefined;
  const hueColor = hue != null ? tintFor(hue, 0.70, 0.10) : RT.textDim;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: RT.panel,
          border: `1px solid ${RT.border}`,
          borderRadius: 7,
          padding: layout.mobile ? '7px 11px' : '6px 11px',
          cursor: 'pointer',
          color: RT.text,
          fontFamily: FONT_SANS,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 9,
          fontSize: 12,
          fontWeight: 500,
          minWidth: layout.mobile ? 200 : 220,
        }}
      >
        {current
          ? <Dot color={hueColor} size={7} pulse={current.online} />
          : <div style={{ width: 14, height: 10, position: 'relative' }}>
              <div style={{ position: 'absolute', inset: 0, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1 }}>
                {cards.slice(0, 3).map((c) => (
                  <div key={c.id} style={{ background: tintFor(hueForId(c.id), 0.68, 0.10), borderRadius: 1 }} />
                ))}
              </div>
            </div>}
        <span style={{ flex: 1, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
        <Icons.chevDown size={12} stroke={RT.textDim} />
      </button>

      {open && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          marginTop: 6,
          background: RT.panel,
          border: `1px solid ${RT.borderHi}`,
          borderRadius: 10,
          width: layout.mobile ? 'calc(100vw - 28px)' : 340,
          maxWidth: 'calc(100vw - 28px)',
          padding: 6,
          zIndex: 30,
          boxShadow: '0 12px 36px rgba(0,0,0,.4)',
        }}>
          <DropItem
            active={openId === null}
            onClick={() => { setOpenId(null); setOpen(false); }}
            primary="All devices"
            secondary={`${cards.filter((c) => c.online).length} online · ${fmtK(cards.reduce((s, c) => s + c.tokens, 0))} total`}
            badge={cards.length}
          />
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          {cards.map((c) => {
            const dHue = hueForId(c.id);
            return (
              <DropItem
                key={c.id}
                active={openId === c.id}
                onClick={() => { setOpenId(c.id); setOpen(false); }}
                hue={dHue}
                online={c.online}
                primary={c.name}
                secondary={`${c.hostname || c.id}${c.os ? ' · ' + c.os : ''}`}
                right={
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontFamily: FONT_MONO, fontSize: 11 }}>{fmtK(c.tokens)}</div>
                    <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow }}>{c.sessions} sess</div>
                  </div>
                }
              />
            );
          })}
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          <DropItem
            icon={<Icons.plus size={13} stroke={RT.textDim} />}
            primary="Add a device…"
            secondary="npx claude-rc connect"
            muted
          />
        </div>
      )}
    </div>
  );
}

// ─── VersionChip ────────────────────────────────────────────────────────────

interface UpdateInfo {
  update_available: boolean;
  latest: string;
  current?: string;
}

function VersionChip() {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.updateCheck().then((data) => {
      if (!cancelled && mounted.current) setInfo(data as UpdateInfo);
    }).catch(() => {/* ignore */});
    return () => { cancelled = true; };
  }, []);

  const handleUpdate = async () => {
    setUpdating(true);
    try {
      await api.update();
    } catch {/* likely network error as server restarts — expected */}
    // Reload after 3s
    setTimeout(() => { window.location.reload(); }, 3000);
  };

  if (!info) return null;

  const vLabel = info.current ? `v${info.current}` : (info.latest ? `v${info.latest}` : null);

  if (info.update_available) {
    return (
      <button
        onClick={handleUpdate}
        disabled={updating}
        style={{
          background: 'oklch(0.72 0.09 78 / 0.15)',
          border: `1px solid oklch(0.72 0.09 78 / 0.40)`,
          borderRadius: 6,
          padding: '4px 9px',
          cursor: updating ? 'wait' : 'pointer',
          color: RT.amber,
          fontFamily: FONT_MONO,
          fontSize: 10,
          fontWeight: 600,
          whiteSpace: 'nowrap',
          opacity: updating ? 0.7 : 1,
        }}
      >
        {updating ? 'updating…' : `v${info.latest} · Update`}
      </button>
    );
  }

  return vLabel ? (
    <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow, letterSpacing: '.06em' }}>
      {vLabel}
    </span>
  ) : null;
}

// ─── GlobalMenu ─────────────────────────────────────────────────────────────

interface GlobalMenuProps {
  openId: string | null;
  onRefresh: () => void;
}

function GlobalMenu({ openId, onRefresh }: GlobalMenuProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const handleStopAll = async () => {
    if (!openId) return;
    setPending(true);
    setOpen(false);
    try {
      await api.stopAll(openId);
    } catch {/* ignore */}
    finally {
      setPending(false);
      onRefresh();
    }
  };

  const menuItemStyle: React.CSSProperties = {
    width: '100%',
    textAlign: 'left',
    background: 'transparent',
    border: 'none',
    borderRadius: 6,
    padding: '8px 10px',
    cursor: 'pointer',
    color: RT.text,
    fontFamily: FONT_SANS,
    fontSize: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 9,
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        style={{ ...btn('icon'), opacity: pending ? 0.6 : 1 }}
        onClick={() => setOpen((o) => !o)}
        title="Menu"
      >
        <Icons.more size={12} stroke={RT.textDim} />
      </button>

      {open && (
        <div style={{
          position: 'absolute',
          top: '100%',
          right: 0,
          marginTop: 6,
          background: RT.panel,
          border: `1px solid ${RT.borderHi}`,
          borderRadius: 10,
          width: 180,
          padding: 6,
          zIndex: 30,
          boxShadow: '0 12px 36px rgba(0,0,0,.4)',
        }}>
          <button
            style={{ ...menuItemStyle, opacity: openId ? 1 : 0.4, cursor: openId ? 'pointer' : 'default' }}
            onClick={openId ? handleStopAll : undefined}
            onMouseEnter={(e) => { if (openId) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
          >
            <Icons.stop size={11} stroke={RT.red} />
            Stop all
          </button>

          <button
            style={menuItemStyle}
            onClick={() => { setOpen(false); onRefresh(); }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
          >
            <Icons.refresh size={11} stroke={RT.textDim} />
            Refresh
          </button>

          <div style={{ height: 1, background: RT.border, margin: '4px 0' }} />

          <button
            style={{ ...menuItemStyle, color: RT.textDim }}
            onClick={() => { window.location.href = '/legacy'; }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
          >
            <Icons.link size={11} stroke={RT.textDim} />
            Classic UI
          </button>

          <button
            style={{ ...menuItemStyle, color: RT.red }}
            onClick={() => { window.location.href = '/logout'; }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
          >
            <Icons.power size={11} stroke={RT.red} />
            Logout
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Header ──────────────────────────────────────────────────────────────────

export function Header({ cards, openId, setOpenId, layout, onRefresh }: HeaderProps) {
  const [shareOpen, setShareOpen] = useState(false);

  return (
    <>
      <div style={{
        flex: 'none',
        height: layout.mobile ? 52 : 48,
        borderBottom: `1px solid ${RT.border}`,
        background: RT.bgRaised,
        display: 'flex',
        alignItems: 'center',
        padding: layout.mobile ? '0 14px' : '0 18px',
        gap: layout.mobile ? 10 : 14,
      }}>
        {/* Mark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <div style={{
            width: 22,
            height: 22,
            borderRadius: 5,
            border: `1px solid ${RT.borderHi}`,
            background: RT.panel,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: FONT_MONO,
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '.02em',
            color: RT.text,
          }}>rc</div>
          {!layout.mobile && (
            <>
              <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em' }}>Claude RC</div>
              <VersionChip />
            </>
          )}
        </div>

        {!layout.mobile && <div style={{ width: 1, height: 18, background: RT.border, marginInline: 4 }} />}

        {/* Machine selector — primary nav */}
        <MachineSelector cards={cards} openId={openId} setOpenId={setOpenId} layout={layout} />

        <div style={{ flex: 1 }} />

        {!layout.mobile && (
          <div style={{
            background: RT.panel,
            border: `1px solid ${RT.border}`,
            borderRadius: 6,
            padding: '5px 10px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            width: 260,
            fontFamily: FONT_MONO,
            fontSize: 11,
            color: RT.textLow,
          }}>
            <Icons.search size={11} stroke={RT.textLow} />
            <span style={{ flex: 1 }}>Search sessions, tasks…</span>
            <span style={{ padding: '0px 5px', border: `1px solid ${RT.border}`, borderRadius: 3, fontSize: 10 }}>⌘K</span>
          </div>
        )}

        {/* Share tunnel button */}
        <button
          style={btn('icon')}
          title="Share tunnel"
          onClick={() => setShareOpen(true)}
        >
          <Icons.share size={12} stroke={RT.textDim} />
        </button>

        {/* Global ⋯ menu (stop all, refresh, logout, classic UI) */}
        {!layout.mobile && <GlobalMenu openId={openId} onRefresh={onRefresh} />}
      </div>

      {shareOpen && <ShareTunnel onClose={() => setShareOpen(false)} />}
    </>
  );
}
