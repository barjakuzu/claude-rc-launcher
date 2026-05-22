// PreviewModal.tsx — tmux pane preview for a session.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { btn } from './btn';
import { api } from '../api';

interface PreviewResult {
  name: string;
  output: string;
  status: string;
}

interface PreviewModalProps {
  deviceId: string;
  name: string;
  onClose: () => void;
}

export function PreviewModal({ deviceId, name, onClose }: PreviewModalProps) {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const [result, setResult] = useState<PreviewResult | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPreview = async () => {
    try {
      const data = await api.preview(deviceId, name) as PreviewResult;
      if (mounted.current) {
        setResult(data);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : 'Failed to load preview');
      }
    }
  };

  // Fetch on mount + optional 3s auto-refresh
  useEffect(() => {
    let cancelled = false;
    fetchPreview();
    if (!autoRefresh) return;
    const id = setInterval(() => { if (!cancelled) fetchPreview(); }, 3000);
    return () => { cancelled = true; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, name, autoRefresh]);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        background: 'rgba(0,0,0,.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px 16px',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxWidth: 680,
          maxHeight: 'calc(100vh - 40px)',
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
          flex: 'none',
          padding: '12px 16px',
          borderBottom: `1px solid ${RT.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <div style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>
            Preview — <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: RT.textDim }}>{name}</span>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: RT.textDim, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              style={{ cursor: 'pointer', accentColor: RT.green }}
            />
            auto-refresh
          </label>
          <button onClick={onClose} style={{ ...btn('mini'), width: 22, height: 22, fontSize: 11 }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
          {error && (
            <div style={{
              padding: '12px 16px',
              color: RT.red,
              fontSize: 12,
              fontFamily: FONT_MONO,
            }}>
              {error}
            </div>
          )}
          {result && (
            <pre style={{
              margin: 0,
              padding: '12px 16px',
              fontFamily: FONT_MONO,
              fontSize: 11,
              lineHeight: 1.5,
              color: RT.text,
              background: RT.bg,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              minHeight: '200px',
            }}>
              {result.output || '(empty)'}
            </pre>
          )}
          {!result && !error && (
            <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>
              Loading…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
