// CostView.tsx: Cost desktop/mobile view. Per-device 30-day token totals
// with a trend, a top projects table, and a top sessions table (Phase 3
// wiring, CONTRACT.md sections 3/5).
//
// GET /api/cost carries device + project totals only: CONTRACT.md's shape
// for it has no per-session breakdown. The "top sessions" table the brief
// asks for is built instead from /api/fleet's sessions, which already gets
// a `usage` field per section 3. useFleet() is already the established
// hook for that data (Sessions tab, header), so this view reuses it rather
// than inventing a second fetch path for something the contract doesn't
// expose from /api/cost.
import { useEffect, useState } from 'react';
import { RT, FONT_SANS, FONT_MONO, fmtK, fmtUsage, hueForId, tintFor } from '../tokens';
import { Sparkline } from './primitives';
import { fetchCost } from '../api';
import type { CostReport, CostDevice, CostDailyBucket } from '../api';
import { useFleet } from '../hooks/useFleet';
import { formatRelativeTime } from '../relativeTime';

const DAYS = 30;
const POLL_INTERVAL_MS = 30_000;
const TOP_SESSIONS = 10;

type LoadStatus = 'loading' | 'ok' | 'unavailable';

interface CostViewProps {
  /** Optional. Lets a device row jump to that device's detail view,
   * mirroring AllSessions.tsx's device chip. Omit for a context with no
   * such navigation (none currently, but keeps the view self-contained). */
  onOpenDevice?: (id: string) => void;
}

const thStyle: React.CSSProperties = {
  textAlign: 'left', padding: '6px 10px', color: RT.textLow,
  fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase', fontFamily: FONT_MONO,
};
const tdStyle: React.CSSProperties = {
  padding: '6px 10px', fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textDim,
};

export function CostView({ onOpenDevice }: CostViewProps) {
  const [report, setReport] = useState<CostReport | null>(null);
  const [status, setStatus] = useState<LoadStatus>('loading');
  const { devices: fleetDevices, sessions: fleetSessions } = useFleet();

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchCost(DAYS);
        if (cancelled) return;
        setReport(data);
        setStatus('ok');
      } catch {
        if (cancelled) return;
        // A single missed poll after we've already loaded once should not
        // blank the view out from under the user.
        setStatus((s) => (s === 'ok' ? s : 'unavailable'));
      }
    };
    load();
    const t = setInterval(load, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (status === 'unavailable') {
    return (
      <div style={{ flex: 1, padding: 24, color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13 }}>
        Cost data not available yet.
      </div>
    );
  }

  if (status === 'loading' || !report) {
    return (
      <div style={{ flex: 1, padding: 24, color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13 }}>
        Loading cost data…
      </div>
    );
  }

  const partialByDevice = new Map(fleetDevices.map((d) => [d.id, !!d.usage_partial]));
  const nameByDevice = new Map(fleetDevices.map((d) => [d.id, d.name]));

  const topSessions = fleetSessions
    .filter((s) => s.usage != null)
    .sort((a, b) => b.usage!.effective - a.usage!.effective)
    .slice(0, TOP_SESSIONS);

  const devicesByTotal = report.devices.slice().sort((a, b) => b.total_effective - a.total_effective);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 16 }}>
        <div style={{
          fontSize: 11, color: RT.textDim, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Cost · last {report.days} days
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
          {fmtK(report.totals.effective)} effective tokens total
        </div>
      </div>

      <Section title="Devices">
        {devicesByTotal.length === 0 ? (
          <EmptyNote text="No device usage recorded yet." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {devicesByTotal.map((d) => (
              <DeviceCostRow
                key={d.device_id}
                device={d}
                days={report.days}
                generatedAt={report.generated_at}
                partial={!!partialByDevice.get(d.device_id)}
                hue={hueForId(d.device_id)}
                onOpen={onOpenDevice ? () => onOpenDevice(d.device_id) : undefined}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title="Top projects">
        {report.projects.length === 0 ? (
          <EmptyNote text="No project usage recorded yet." />
        ) : (
          <table style={{ borderCollapse: 'collapse', width: '100%', fontFamily: FONT_SANS }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${RT.border}` }}>
                <th style={thStyle}>Device</th>
                <th style={thStyle}>Project</th>
                <th style={thStyle}>Effective tokens</th>
              </tr>
            </thead>
            <tbody>
              {report.projects.map((p, i) => (
                <tr key={`${p.device_id}:${p.project}:${i}`} style={{ borderBottom: `1px solid ${RT.border}` }}>
                  <td style={tdStyle}>{nameByDevice.get(p.device_id) ?? p.device_id}</td>
                  <td style={tdStyle}>{p.project || '—'}</td>
                  <td style={tdStyle}>{fmtK(p.effective)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Top sessions">
        {topSessions.length === 0 ? (
          <EmptyNote text="No session usage recorded yet." />
        ) : (
          <table style={{ borderCollapse: 'collapse', width: '100%', fontFamily: FONT_SANS }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${RT.border}` }}>
                <th style={thStyle}>Device</th>
                <th style={thStyle}>Session</th>
                <th style={thStyle}>Effective tokens</th>
                <th style={thStyle}>Updated</th>
              </tr>
            </thead>
            <tbody>
              {topSessions.map((s) => (
                <tr key={`${s.device_id}:${s.session_id}`} style={{ borderBottom: `1px solid ${RT.border}` }}>
                  <td style={tdStyle}>{nameByDevice.get(s.device_id) ?? s.device_id}</td>
                  <td style={{ ...tdStyle, color: RT.text }}>{s.name ?? s.session_id}</td>
                  <td style={tdStyle}>{fmtUsage(s.usage)}</td>
                  <td style={tdStyle}>
                    {s.usage_age_seconds != null
                      ? formatRelativeTime(Date.now() / 1000 - s.usage_age_seconds)
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{
        fontSize: 11, color: RT.textDim, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 8,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function EmptyNote({ text }: { text: string }) {
  return (
    <div style={{
      padding: 16, color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12,
      border: `1px dashed ${RT.border}`, borderRadius: 8,
    }}>
      {text}
    </div>
  );
}

// usage_daily / cost.devices[].daily is sparse: the device reports only
// the days it actually saw usage on (CONTRACT.md's "usage_daily is sparse,
// not zero-filled" ruling). A sparkline needs a continuous axis, so this
// fills the missing days with 0, anchored on the report's own
// `generated_at` rather than the browser clock so the filled range always
// matches what the server means by "the last N days," never shifting bars
// out of alignment with their real dates. A missing day means "no usage
// that day," so 0 is correct here; that is the opposite case from a null
// per-session `usage`, which means unknown and must render as a dash.
function gapFillDaily(daily: CostDailyBucket[], days: number, generatedAt: number): number[] {
  const byDay = new Map(daily.map((d) => [d.day, d.effective]));
  const dayMs = 86_400_000;
  const endMs = Math.floor((generatedAt * 1000) / dayMs) * dayMs;
  const out: number[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const dayStr = new Date(endMs - i * dayMs).toISOString().slice(0, 10);
    out.push(byDay.get(dayStr) ?? 0);
  }
  return out;
}

function DeviceCostRow({ device, days, generatedAt, partial, hue, onOpen }: {
  device: CostDevice; days: number; generatedAt: number; partial: boolean; hue: number; onOpen?: () => void;
}) {
  const hueColor = tintFor(hue, 0.70, 0.10);
  const trend = gapFillDaily(device.daily, days, generatedAt);
  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`, borderRadius: 10,
      padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 16,
    }}>
      <button
        onClick={onOpen}
        disabled={!onOpen}
        title={onOpen ? 'Open device' : undefined}
        style={{
          background: 'transparent', border: 'none', padding: 0, textAlign: 'left',
          cursor: onOpen ? 'pointer' : 'default', color: RT.text, fontFamily: 'inherit',
          minWidth: 160, flex: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ width: 7, height: 7, borderRadius: 7, background: hueColor, flex: 'none' }} />
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{device.name}</span>
        </div>
        {partial && (
          <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.amber, marginTop: 3 }}>
            partial data
          </div>
        )}
      </button>
      <div style={{ flex: 1, minWidth: 0, color: hueColor }}>
        <Sparkline data={trend} w={300} h={26} color={hueColor} fillOpacity={0.10} dotEnd responsive />
      </div>
      <div style={{ textAlign: 'right', flex: 'none', minWidth: 90 }}>
        <div style={{ fontFamily: FONT_MONO, fontSize: 15, fontWeight: 500 }}>{fmtK(device.total_effective)}</div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow }}>effective</div>
      </div>
    </div>
  );
}
