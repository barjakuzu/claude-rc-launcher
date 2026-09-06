// ConfigMatrix.tsx — parity matrix across devices' claude-config state,
// with red/amber skew highlighting, expandable rows, and a per-row
// "copy update command" action. Renders only skill/plugin/rule NAMES,
// never file contents.
import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { RT, FONT_SANS, FONT_MONO } from '../tokens';
import { fetchConfigMatrix } from '../api';
import type { ConfigMatrix as ConfigMatrixData, ConfigReport } from '../api';
import type { DeviceCard } from '../types';

const UPDATE_CMD = 'git -C ~/claude-config pull --ff-only && ~/claude-config/bootstrap.sh';

function isReport(v: ConfigReport | { error: string } | undefined): v is ConfigReport {
  return !!v && !('error' in v);
}

function isErrorEntry(v: ConfigReport | { error: string } | undefined): v is { error: string } {
  return !!v && 'error' in v;
}

const thStyle: CSSProperties = {
  textAlign: 'left', padding: '6px 10px', color: RT.textLow,
  fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase', fontFamily: FONT_MONO,
};

const joinOrDash = (list: string[]): string => (list.length ? list.join(', ') : '—');

export function ConfigMatrixView({ cards }: { cards: DeviceCard[] }) {
  const [matrix, setMatrix] = useState<ConfigMatrixData | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchConfigMatrix()
      .then((m) => { if (!cancelled) setMatrix(m); })
      .catch(() => { if (!cancelled) setLoadError(true); });
    return () => { cancelled = true; };
  }, []);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const copyCmd = async (id: string) => {
    try {
      await navigator.clipboard.writeText(UPDATE_CMD);
      setCopiedId(id);
      setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1500);
    } catch { /* ignore */ }
  };

  if (loadError) {
    return (
      <div style={{ padding: 24, color: RT.red, fontFamily: FONT_MONO, fontSize: 12 }}>
        Failed to load config matrix.
      </div>
    );
  }

  if (!matrix) {
    return (
      <div style={{ padding: 24, color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12 }}>
        Loading config matrix…
      </div>
    );
  }

  const cellStyle = (skewed: boolean, tone: 'red' | 'amber' = 'red'): CSSProperties => ({
    padding: '6px 10px',
    background: skewed ? `${tone === 'red' ? RT.red : RT.amber}26` : 'transparent',
    fontFamily: FONT_MONO,
    fontSize: 11.5,
    color: RT.textDim,
  });

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      {matrix.hub_head && (
        <div style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, marginBottom: 10 }}>
          hub HEAD: {matrix.hub_head.slice(0, 12)}
        </div>
      )}
      <table style={{ borderCollapse: 'collapse', width: '100%', fontFamily: FONT_SANS }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${RT.border}` }}>
            <th style={thStyle}>Device</th>
            <th style={thStyle}>Claude</th>
            <th style={thStyle}>Commit</th>
            <th style={thStyle}>Dirty</th>
            <th style={thStyle}>Skills</th>
            <th style={thStyle}>Plugins</th>
            <th style={thStyle}>Rules</th>
            <th style={thStyle}>Hooks</th>
            <th style={thStyle}>RC@startup</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {cards.map((c) => {
            const entry = matrix.devices[c.id];
            const reasons = matrix.skew[c.id] || [];
            const has = (r: string) => reasons.includes(r);
            const report = isReport(entry) ? entry : null;
            const unreachable = isErrorEntry(entry);
            const isExpanded = expanded.has(c.id);
            const commitSkewed = has('head differs from hub') || has('settings uncommitted');
            const commitTone: 'red' | 'amber' = has('head differs from hub') ? 'red' : 'amber';
            return (
              <>
                <tr
                  key={c.id}
                  style={{ borderBottom: `1px solid ${RT.border}`, cursor: report ? 'pointer' : 'default' }}
                  onClick={() => report && toggle(c.id)}
                >
                  <td style={{ padding: '6px 10px', fontFamily: FONT_SANS, fontSize: 12.5, color: RT.text }}>
                    {report ? (isExpanded ? '▾ ' : '▸ ') : ''}{c.name}
                  </td>
                  {unreachable ? (
                    <td colSpan={7} style={{ padding: '6px 10px', fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textLow, fontStyle: 'italic' }}>
                      unreachable{(entry as { error: string }).error ? ` — ${(entry as { error: string }).error}` : ''}
                    </td>
                  ) : (
                    <>
                      <td style={cellStyle(has('claude version differs'))}>{report?.claude_version ?? '—'}</td>
                      <td style={cellStyle(commitSkewed, commitTone)}>{report?.claude_config.short_head ?? '—'}</td>
                      <td style={cellStyle(has('dirty'))}>{report ? (report.claude_config.dirty ? 'yes' : 'no') : '—'}</td>
                      <td style={cellStyle(has('external skills not installed (run bootstrap)'), 'amber')}>
                        {report ? `${report.skills.count} / ${report.skills.deps_missing.length} deps missing` : '—'}
                      </td>
                      <td style={cellStyle(has('missing plugins'))}>
                        {report ? `${report.plugins.missing.length} missing / ${report.plugins.extra.length} extra` : '—'}
                      </td>
                      <td style={{ padding: '6px 10px', fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textDim }}>
                        {report ? `${report.rules.shared.length} shared / ${report.rules.local.length} local` : '—'}
                      </td>
                      <td style={cellStyle(has('no hooks'))}>{report ? (report.settings.hooks_present ? 'yes' : 'no') : '—'}</td>
                      <td style={{ padding: '6px 10px', fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textDim }}>
                        {report ? (report.settings.remote_control_at_startup ? 'yes' : 'no') : '—'}
                      </td>
                    </>
                  )}
                  <td style={{ padding: '6px 10px' }}>
                    <button
                      onClick={(e) => { e.stopPropagation(); copyCmd(c.id); }}
                      style={{
                        fontFamily: FONT_MONO, fontSize: 10, background: 'transparent',
                        border: `1px solid ${RT.border}`, borderRadius: 5, padding: '3px 8px',
                        color: RT.textLow, cursor: 'pointer', whiteSpace: 'nowrap',
                      }}
                    >
                      {copiedId === c.id ? 'Copied ✓' : 'Copy update command'}
                    </button>
                  </td>
                </tr>
                {isExpanded && report && (
                  <tr key={`${c.id}-detail`} style={{ borderBottom: `1px solid ${RT.border}` }}>
                    <td colSpan={10} style={{ padding: '4px 10px 14px 26px', fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, lineHeight: 1.7 }}>
                      <div>skills: {joinOrDash(report.skills.names)}</div>
                      <div>device-only skills: {joinOrDash(report.skills.device_only)}</div>
                      <div>plugins declared: {joinOrDash(report.plugins.declared)}</div>
                      <div>plugins installed: {joinOrDash(report.plugins.installed)}</div>
                      <div>rules (shared): {joinOrDash(report.rules.shared)}</div>
                      <div>rules (local): {joinOrDash(report.rules.local)}</div>
                      <div>effective model: {report.effective_model || '—'}</div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
