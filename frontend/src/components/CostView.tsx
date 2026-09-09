// CostView.tsx: Cost desktop/mobile view. Per-device 30-day token totals
// with a trend, a top projects table, and a top sessions table (Phase 3
// wiring, CONTRACT.md sections 3/5).
//
// GET /api/cost carries device + project totals only: at the time this was
// written, CONTRACT.md's shape for it had no per-session breakdown, so the
// "top sessions" table is built from /api/fleet's sessions instead, which
// already carries a `usage` field per section 3. That source is
// lifetime-cumulative and covers only currently-live sessions though, so a
// large ended session never appears and a long-lived one over-reports
// against the "last N days" framing used elsewhere on this page: the
// section is labeled for what it actually shows rather than implying a
// 30-day window it doesn't have. CONTRACT.md has since been amended to add
// a proper `sessions` array to `/api/cost`, drawn from `session_usage`
// joined to `sessions` so ended sessions survive; switch to that once the
// API lane ships it.
import { RT, FONT_SANS, FONT_MONO, fmtK, fmtPct, fmtUsage, hueForId, tintFor, usagePartialFor } from '../tokens';
import { Sparkline } from './primitives';
import type { CostDevice, CostDailyBucket, FleetSession, SessionUsage, LimitsWindow } from '../api';
import { useFleet } from '../hooks/useFleet';
import { useCost } from '../hooks/useCost';
import { useLimits } from '../hooks/useLimits';
import { limitColor } from '../limitsFormat';
import { formatRelativeTime } from '../relativeTime';

const TOP_SESSIONS = 10;

// task-m3 (2026-09-09-usability): `estimated_tokens` isn't on the shared
// LimitsWindow type (api.ts is owned by a parallel lane for this task) -
// declared locally and intersected in, same pattern LimitsSummary.tsx
// (and ScheduleModal.tsx's `trigger` field) use for the same reason.
// This hub's own derived token-budget estimate, not a figure Anthropic
// reports - see limits.py's estimate_window_tokens for the derivation
// and its "never guess" refusals.
interface EstimatedTokens {
  consumed: number;
  budget: number;
  remaining: number;
  approximate: true;
}
type WindowWithEstimate = LimitsWindow & { estimated_tokens?: EstimatedTokens | null };

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

function hasUsage(s: FleetSession): s is FleetSession & { usage: SessionUsage } {
  return s.usage != null;
}

// Last-updated label for a fleet session: prefers the authoritative
// usage.last_ts timestamp over usage_age_seconds, which would otherwise
// have to be round-tripped back through the browser clock (Date.now() at
// render time) to get a display string, drifting slightly from the actual
// moment the backend measured.
function lastUpdatedLabel(s: FleetSession): string {
  if (s.usage?.last_ts != null) return formatRelativeTime(s.usage.last_ts);
  if (s.usage_age_seconds != null) return formatRelativeTime(Date.now() / 1000 - s.usage_age_seconds);
  return '—';
}

// Small inline marker for a device's tri-state usage_partial, attached next
// to a number derived (even partly) from that device's usage. `true`
// (confirmed partial) and `undefined` (unknown, e.g. /api/fleet hasn't
// reported this device yet) both need a visible marker: only `false`
// (confirmed not partial) renders nothing. Never coerce this away with
// `!!` at the call site, that is exactly the bug this component had.
function PartialMark({ status }: { status: boolean | undefined }) {
  if (status === true) {
    return (
      <span title="This device's usage snapshot is partial (still converging after a restart)" style={{ color: RT.amber, marginLeft: 5 }}>
        ~
      </span>
    );
  }
  if (status === undefined) {
    return (
      <span title="Partial status unknown for this device (fleet data not loaded yet)" style={{ color: RT.textLow, marginLeft: 5 }}>
        ?
      </span>
    );
  }
  return null;
}

export function CostView({ onOpenDevice }: CostViewProps) {
  const { report, status } = useCost();
  const { devices: fleetDevices, sessions: fleetSessions, hasLoaded: fleetLoaded } = useFleet();
  // task-m3: the user's own words, "I don't see how much of the 5-hour
  // tokens and weekly tokens are consumed" -- same /api/limits payload
  // LimitsSummary.tsx's dropdown already reads, surfaced here too since
  // this is the other place that request was aimed at.
  const { report: limitsReport } = useLimits();

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

  const nameByDevice = new Map(fleetDevices.map((d) => [d.id, d.name]));

  // /api/fleet has been observed at least once (via SSE or the polling
  // fallback). Before that, an empty topSessions table means "we haven't
  // heard from the fleet yet," not "no sessions have usage," so those two
  // cases need different empty-state copy. Round 6: this used to be
  // connected || usingFallback || fleetDevices.length > 0 ||
  // fleetSessions.length > 0, which reads true the instant SSE errors
  // (usingFallback flips synchronously, before the fallback poll it
  // starts has resolved), not once real data has arrived. useFleet's own
  // hasLoaded (aliased to fleetLoaded above) is the one signal that only
  // means the server has actually answered at least once.

  const topSessions = fleetSessions
    .filter(hasUsage)
    .sort((a, b) => b.usage.effective - a.usage.effective)
    .slice(0, TOP_SESSIONS);

  const devicesByTotal = report.devices.slice().sort((a, b) => b.total_effective - a.total_effective);

  const partialDevices = devicesByTotal.filter((d) => usagePartialFor(fleetDevices, d.device_id) === true);
  const unknownPartialDevices = devicesByTotal.filter((d) => usagePartialFor(fleetDevices, d.device_id) === undefined);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
        <div style={{
          fontSize: 11, color: RT.textDim, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Cost · last {report.days} days
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
          {fmtK(report.totals.effective)} effective tokens total
          {partialDevices.length > 0 && (
            <span style={{ color: RT.amber, marginLeft: 6 }}>
              ({partialDevices.length} device{partialDevices.length === 1 ? '' : 's'} partial)
            </span>
          )}
          {unknownPartialDevices.length > 0 && (
            <span style={{ color: RT.textLow, marginLeft: 6 }}>
              ({unknownPartialDevices.length} device{unknownPartialDevices.length === 1 ? '' : 's'} status unknown)
            </span>
          )}
        </div>
      </div>
      {/* A permanently failing poll must not keep showing the last good
          numbers as though they were live: the age makes a stale reading
          visibly stale instead of silently current. */}
      <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, marginBottom: 16 }}>
        updated {formatRelativeTime(report.generated_at)}
      </div>

      {limitsReport?.primary?.available && (
        <Section title="Limits">
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <LimitEstimateCard label="5 hour" window={limitsReport.primary.five_hour} />
            <LimitEstimateCard label="7 day" window={limitsReport.primary.seven_day} />
          </div>
        </Section>
      )}

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
                partial={usagePartialFor(fleetDevices, d.device_id)}
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
          // Wide content scrolls inside its own container, never the page:
          // at 390px this table is wider than the viewport, and without
          // this wrapper the overflow would otherwise leak onto the whole
          // scroll view instead of staying scoped to the table.
          <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 420, fontFamily: FONT_SANS }}>
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
                  <td style={tdStyle}>
                    {fmtK(p.effective)}
                    <PartialMark status={usagePartialFor(fleetDevices, p.device_id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Section>

      <Section
        title="Top sessions"
        note="Current sessions only, lifetime totals, not scoped to the 30-day window above. Ended sessions are not included yet."
      >
        {topSessions.length === 0 ? (
          <EmptyNote text={fleetLoaded ? 'No session usage recorded yet.' : 'Loading session data…'} />
        ) : (
          <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 480, fontFamily: FONT_SANS }}>
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
                  <td style={tdStyle}>
                    {fmtUsage(s.usage)}
                    <PartialMark status={usagePartialFor(fleetDevices, s.device_id)} />
                  </td>
                  <td style={tdStyle}>{lastUpdatedLabel(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{
        fontSize: 11, color: RT.textDim, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: note ? 3 : 8,
      }}>
        {title}
      </div>
      {note && (
        <div style={{ fontSize: 10.5, color: RT.textLow, fontFamily: FONT_MONO, marginBottom: 8, maxWidth: 640 }}>
          {note}
        </div>
      )}
      {children}
    </div>
  );
}

// task-m3: one window's percent + our own derived token estimate. `null`
// (window not reported at all) and "reported but no estimate yet"
// (below the percent floor, or not enough measurement history) are
// deliberately different states below -- the first shows nothing to
// derive from, the second shows the real percent while explaining the
// token half isn't ready, never a fabricated number in either case.
function LimitEstimateCard({ label, window }: { label: string; window: WindowWithEstimate | null }) {
  const cardStyle: React.CSSProperties = {
    flex: '1 1 160px', minWidth: 150, background: RT.card,
    border: `1px solid ${RT.border}`, borderRadius: 10, padding: '12px 14px',
  };
  const labelStyle: React.CSSProperties = {
    fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow,
    letterSpacing: '.1em', textTransform: 'uppercase',
  };
  if (!window) {
    return (
      <div style={cardStyle}>
        <div style={labelStyle}>{label}</div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 18, color: RT.textLow, marginTop: 5 }}>—</div>
      </div>
    );
  }
  const color = limitColor(window.percent, window.severity);
  const est = window.estimated_tokens;
  return (
    <div style={cardStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={{ fontFamily: FONT_MONO, fontSize: 20, fontWeight: 600, color, marginTop: 4 }}>
        {fmtPct(window.percent)}
      </div>
      {est ? (
        <div
          title="Estimated from this hub's own measured token usage in this window, divided by Anthropic's reported percent. Anthropic does not report a token budget itself, so this is never exact."
          style={{ marginTop: 8, cursor: 'help' }}
        >
          <div style={{ fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textDim }}>
            {fmtK(est.consumed)} / ~{fmtK(est.budget)} tokens
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, marginTop: 2 }}>
            ~{fmtK(est.remaining)} left · approximate
          </div>
        </div>
      ) : (
        <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, marginTop: 8 }}>
          token estimate not available yet
        </div>
      )}
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
  device: CostDevice; days: number; generatedAt: number; partial: boolean | undefined; hue: number; onOpen?: () => void;
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
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{device.name ?? '—'}</span>
        </div>
        {partial === true && (
          <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.amber, marginTop: 3 }}>
            partial data
          </div>
        )}
        {partial === undefined && (
          <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow, marginTop: 3 }}>
            partial status unknown
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
