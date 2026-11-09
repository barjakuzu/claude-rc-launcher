import { Banknote, CloudSun, Layers3, Send, SlidersHorizontal, Star, Zap } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

export const FEATURES: { icon: Icon; title: string; body: string; tint: string }[] = [
  { icon: Layers3, title: "3D Maps", body: "Tilted 3D city views with extruded buildings and a clear, modern style.", tint: "from-[#5ad8ff] to-[#2f6bff]" },
  { icon: Send, title: "Send to Car", body: "Plan on your phone and send the destination to your paired car browser.", tint: "from-[#7cc4ff] to-[#3a7bff]" },
  { icon: Zap, title: "Chargers", body: "Find public chargers by connector and power, with source and freshness shown.", tint: "from-[#9b8cff] to-[#5a4bff]" },
  { icon: CloudSun, title: "Weather & Currency", body: "Weather where you are, plus official National Bank of Georgia reference rates.", tint: "from-[#ffe17a] to-[#5aa8ff]" },
  { icon: Star, title: "Saved Places", body: "Home, Work and favourites, synced across your devices.", tint: "from-[#b9a6ff] to-[#6b5cff]" },
  { icon: SlidersHorizontal, title: "Subscription Management", body: "See your plan, trial and billing — and cancel — from your account.", tint: "from-[#4ad6ff] to-[#1f7bff]" },
];

export function FeatureTile({ icon: I, title, body, tint }: (typeof FEATURES)[number]) {
  return (
    <article className="glass flex flex-col gap-3 p-5 transition-transform motion-safe:hover:-translate-y-0.5">
      <span className={`grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br ${tint} shadow-[0_10px_30px_-10px_var(--glow)]`}>
        <I className="h-7 w-7 text-white" aria-hidden />
      </span>
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="text-sm leading-relaxed text-muted">{body}</p>
    </article>
  );
}

export const FX_ICON = Banknote;
