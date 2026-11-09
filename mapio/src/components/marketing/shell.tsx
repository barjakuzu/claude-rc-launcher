import { SiteHeader } from "./site-header";
import { SiteFooter } from "./site-footer";

export function MarketingShell({ children, trialDays }: { children: React.ReactNode; trialDays?: number }) {
  return (
    <div className="aurora min-h-dvh pt-1">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 btn btn-primary">
        Skip to content
      </a>
      <SiteHeader trialDays={trialDays} />
      <main id="main">{children}</main>
      <SiteFooter />
    </div>
  );
}

export function PageIntro({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: React.ReactNode }) {
  return (
    <section className="mx-auto max-w-[1100px] px-4 pb-6 pt-16 md:pt-20">
      {eyebrow && <p className="chip mb-4 text-accent-2">{eyebrow}</p>}
      <h1 className="text-4xl font-bold tracking-tight md:text-5xl">{title}</h1>
      {children && <div className="mt-4 max-w-3xl text-lg leading-relaxed text-muted">{children}</div>}
    </section>
  );
}

export function LegalDraftBanner() {
  return (
    <div role="note" className="mx-auto mb-8 max-w-[1100px] px-4">
      <div className="rounded-2xl border border-warn/50 bg-warn/10 p-4 text-sm text-warn">
        <b>DRAFT — requires review by qualified legal counsel before launch.</b> This page is a structural
        placeholder. Bracketed items like [COMPANY LEGAL NAME] must be completed and approved.
      </div>
    </div>
  );
}
