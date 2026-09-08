// ErrorBoundary.tsx: last-resort catch-all so one uncaught render error
// blanks a single boundary instead of the whole app going white. Added in
// fix round 3 after two rounds of review found bugs in isCostReport/
// isAlertsReport, the shape guards that decide whether a fetched payload
// is safe to render: a guard that is even slightly wrong throws deep in a
// render with nothing upstream to catch it. The guards should stay
// correct on their own, this is the insurance for when they, or anything
// else in the tree, are not.
import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { RT, FONT_MONO, FONT_SANS } from '../tokens';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Uncaught render error', error, info.componentStack);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div style={{
        // Round 4: matches the same 100vh -> 100dvh fix as the two modals
        // (ScheduleModal.tsx, ResumeList.tsx). The pinned body (index.html)
        // cannot scroll to reveal anything 100vh sizes beyond the real
        // visible viewport.
        minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: RT.bg, color: RT.text, fontFamily: FONT_SANS, padding: 24,
      }}>
        <div style={{ maxWidth: 480, textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Something went wrong.</div>
          <div style={{
            fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow, marginBottom: 16,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>
            {error.message}
          </div>
          <button
            onClick={this.handleReload}
            style={{
              background: RT.panel, color: RT.text, border: `1px solid ${RT.border}`,
              borderRadius: 7, padding: '8px 16px', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13,
            }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
