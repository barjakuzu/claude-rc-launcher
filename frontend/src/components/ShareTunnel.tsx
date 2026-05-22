// ShareTunnel.tsx — tunnel share modal/popover.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { btn } from './btn';
import { api } from '../api';

interface TunnelStatus {
  available: boolean;
  running: boolean;
  url?: string;
  auth_configured?: boolean;
}

interface ShareTunnelProps {
  onClose: () => void;
}

export function ShareTunnel({ onClose }: ShareTunnelProps) {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const [status, setStatus] = useState<TunnelStatus | null>(null);
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);

  // Poll tunnel status every 3s
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await api.tunnelStatus() as TunnelStatus;
        if (!cancelled && mounted.current) setStatus(s);
      } catch {/* ignore */}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const handleCopy = () => {
    if (status?.url) {
      navigator.clipboard.writeText(status.url).catch(() => {/* ignore */});
      setCopied(true);
      setTimeout(() => { if (mounted.current) setCopied(false); }, 2000);
    }
  };

  const handleStart = async () => {
    setPending(true);
    try {
      await api.tunnelStart();
    } catch {/* ignore */}
    finally {
      if (mounted.current) setPending(false);
    }
  };

  const handleStop = async () => {
    setPending(true);
    try {
      await api.tunnelStop();
    } catch {/* ignore */}
    finally {
      if (mounted.current) setPending(false);
    }
  };

  const fieldStyle: React.CSSProperties = {
    fontFamily: FONT_MONO,
    fontSize: 11,
    color: RT.text,
    background: RT.bg,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    padding: '7px 10px',
    wordBreak: 'break-all',
    lineHeight: 1.5,
  };

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
          maxWidth: 400,
          background: RT.panel,
          border: `1px solid ${RT.borderHi}`,
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          padding: '14px 16px 12px',
          borderBottom: `1px solid ${RT.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <div style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>Share Tunnel</div>
          <button onClick={onClose} style={{ ...btn('mini'), width: 22, height: 22, fontSize: 11 }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {status === null && (
            <div style={{ color: RT.textLow, fontSize: 12, textAlign: 'center', padding: '16px 0' }}>
              Loading…
            </div>
          )}

          {status !== null && !status.available && (
            <div style={{ color: RT.textDim, fontSize: 12 }}>
              <span style={{ color: RT.red, fontWeight: 600 }}>cloudflared not installed.</span>
              {' '}Install cloudflared to enable tunnel sharing.
            </div>
          )}

          {status !== null && status.available && (
            <>
              {status.running && status.url ? (
                <>
                  {!status.auth_configured && (
                    <div style={{
                      fontSize: 11,
                      color: RT.amber,
                      background: 'oklch(0.72 0.09 78 / 0.10)',
                      border: `1px solid oklch(0.72 0.09 78 / 0.30)`,
                      borderRadius: 6,
                      padding: '7px 10px',
                    }}>
                      No auth configured — anyone with the URL has access.
                    </div>
                  )}

                  <div>
                    <div style={{ fontSize: 11, color: RT.textDim, marginBottom: 4 }}>Tunnel URL</div>
                    <a
                      href={status.url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ ...fieldStyle, display: 'block', textDecoration: 'none' }}
                    >
                      {status.url}
                    </a>
                  </div>

                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      onClick={handleCopy}
                      style={{
                        flex: 1,
                        background: RT.text,
                        border: 'none',
                        borderRadius: 6,
                        padding: '7px 14px',
                        cursor: 'pointer',
                        color: RT.bg,
                        fontSize: 12,
                        fontFamily: 'inherit',
                        fontWeight: 600,
                      }}
                    >
                      {copied ? 'Copied!' : 'Copy URL'}
                    </button>
                    <button
                      onClick={handleStop}
                      disabled={pending}
                      style={{
                        flex: 1,
                        background: 'transparent',
                        border: `1px solid ${RT.border}`,
                        borderRadius: 6,
                        padding: '7px 14px',
                        cursor: pending ? 'wait' : 'pointer',
                        color: RT.textDim,
                        fontSize: 12,
                        fontFamily: 'inherit',
                        opacity: pending ? 0.6 : 1,
                      }}
                    >
                      Stop sharing
                    </button>
                  </div>
                </>
              ) : (
                <>
                  {status.running && !status.url && (
                    <div style={{ color: RT.textDim, fontSize: 12, textAlign: 'center' }}>
                      Starting tunnel…
                    </div>
                  )}
                  {!status.running && (
                    <div style={{ fontSize: 12, color: RT.textDim }}>
                      Tunnel is not running. Start it to get a public URL for this instance.
                    </div>
                  )}
                  <button
                    onClick={handleStart}
                    disabled={pending || status.running}
                    style={{
                      width: '100%',
                      background: RT.text,
                      border: 'none',
                      borderRadius: 6,
                      padding: '8px 14px',
                      cursor: (pending || status.running) ? 'wait' : 'pointer',
                      color: RT.bg,
                      fontSize: 12,
                      fontFamily: 'inherit',
                      fontWeight: 600,
                      opacity: (pending || status.running) ? 0.6 : 1,
                    }}
                  >
                    {status.running ? 'Starting…' : 'Start sharing'}
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
