import { CloudSun, Layers3, Send, SlidersHorizontal, Star, Zap } from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { IconTile } from "@/components/ui/glass";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

export const FEATURES: { icon: Icon; title: string; body: string; tint: string }[] = [
  { icon: Layers3, title: "3D Navigation", body: "Tilted 3D city views with extruded buildings and a clear, modern style.", tint: "from-[#63e0ff] to-[#2f6bff]" },
  { icon: Send, title: "Send to Car", body: "Plan on your phone and send the destination to your paired car browser.", tint: "from-[#8ccfff] to-[#3a7bff]" },
  { icon: Zap, title: "Charging Stations", body: "Public chargers by connector and power, with source and freshness shown.", tint: "from-[#a597ff] to-[#5a4bff]" },
  { icon: CloudSun, title: "Weather & Currency", body: "Local weather and official National Bank of Georgia reference rates.", tint: "from-[#ffd86b] to-[#4b9dff]" },
  { icon: Star, title: "Saved Places", body: "Home, Work and favourites, synced across your browsers.", tint: "from-[#c2b2ff] to-[#6b5cff]" },
  { icon: SlidersHorizontal, title: "Subscription Management", body: "See your plan and trial, update payment, or cancel — from your account.", tint: "from-[#4ad6ff] to-[#1f7bff]" },
];

export function FeatureTile({ icon: I, title, body, tint }: (typeof FEATURES)[number]) {
  return (
    <article className="glass lift flex h-full flex-col gap-3 !rounded-[22px] p-5">
      <IconTile tint={tint}>
        <I className="h-7 w-7" aria-hidden />
      </IconTile>
      <h3 className="mt-1 text-[17px] font-semibold tracking-tight">{title}</h3>
      <p className="text-sm leading-relaxed text-muted">{body}</p>
    </article>
  );
}
