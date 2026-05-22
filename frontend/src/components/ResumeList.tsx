// ResumeList.tsx — list and resume Claude sessions.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { btn } from './btn';
import { api } from '../api';

interface ResumeSession {
  id: string;
  name: string | null;
  branch: string;
  size_label: string;
  updated: string;
  cwd: string | null;
}

interface ResumeProject {
  project: string;
  sessions: ResumeSession[];
}

interface ResumeListProps {
  deviceId: string;
  onClose: () => void;
  onResumed: () => void;
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export function ResumeList({ deviceId, onClose, onResumed }: ResumeListProps) {
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const [projects, setProjects] = useState<ResumeProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [resuming, setResuming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.resumeList(deviceId) as { projects: ResumeProject[] };
        if (!cancelled && mounted.current) {
          setProjects(data.projects ?? []);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled && mounted.current) {
          setError(err instanceof Error ? err.message : 'Failed to load sessions');
          setLoading(false);
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [deviceId]);

  // Flatten projects → sessions for display
  const allSessions: (ResumeSession & { project: string })[] = projects.flatMap((p) =>
    p.sessions.map((s) => ({ ...s, project: p.project }))
  );

  const handleResume = async (sess: ResumeSession & { project: string }) => {
    setResuming(sess.id);
    try {
      const body = {
        session_id: sess.id,
        title: sess.name ?? sess.id.slice(0, 8),
        project: sess.project,
        mode: 'c',
      };
      await api.resumeStart(deviceId, body);
      if (mounted.current) {
        onResumed();
        onClose();
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : 'Resume failed');
        setResuming(null);
      }
    }
  };

  return (
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
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxWidth: 480,
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
          padding: '14px 16px 12px',
          borderBottom: `1px solid ${RT.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <div style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>Resume a session</div>
          <button onClick={onClose} style={{ ...btn('mini'), width: 22, height: 22, fontSize: 11 }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {loading && (
            <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>Loading…</div>
          )}

          {!loading && error && (
            <div style={{ padding: '10px 12px', color: RT.red, fontSize: 12, fontFamily: FONT_MONO }}>{error}</div>
          )}

          {!loading && !error && allSessions.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>
              No resumable sessions.
            </div>
          )}

          {!loading && allSessions.map((sess) => (
            <div
              key={sess.id}
              style={{
                background: RT.card,
                border: `1px solid ${RT.border}`,
                borderRadius: 8,
                padding: '10px 12px',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 12,
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {sess.name ?? sess.id.slice(0, 8)}
                </div>
                <div style={{
                  fontSize: 10,
                  fontFamily: FONT_MONO,
                  color: RT.textLow,
                  marginTop: 2,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {sess.cwd ?? sess.project} · {sess.size_label} · {fmtDate(sess.updated)}
                </div>
              </div>
              <button
                onClick={() => handleResume(sess)}
                disabled={resuming !== null}
                style={{
                  flex: 'none',
                  background: RT.text,
                  border: 'none',
                  borderRadius: 6,
                  padding: '5px 12px',
                  cursor: resuming !== null ? 'wait' : 'pointer',
                  color: RT.bg,
                  fontSize: 11,
                  fontFamily: 'inherit',
                  fontWeight: 600,
                  opacity: resuming !== null ? 0.6 : 1,
                  whiteSpace: 'nowrap',
                }}
              >
                {resuming === sess.id ? 'Resuming…' : 'Resume'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
