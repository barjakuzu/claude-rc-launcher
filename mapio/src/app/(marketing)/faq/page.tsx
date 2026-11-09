import type { Metadata } from "next";
import { MarketingShell, PageIntro } from "@/components/marketing/shell";
import { FaqList, FAQ_ITEMS } from "@/components/marketing/faq";

export const metadata: Metadata = { title: "FAQ", description: "Answers about MAPIO, the free trial, Send to Car and browser support." };

export default function FaqPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: FAQ_ITEMS.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
  };
  return (
    <MarketingShell>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <PageIntro eyebrow="FAQ" title="Frequently asked questions" />
      <div className="mx-auto max-w-[900px] px-4">
        <FaqList />
      </div>
    </MarketingShell>
  );
}
