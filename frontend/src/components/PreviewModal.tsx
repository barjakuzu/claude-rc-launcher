import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { RT, FONT_MONO, FONT_SANS, Z } from '../tokens';
import { Icons } from './primitives';
import { api } from '../api';
import { TranscriptView } from './TranscriptView';

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
  const [showHistory, setShowHistory] = useState(false);
  const [status, setStatus] = useState<string>('connecting…');
  const lastContentRef = useRef<string>('');
  // Stable viewer id — the server sizes the tmux window to the minimum
  // across live viewers, so web + mobile can watch the same session.
  const viewerIdRef = useRef<string>(
    typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2),
  );
  const sizeRef = useRef<{ cols: number; rows: number }>({ cols: 0, rows: 0 });
  // Set when the user types here; the next poll reports active=1 so this
  // viewer's size wins (most-recently-active viewer controls the window).
  const activityRef = useRef(false);
  // Timestamp of the last wheel/touch scroll gesture. While Claude streams,
  // output changes every poll and the re-render snaps to the bottom — this
  // guard pauses rendering the moment the user starts scrolling so they can
  // actually reach old messages.
  const scrollIntentRef = useRef(0);
  // Alternate-screen flag from /preview. Claude Code runs in the alternate
  // screen with ZERO tmux history — there is nothing to scroll in the
  // browser buffer. Scrolling must be forwarded to the app as PgUp/PgDn.
  const altRef = useRef(false);
  // Immediate refresh hook (set by the polling effect) — called right after
  // keystrokes land so the echo shows up without waiting for the next tick.
  const refreshRef = useRef<() => void>(() => {});
  // Typed-character batch: coalesce a typing burst into one request instead
  // of one HTTP round-trip per keystroke.
  const keyBufRef = useRef('');
  const keyTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

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

    // Flush the typed-character batch as a single request, then refresh
    // right away so the echo appears without waiting for the next poll.
    const flushKeys = (): Promise<unknown> => {
      clearTimeout(keyTimerRef.current);
      const buf = keyBufRef.current;
      keyBufRef.current = '';
      if (!buf) return Promise.resolve();
      return api.sendKeys(deviceId, name, { keys: buf })
        .then(() => refreshRef.current())
        .catch(() => { /* ignore */ });
    };

    // Special keys flush pending text first so ordering is preserved
    // (typed chars land before the Enter that submits them).
    const sendSpecial = (special: string) => {
      activityRef.current = true;
      flushKeys()
        .then(() => api.sendKeys(deviceId, name, { special: [special] }))
        .then(() => refreshRef.current())
        .catch(() => { /* ignore */ });
    };

    // Forward keystrokes (printable text) to the session, batched ~40ms.
    term.onData((data) => {
      if (!mounted.current) return;
      activityRef.current = true;
      keyBufRef.current += data;
      clearTimeout(keyTimerRef.current);
      keyTimerRef.current = setTimeout(flushKeys, 40);
    });

    // Forward known special keys.
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true; // ignore keyup
      const key = ev.key;
      // Ctrl combinations: send as e.g. "C-c", "C-d", "C-l".
      if (ev.ctrlKey && key.length === 1 && /[a-zA-Z]/.test(key)) {
        sendSpecial(`C-${key.toLowerCase()}`);
        return false; // prevent xterm from also sending the char
      }
      const mapped = SPECIAL_KEY_MAP[key];
      if (mapped) {
        sendSpecial(mapped);
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
    // Scroll forwarding. Claude Code lives in the alternate screen with no
    // tmux history — the transcript can only be scrolled by the app itself,
    // so wheel/touch gestures become PgUp/PgDn keys. Non-alt content (plain
    // shells) keeps native browser-buffer scrolling.
    let pagePx = 0;
    let lastPageAt = 0;
    const forwardScroll = (deltaPx: number) => {
      pagePx += deltaPx;
      const now = Date.now();
      const THRESHOLD = 70;   // px of gesture per page key
      const MIN_GAP = 130;    // ms between page keys
      if (Math.abs(pagePx) >= THRESHOLD && now - lastPageAt >= MIN_GAP) {
        const dir = pagePx < 0 ? 'PPage' : 'NPage';
        pagePx = 0;
        lastPageAt = now;
        api.sendKeys(deviceId, name, { special: [dir] })
          .then(() => refreshRef.current())
          .catch(() => { /* ignore */ });
      }
    };

    const onWheel = (e: WheelEvent) => {
      if (altRef.current) { forwardScroll(e.deltaY); return; }
      scrollIntentRef.current = Date.now();
    };
    el.addEventListener('wheel', onWheel, { passive: true });

    const onTouchMove = (e: TouchEvent) => {
      if (!active || e.touches.length !== 1 || !termRef.current) return;
      const y = e.touches[0].clientY;
      const dy = lastY - y;        // positive: finger moved up → scroll down
      lastY = y;
      if (altRef.current) {
        forwardScroll(dy);
        if (e.cancelable) e.preventDefault();
        return;
      }
      scrollIntentRef.current = Date.now();
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
      el.removeEventListener('wheel', onWheel);
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
        const active = activityRef.current;
        activityRef.current = false;
        const data = await api.preview(
          deviceId, name,
          cols >= 40 && rows >= 10
            ? { viewer: viewerIdRef.current, cols, rows, active }
            : undefined,
        );
        if (cancelled || !mounted.current) return;
        const output: string = data?.output ?? '';
        const cursor = data?.cursor as { x: number; y: number; visible: boolean } | null;
        altRef.current = !!data?.alt;
        const term = termRef.current;
        if (output !== lastContentRef.current && term) {
          // Alt-screen apps (Claude Code) scroll inside the app — always
          // render. For normal content, pause re-rendering while the user
          // is scrolled back so the view doesn't snap to the bottom.
          const atBottom = term.buffer.active.viewportY >= term.buffer.active.length - term.rows;
          const userScrolling = !altRef.current && Date.now() - scrollIntentRef.current < 1500;
          if (altRef.current || (atBottom && !userScrolling)) {
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
    // Let input handlers trigger an immediate refresh (typed echo, PgUp).
    refreshRef.current = () => { if (!cancelled) fetchOnce(); };
    fetchOnce();
    const id = setInterval(fetchOnce, 700);
    return () => { cancelled = true; clearInterval(id); refreshRef.current = () => {}; };
  }, [autoRefresh, deviceId, name]);

  // Esc closes
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Fullscreen on small screens — every row of terminal space counts.
  const fullscreen = typeof window !== 'undefined' && window.innerWidth < 700;

  // When the on-screen keyboard opens, the visual viewport shrinks but the
  // layout viewport doesn't — without this the input line hides behind the
  // keyboard. Track visualViewport height and refit the terminal to it.
  const [vvH, setVvH] = useState<number | null>(null);
  useEffect(() => {
    if (!fullscreen || !window.visualViewport) return;
    const vv = window.visualViewport;
    let t: ReturnType<typeof setTimeout> | undefined;
    const upd = () => {
      setVvH(Math.round(vv.height));
      window.scrollTo(0, 0);
      clearTimeout(t);
      t = setTimeout(() => {
        const term = termRef.current, fit = fitRef.current;
        if (term && fit) {
          try {
            fit.fit();
            if (term.cols >= 40 && term.rows >= 10) {
              sizeRef.current = { cols: term.cols, rows: term.rows };
            }
          } catch { /* ignore */ }
        }
      }, 80);
    };
    upd();
    vv.addEventListener('resize', upd);
    vv.addEventListener('scroll', upd);
    return () => {
      clearTimeout(t);
      vv.removeEventListener('resize', upd);
      vv.removeEventListener('scroll', upd);
    };
  }, [fullscreen]);

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)',
      display: 'flex', alignItems: fullscreen ? 'flex-start' : 'center', justifyContent: 'center',
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
          height: fullscreen ? (vvH ? `${vvH}px` : '100dvh') : '85vh',
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
          {/* History ↔ Live toggle — history is DOM-scrolled (real scrollbar) */}
          <button
            onClick={() => setShowHistory((v) => !v)}
            style={{
              background: showHistory ? RT.text : RT.panel,
              color: showHistory ? RT.bg : RT.textDim,
              border: `1px solid ${showHistory ? RT.text : RT.border}`,
              borderRadius: 6, padding: '5px 10px',
              fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 600,
              letterSpacing: '.05em', cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', gap: 5,
            }}
          >
            <Icons.clock size={11} stroke={showHistory ? RT.bg : RT.textDim} />
            {showHistory ? 'Back to live' : 'History'}
          </button>
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
          {showHistory && <TranscriptView deviceId={deviceId} name={name} />}
          {!showHistory && status === 'paused (scrolled)' && (
            <button
              onClick={() => { scrollIntentRef.current = 0; termRef.current?.scrollToBottom(); }}
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
        <KeyBar deviceId={deviceId} name={name} onActivity={() => { activityRef.current = true; }} />
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
function KeyBar({ deviceId, name, onActivity }: { deviceId: string; name: string; onActivity?: () => void }) {
  const send = (special: string) => {
    onActivity?.();
    api.sendKeys(deviceId, name, { special: [special] }).catch(() => { /* ignore */ });
  };
  const keys: { label: string; special: string; flex?: number; accent?: string }[] = [
    { label: 'Esc', special: 'Escape' },
    { label: 'Tab', special: 'Tab' },
    { label: '⇞',   special: 'PPage' },
    { label: '⇟',   special: 'NPage' },
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
