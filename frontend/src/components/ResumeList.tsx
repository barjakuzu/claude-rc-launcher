// ResumeList.tsx — list and resume Claude sessions.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO, fmtDate, Z } from '../tokens';
import { btn } from './btn';
import { api, isFailureEnvelope } from '../api';
import { Portal } from './Portal';

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
        const data: unknown = await api.resumeList(deviceId);
        if (!cancelled && mounted.current) {
          // Round 6: a reachable device can answer this with a real,
          // non-list response, {"ok": false, "message": "..."} -- most
          // commonly a metadata-role device's blanket 403 gate. Without
          // this check, data.projects was undefined, "?? []" quietly
          // produced an empty list, and this rendered the false "No
          // resumable sessions." below instead of the device's own
          // message.
          if (isFailureEnvelope(data)) {
            setError(data.message || 'This device refused the request.');
          } else {
            setProjects((data as { projects: ResumeProject[] }).projects ?? []);
          }
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

  // Search: filters as-you-type across whatever identifies a session to
  // a human: name, project/directory, and branch (branch isn't shown
  // in the row below, but a feature-branch name is exactly the kind of
  // thing someone remembers and types to find a session, so it still
  // counts as an identifier). The raw id is included too, since a
  // session with no name falls back to its id in the row itself. Query
  // is split on whitespace and every token must match somewhere (AND,
  // not phrase-exact), forgiving of word order, closer to how a
  // terminal fuzzy-finder behaves than a single literal substring test.
  const [query, setQuery] = useState('');
  const searchTokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const filteredSessions = searchTokens.length === 0 ? allSessions : allSessions.filter((sess) => {
    const haystack = [sess.name, sess.id, sess.cwd, sess.project, sess.branch]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return searchTokens.every((t) => haystack.includes(t));
  });

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
    <Portal>
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: Z.modal,
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
          // Round 4: 100vh can exceed the pinned document's real visible
          // height (index.html pins body to the viewport and #root to
          // 100dvh), which would size this modal taller than the screen
          // and leave the bottom of it unreachable, since the page itself
          // no longer scrolls to reveal it.
          maxHeight: 'calc(100dvh - 40px)',
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

        {/* Search: not loading/error, and only worth showing once there
            is more than one session to filter down. Sits in its own
            fixed (never-scrolling) row, one hand's easy tap away from
            the header above it, with a large clear button that stays
            reachable at the input's own edge rather than making a thumb
            travel to a corner. */}
        {!loading && !error && allSessions.length > 1 && (
          <div style={{ flex: 'none', padding: '10px 12px', borderBottom: `1px solid ${RT.border}` }}>
            <div style={{ position: 'relative' }}>
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search sessions…"
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  background: RT.card,
                  border: `1px solid ${RT.border}`,
                  borderRadius: 8,
                  padding: '11px 38px 11px 12px',
                  color: RT.text,
                  fontFamily: FONT_MONO,
                  fontSize: 13,
                  outline: 'none',
                }}
              />
              {query && (
                <button
                  onClick={() => setQuery('')}
                  aria-label="Clear search"
                  title="Clear search"
                  style={{
                    position: 'absolute',
                    right: 4,
                    top: 0,
                    bottom: 0,
                    width: 36,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: RT.textLow,
                    fontSize: 15,
                  }}
                >
                  ✕
                </button>
              )}
            </div>
          </div>
        )}

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', overscrollBehavior: 'contain', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
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

          {/* Distinct from the empty-state above: there ARE sessions,
              the search just doesn't match any of them. Never the same
              message as "no resumable sessions" (that would read as if
              the search had somehow deleted them). */}
          {!loading && !error && allSessions.length > 0 && filteredSessions.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>
              No sessions match "{query.trim()}".
            </div>
          )}

          {!loading && filteredSessions.map((sess) => (
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
    </Portal>
  );
}
