// DirBrowser.tsx — directory picker modal/overlay for MiniLauncher.
import { useState, useEffect, useCallback, useRef } from 'react';
import { RT, FONT_MONO, Z } from '../tokens';
import { Icons } from './primitives';
import { btn } from './btn';
import { api } from '../api';
import { Portal } from './Portal';

interface BrowseResult {
  path: string;
  parent: string | null;
  dirs: string[];
}

// server.py's own /browse handler answers a bad path (outside the
// allowed roots, not a directory, unreadable) with its own 403/400 error
// body, e.g. {"error": "Access denied"} -- a real response from the
// device, relayed through as-is, not the synthetic "device unreachable"
// 502 api.ts's req() already distinguishes. Without this guard that body
// got stored into `result` as if it were a real BrowseResult, and
// result.dirs.length below threw on the missing `dirs` field: reachable
// by simply typing a bogus path into the launcher combobox and clicking
// Browse, and with only the root ErrorBoundary between that typo and a
// blanked app.
function isBrowseResult(v: unknown): v is BrowseResult {
  return !!v && typeof v === 'object' && Array.isArray((v as BrowseResult).dirs);
}

export interface DirBrowserProps {
  deviceId: string;
  initialPath: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

export function DirBrowser({ deviceId, initialPath, onSelect, onClose }: DirBrowserProps) {
  const [currentPath, setCurrentPath] = useState(initialPath);
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const navigate = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const data: unknown = await api.browse(deviceId, path);
      if (!mounted.current) return;
      if (!isBrowseResult(data)) {
        const message = (data && typeof data === 'object'
          && typeof (data as { error?: unknown }).error === 'string')
          ? (data as { error: string }).error
          : 'Failed to list directory.';
        setError(message);
        return;
      }
      setResult(data);
      setCurrentPath(data.path);
    } catch {
      if (!mounted.current) return;
      setError('Failed to list directory.');
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [deviceId]);

  useEffect(() => {
    navigate(initialPath);
  }, [navigate, initialPath]);

  const handleDirClick = (dir: string) => {
    const newPath = currentPath.replace(/\/$/, '') + '/' + dir;
    navigate(newPath);
  };

  const handleParentClick = () => {
    if (result?.parent) navigate(result.parent);
  };

  return (
    <Portal>
    {/* Backdrop */}
    <div
      onClick={onClose}
      style={{
        // Round (2026-09-09-usability): this used to be position:'absolute',
        // which resolved against MiniLauncher/V5Launcher's own small
        // position:relative row instead of the viewport -- the backdrop
        // dimmed only that row's box, not the screen, and the panel spilled
        // out below it uncontained. Now portaled to document.body (see
        // Portal.tsx) alongside every other overlay, so it needs the same
        // position:'fixed' the rest of them use to actually cover the
        // viewport.
        position: 'fixed',
        inset: 0,
        zIndex: Z.modal,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        padding: '24px 12px',
      }}
    >
      {/* Panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxWidth: 400,
          background: RT.bgRaised,
          border: `1px solid ${RT.border}`,
          borderRadius: 10,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          maxHeight: 480,
        }}
      >
        {/* Header */}
        <div style={{
          flex: 'none',
          padding: '10px 14px',
          borderBottom: `1px solid ${RT.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <Icons.folder size={13} stroke={RT.textDim} />
          <div style={{
            flex: 1,
            fontSize: 11,
            fontFamily: FONT_MONO,
            color: RT.textDim,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {currentPath || '/'}
          </div>
          <button onClick={onClose} style={{ ...btn('mini'), width: 22, height: 22, fontSize: 11 }}>
            ✕
          </button>
        </div>

        {/* Dir list */}
        <div style={{ flex: 1, overflow: 'auto', overscrollBehavior: 'contain', padding: '6px 0' }}>
          {loading && (
            <div style={{ padding: '16px 14px', fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>
              Loading…
            </div>
          )}
          {error && (
            <div style={{ padding: '12px 14px', fontSize: 11, color: RT.red }}>
              {error}
            </div>
          )}
          {!loading && !error && result && (
            <>
              {result.parent !== null && (
                <button
                  onClick={handleParentClick}
                  style={{
                    width: '100%',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: `1px solid ${RT.border}`,
                    cursor: 'pointer',
                    padding: '8px 14px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    textAlign: 'left',
                    color: RT.textDim,
                    fontFamily: FONT_MONO,
                    fontSize: 11,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = RT.panel)}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <Icons.back size={12} stroke={RT.textDim} />
                  ..
                </button>
              )}
              {result.dirs.length === 0 && (
                <div style={{ padding: '12px 14px', fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>
                  No subdirectories.
                </div>
              )}
              {result.dirs.map((dir) => (
                <button
                  key={dir}
                  onClick={() => handleDirClick(dir)}
                  style={{
                    width: '100%',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '8px 14px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    textAlign: 'left',
                    color: RT.text,
                    fontFamily: FONT_MONO,
                    fontSize: 11,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = RT.panel)}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <Icons.folder size={12} stroke={RT.textDim} />
                  {dir}
                </button>
              ))}
            </>
          )}
        </div>

        {/* Footer */}
        <div style={{
          flex: 'none',
          padding: '10px 14px',
          borderTop: `1px solid ${RT.border}`,
          display: 'flex',
          gap: 8,
          justifyContent: 'flex-end',
        }}>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: `1px solid ${RT.border}`,
              borderRadius: 6,
              padding: '6px 12px',
              cursor: 'pointer',
              color: RT.textDim,
              fontSize: 11,
              fontFamily: 'inherit',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => { onSelect(currentPath); onClose(); }}
            style={{
              background: RT.text,
              border: 'none',
              borderRadius: 6,
              padding: '6px 14px',
              cursor: 'pointer',
              color: RT.bg,
              fontSize: 11,
              fontFamily: 'inherit',
              fontWeight: 600,
            }}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
    </Portal>
  );
}
