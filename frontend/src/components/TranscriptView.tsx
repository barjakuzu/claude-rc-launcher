// TranscriptView.tsx — natively scrollable session history from the JSONL
// transcript. Claude Code's TUI has no tmux scrollback (alternate screen),
// so this is how you browse old messages: real DOM scrolling, real
// scrollbar, instant.
import { useEffect, useRef, useState, useCallback } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { api } from '../api';

interface Msg { role: string; text: string; tools: string[]; ts?: string }

export interface TranscriptViewProps {
  deviceId: string;
  name: string;
}

export function TranscriptView({ deviceId, name }: TranscriptViewProps) {
  const [messages, setMessages] = useState<Msg[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const countRef = useRef(0);

  useEffect(() => () => { mounted.current = false; }, []);

  const load = useCallback(async () => {
    try {
      const data = await api.transcript(deviceId, name) as
        { ok?: boolean; message?: string; messages?: Msg[] };
      if (!mounted.current) return;
      if (data?.ok === false || !data?.messages) {
        setError(data?.message ?? 'No transcript available.');
        return;
      }
      setError(null);
      if (data.messages.length !== countRef.current) {
        const el = scrollRef.current;
        const atBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        countRef.current = data.messages.length;
        setMessages(data.messages);
        if (atBottom) {
          // keep following the tail
          requestAnimationFrame(() => {
            scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
          });
        }
      }
    } catch {
      if (mounted.current && countRef.current === 0) setError('Could not load transcript.');
    }
  }, [deviceId, name]);

  useEffect(() => {
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [load]);

  // Initial scroll to bottom once content arrives
  useEffect(() => {
    if (messages && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages === null]);

  return (
    <div style={{
      position: 'absolute', inset: 0, background: RT.bg,
      display: 'flex', flexDirection: 'column',
    }}>
      <div
        ref={scrollRef}
        style={{
          flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch',
          overscrollBehavior: 'contain',
          padding: '14px 16px 20px',
          display: 'flex', flexDirection: 'column', gap: 12,
          fontFamily: FONT_MONO, fontSize: 12.5, lineHeight: 1.55,
        }}
      >
        {messages === null && !error && (
          <div style={{ color: RT.textLow, padding: 20, textAlign: 'center' }}>loading transcript…</div>
        )}
        {error && (
          <div style={{ color: RT.textLow, padding: 20, textAlign: 'center' }}>{error}</div>
        )}
        {messages?.map((m, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {m.role === 'user' && m.text && (
              <div style={{
                borderLeft: `3px solid ${RT.accent}`,
                background: RT.card, borderRadius: '0 8px 8px 0',
                padding: '8px 12px', color: RT.text,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {m.text}
              </div>
            )}
            {m.role === 'assistant' && m.text && (
              <div style={{
                color: RT.textDim, padding: '0 2px',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {m.text}
              </div>
            )}
            {m.tools.map((t, j) => (
              <div key={j} style={{
                color: RT.textLow, fontSize: 11.5, padding: '0 2px',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                ⚒ {t}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
