import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { RT, FONT_MONO, FONT_SANS } from '../tokens';
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
      scrollback: 5000,
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

    // Resize handler — refit when container resizes.
    const onResize = () => { try { fit.fit(); } catch { /* ignore */ } };
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
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
        const data = await api.preview(deviceId, name);
        if (cancelled || !mounted.current) return;
        const output: string = data?.output ?? '';
        if (output !== lastContentRef.current) {
          lastContentRef.current = output;
          const term = termRef.current;
          if (term) {
            const wasAtBottom = term.buffer.active.viewportY >= term.buffer.active.length - term.rows;
            term.reset();
            term.write(output);
            if (wasAtBottom) term.scrollToBottom();
          }
        }
        if (mounted.current) setStatus('live');
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

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 50, padding: 16,
      fontFamily: FONT_SANS,
    }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 12, width: '100%', maxWidth: 1000, height: '85vh',
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
        <div
          ref={containerRef}
          style={{
            flex: 1, padding: 10, background: '#1a1a18', overflow: 'hidden',
          }}
        />
        <div style={{
          flex: 'none', padding: '8px 14px', borderTop: `1px solid ${RT.border}`,
          background: RT.bgRaised, display: 'flex', alignItems: 'center', gap: 10,
          fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow,
          letterSpacing: '.04em',
        }}>
          <span>type to send keystrokes</span>
          <span style={{ color: RT.borderHi }}>·</span>
          <span>Ctrl-C / Enter / Tab / arrows work</span>
          <div style={{ flex: 1 }} />
          <span>{deviceId}</span>
        </div>
      </div>
    </div>
  );
}
