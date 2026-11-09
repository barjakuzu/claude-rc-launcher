import type { Metadata } from "next";
import Link from "next/link";
import { Car, QrCode, Send, Smartphone, TriangleAlert } from "lucide-react";
import { MarketingShell, PageIntro } from "@/components/marketing/shell";

export const metadata: Metadata = {
  title: "How it works",
  description: "Pair your car’s browser with your phone in under a minute and send destinations to it.",
};

const steps = [
  { icon: Car, title: "Open MAPIO in your car’s browser", body: "Go to MAPIO in the in-car browser and sign in. Add it to the browser’s favourites so it’s one tap away next time." },
  { icon: QrCode, title: "Pair this browser", body: "In Devices, choose “Pair this browser”. MAPIO shows a QR code that is valid for 5 minutes and works only once." },
  { icon: Smartphone, title: "Scan with your phone", body: "Scan the code with your phone, sign in to the same MAPIO account and approve. The car browser becomes a paired device." },
  { icon: Send, title: "Send destinations", body: "Search on your phone and tap Send to Car. The destination appears in MAPIO in your car — even if the car browser was offline when you sent it." },
];

export default function HowItWorksPage() {
  return (
    <MarketingShell>
      <PageIntro eyebrow="How it works" title="Phone to car in four steps">
        MAPIO pairs your car’s browser with your account — no car apps, no vehicle access.
      </PageIntro>
      <div className="mx-auto max-w-[1100px] px-4">
        <ol className="grid gap-4 md:grid-cols-2">
          {steps.map((s, i) => (
            <li key={s.title} className="glass flex gap-4 p-6">
              <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-accent/20 text-accent-2">
                <s.icon className="h-6 w-6" aria-hidden />
              </span>
              <div>
                <p className="text-sm font-semibold text-accent-2">Step {i + 1}</p>
                <h2 className="text-lg font-semibold">{s.title}</h2>
                <p className="mt-1 leading-relaxed text-muted">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
        <section className="glass mt-8 p-6">
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <TriangleAlert className="h-5 w-5 text-warn" aria-hidden /> Browser limitations
          </h2>
          <ul className="mt-3 list-disc space-y-2 pl-6 text-muted">
            <li>“Send to Car” delivers to MAPIO in the car’s browser — not to the car’s built-in navigation.</li>
            <li>In-car browsers may restrict GPS, heading, background tabs or audio. When live position isn’t reliable, MAPIO shows a route preview rather than guidance.</li>
            <li>Many cars restrict browser use while driving. Plan your route before you set off, and never interact with the screen in a way that distracts you from driving.</li>
            <li>Performance varies by car computer. MAPIO lowers visual effects on slower hardware, and you can switch to 2D.</li>
          </ul>
          <Link href="/signup" className="btn btn-primary mt-6">Try it free</Link>
        </section>
      </div>
    </MarketingShell>
  );
}
