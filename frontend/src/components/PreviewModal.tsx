import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { RT, FONT_MONO, FONT_SANS, Z } from '../tokens';
import { Icons } from './primitives';
import { api } from '../api';

interface PreviewModalProps {
  deviceId: string;
  name: string;
  onClose: () => void;
}

// Map common JS keys to tmux send-keys names.
const SPECIAL_KEY_MAP: Record<string, string> = {
  'Enter': 'Enter',
  'Backspace': 'BSpace',
  'Tab': 'Tab',
  'Escape': 'Escape',
  'ArrowUp': 'Up',
  'ArrowDown': 'Down',
  'ArrowLeft': 'Left',
  'ArrowRight': 'Right',
  'Home': 'Home',
  'End': 'End',
  'PageUp': 'PageUp',
  'PageDown': 'PageDown',
  'Delete': 'DC',
};

export function PreviewModal({ deviceId, name, onClose }: PreviewModalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const mounted = useRef(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [status, setStatus] = useState<string>('connecting…');
  const lastContentRef = useRef<string>('');
  // Stable viewer id — the server sizes the tmux window to the minimum
  // across live viewers, so web + mobile can watch the same session.
  const viewerIdRef = useRef<string>(
    typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2),
  );
  const sizeRef = useRef<{ cols: number; rows: number }>({ cols: 0, rows: 0 });

  useEffect(() => () => { mounted.current = false; }, []);

  // Initialize xterm
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      cursorBlink: false,
      fontFamily: '"Geist Mono", ui-monospace, SFMono-Regular, monospace',
      fontSize: 12,
      lineHeight: 1.2,
      theme: {
        background: '#1a1a18',
        foreground: '#e8e7e3',
        cursor: '#e8e7e3',
        selectionBackground: 'rgba(150,150,150,.3)',
      },
      scrollback: 10000,
      convertEol: true,
      disableStdin: false,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    try { fit.fit(); } catch { /* ignore early-fit failures */ }
    termRef.current = term;
    fitRef.current = fit;

    // Track the browser terminal's real cols/rows; every /preview poll
    // reports them, and the server applies the min across live viewers.
    const syncSize = () => {
      try {
        fit.fit();
        if (term.cols >= 40 && term.rows >= 10) {
          sizeRef.current = { cols: term.cols, rows: term.rows };
        }
      } catch { /* ignore */ }
    };
    syncSize();
    // Re-fit once webfonts load — cell metrics measured against a fallback
    // font are wrong, which shows up as overlapping glyphs.
    document.fonts?.ready?.then(() => { if (mounted.current) syncSize(); });

    // Forward keystrokes (printable text) to the session.
    term.onData((data) => {
      if (!mounted.current) return;
      api.sendKeys(deviceId, name, { keys: data }).catch(() => { /* ignore */ });
    });

    // Forward known special keys.
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true; // ignore keyup
      const key = ev.key;
      // Ctrl combinations: send as e.g. "C-c", "C-d", "C-l".
      if (ev.ctrlKey && key.length === 1 && /[a-zA-Z]/.test(key)) {
        api.sendKeys(deviceId, name, { special: [`C-${key.toLowerCase()}`] }).catch(() => {});
        return false; // prevent xterm from also sending the char
      }
      const mapped = SPECIAL_KEY_MAP[key];
      if (mapped) {
        api.sendKeys(deviceId, name, { special: [mapped] }).catch(() => {});
        return false; // we handled it
      }
      return true; // let onData handle printable
    });

    // Resize handler — refit and re-sync tmux size when the window resizes.
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(syncSize, 250);
    };
    window.addEventListener('resize', onResize);

    // Touch-scroll handler — xterm.js doesn't translate touch swipes into
    // viewport scrolling on iOS / Android. We capture touchmove on the
    // container and translate the vertical delta into term.scrollLines.
    const el = containerRef.current;
    let lastY = 0;
    let active = false;
    let accumulator = 0;
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      active = true;
      lastY = e.touches[0].clientY;
      accumulator = 0;
    };
    const onTouchMove = (e: TouchEvent) => {
      if (!active || e.touches.length !== 1 || !termRef.current) return;
      const y = e.touches[0].clientY;
      const dy = lastY - y;        // positive: finger moved up → scroll down
      lastY = y;
      const rows = Math.max(1, termRef.current.rows);
      const lineHeight = el!.clientHeight / rows;
      accumulator += dy / lineHeight;
      const whole = Math.trunc(accumulator);
      if (whole !== 0) {
        termRef.current.scrollLines(whole);
        accumulator -= whole;
        if (e.cancelable) e.preventDefault();
      }
    };
    const onTouchEnd = () => { active = false; };
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove',  onTouchMove,  { passive: false });
    el.addEventListener('touchend',   onTouchEnd,   { passive: true });
    el.addEventListener('touchcancel',onTouchEnd,   { passive: true });

    return () => {
      window.removeEventListener('resize', onResize);
      clearTimeout(resizeTimer);
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove',  onTouchMove);
      el.removeEventListener('touchend',   onTouchEnd);
      el.removeEventListener('touchcancel',onTouchEnd);
      // Tell the server this viewer is gone — it restores 200×50 when the
      // last viewer leaves (background parsing expects the wide layout).
      api.previewBye(deviceId, name, viewerIdRef.current).catch(() => { /* ignore */ });
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [deviceId, name]);

  // Polling loop: refresh pane content
  useEffect(() => {
    if (!autoRefresh) return;
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const { cols, rows } = sizeRef.current;
        const data = await api.preview(
          deviceId, name,
          cols >= 40 && rows >= 10 ? { viewer: viewerIdRef.current, cols, rows } : undefined,
        );
        if (cancelled || !mounted.current) return;
        const output: string = data?.output ?? '';
        const cursor = data?.cursor as { x: number; y: number; visible: boolean } | null;
        const term = termRef.current;
        if (output !== lastContentRef.current && term) {
          // Pause re-rendering while the user has scrolled back, so the view
          // doesn't snap to the bottom every poll. Once they scroll back down,
          // the next poll re-renders normally.
          const atBottom = term.buffer.active.viewportY >= term.buffer.active.length - term.rows;
          if (atBottom) {
            lastContentRef.current = output;
            term.reset();
            // capture-pane terminates the last row with \n — writing it as-is
            // creates a phantom empty line that shifts the screen (and our
            // cursor placement) up by one row.
            term.write(output.endsWith('\n') ? output.slice(0, -1) : output);
            term.scrollToBottom();
            // Place the terminal cursor where tmux's real cursor is —
            // otherwise it just trails the last written character.
            if (cursor?.visible) {
              term.write(`\x1b[${cursor.y + 1};${cursor.x + 1}H\x1b[?25h`);
            } else {
              term.write('\x1b[?25l');
            }
            if (mounted.current) setStatus('live');
          } else {
            if (mounted.current) setStatus('paused (scrolled)');
          }
        } else if (mounted.current) {
          setStatus('live');
        }
      } catch {
        if (mounted.current) setStatus('reconnecting…');
      }
    };
    fetchOnce();
    const id = setInterval(fetchOnce, 700);
    return () => { cancelled = true; clearInterval(id); };
  }, [autoRefresh, deviceId, name]);

  // Esc closes
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Fullscreen on small screens — every row of terminal space counts.
  const fullscreen = typeof window !== 'undefined' && window.innerWidth < 700;

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: Z.modal, padding: fullscreen ? 0 : 16,
      fontFamily: FONT_SANS,
    }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: RT.panel,
          border: fullscreen ? 'none' : `1px solid ${RT.borderHi}`,
          borderRadius: fullscreen ? 0 : 12,
          width: '100%', maxWidth: fullscreen ? '100%' : 1000,
          height: fullscreen ? '100dvh' : '85vh',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,.5)',
        }}
      >
        <div style={{
          flex: 'none', padding: '12px 14px', borderBottom: `1px solid ${RT.border}`,
          background: RT.bgRaised, display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <Icons.terminal size={14} stroke={RT.textDim} />
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em' }}>{name}</div>
          <div style={{
            fontSize: 10, fontFamily: FONT_MONO, color: RT.textLow,
            letterSpacing: '.06em', textTransform: 'uppercase',
          }}>{status}</div>
          <div style={{ flex: 1 }} />
          <label style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 11, color: RT.textDim, fontFamily: FONT_MONO, cursor: 'pointer',
          }}>
            <input
              type="checkbox" checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              style={{ accentColor: RT.accent }}
            />
            auto
          </label>
          <button onClick={onClose} title="Close (Esc)" style={{
            background: 'transparent', border: 'none', color: RT.textDim,
            fontSize: 16, cursor: 'pointer', padding: '4px 8px',
          }}>✕</button>
        </div>
        <div style={{ flex: 1, position: 'relative', minHeight: 0 }}>
          <div
            ref={containerRef}
            style={{
              position: 'absolute', inset: 0,
              padding: 10, background: '#1a1a18', overflow: 'hidden',
              touchAction: 'pan-y',
            }}
          />
          {status === 'paused (scrolled)' && (
            <button
              onClick={() => { termRef.current?.scrollToBottom(); }}
              style={{
                position: 'absolute', right: 14, bottom: 14, zIndex: Z.raised,
                background: RT.green, color: RT.bg,
                border: 'none', borderRadius: 999,
                padding: '8px 14px', fontFamily: FONT_MONO, fontSize: 11,
                fontWeight: 600, letterSpacing: '.06em', textTransform: 'uppercase',
                boxShadow: '0 8px 20px rgba(0,0,0,.45)',
                cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                touchAction: 'manipulation',
              }}
            >
              ↓ Jump to live
            </button>
          )}
        </div>
        <KeyBar deviceId={deviceId} name={name} />
        <div style={{
          flex: 'none', padding: '6px 14px', borderTop: `1px solid ${RT.border}`,
          background: RT.bgRaised, display: 'flex', alignItems: 'center', gap: 10,
          fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow,
          letterSpacing: '.04em',
        }}>
          <span>tap the keys above or type on a keyboard</span>
          <div style={{ flex: 1 }} />
          <span>{deviceId}</span>
        </div>
      </div>
    </div>
  );
}

// On-screen key bar for mobile — taps map to tmux send-keys specials.
function KeyBar({ deviceId, name }: { deviceId: string; name: string }) {
  const send = (special: string) => {
    api.sendKeys(deviceId, name, { special: [special] }).catch(() => { /* ignore */ });
  };
  const keys: { label: string; special: string; flex?: number; accent?: string }[] = [
    { label: 'Esc', special: 'Escape' },
    { label: 'Tab', special: 'Tab' },
    { label: '←',   special: 'Left' },
    { label: '↓',   special: 'Down' },
    { label: '↑',   special: 'Up' },
    { label: '→',   special: 'Right' },
    { label: '⏎',   special: 'Enter', flex: 2, accent: RT.green },
  ];
  return (
    <div style={{
      flex: 'none',
      borderTop: `1px solid ${RT.border}`,
      background: RT.bg,
      padding: '8px 8px',
      display: 'flex', gap: 6, overflowX: 'auto', WebkitOverflowScrolling: 'touch',
    }}>
      {keys.map((k) => (
        <button
          key={k.label}
          onClick={() => send(k.special)}
          // Prevent stealing focus from the terminal on tap.
          onMouseDown={(e) => e.preventDefault()}
          style={{
            flex: k.flex ?? 1, minWidth: 44, minHeight: 38,
            background: RT.panel,
            color: k.accent ?? RT.text,
            border: `1px solid ${RT.border}`,
            borderRadius: 8,
            fontFamily: FONT_MONO, fontSize: 14, fontWeight: 500,
            cursor: 'pointer', padding: '0 10px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            whiteSpace: 'nowrap',
            touchAction: 'manipulation',
          }}
        >
          {k.label}
        </button>
      ))}
    </div>
  );
}
