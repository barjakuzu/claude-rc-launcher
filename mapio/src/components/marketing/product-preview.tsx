import { ArrowUp, Car, ChevronRight, CloudSun, Layers3, Navigation2, Search, Settings, Zap } from "lucide-react";

/**
 * Hero product visual: a real MAPIO map render (generated from the app's own 3D map by
 * `npm run preview:capture`, synthetic demo city) under the actual glass widget styles.
 * Labelled as a preview; numbers shown are illustrative.
 */
export function ProductPreview({ compact = false }: { compact?: boolean }) {
  return (
    <figure className="relative [perspective:1600px]">
      <div
        className="glass glow-edge relative overflow-hidden !rounded-[28px] p-0 [transform:rotateX(14deg)_rotateY(-14deg)_rotateZ(3deg)] motion-safe:transition-transform motion-safe:duration-700 hover:[transform:rotateX(8deg)_rotateY(-8deg)_rotateZ(2deg)]"
        style={{ aspectRatio: compact ? "16/10" : "16/10" }}
      >
        <picture>
          <img
            src="/preview/map-3d.png"
            alt="MAPIO 3D map preview with a blue route through a city of extruded buildings"
            className="absolute inset-0 h-full w-full object-cover"
            loading="eager"
          />
        </picture>
        <div className="absolute inset-0 bg-gradient-to-t from-[#06122a]/40 via-transparent to-transparent" />

        <div className="absolute left-[4%] top-[6%] rounded-2xl bg-gradient-to-b from-[#2f8cff] to-[#1366ea] px-4 py-3 text-white shadow-[0_10px_30px_-8px_rgba(40,130,255,0.8)]">
          <div className="flex items-center gap-2">
            <ArrowUp className="h-7 w-7" strokeWidth={3} aria-hidden />
            <span className="text-3xl font-bold italic leading-none">300</span>
            <span className="text-lg font-semibold italic">m</span>
          </div>
          <div className="mt-1 pl-1 text-sm font-semibold">Rustaveli Avenue</div>
        </div>

        <div className="absolute right-[4%] top-[8%] flex flex-col gap-2">
          {[Search, Layers3, Settings].map((I, i) => (
            <span key={i} className="grid h-10 w-10 place-items-center rounded-xl border border-white/25 bg-[#0d1d3a]/70 text-white">
              <I className="h-5 w-5" aria-hidden />
            </span>
          ))}
        </div>

        <div className="absolute right-[22%] top-[34%] flex items-center gap-2 rounded-xl border border-white/30 bg-white/85 px-3 py-2 text-[#0b1830] shadow-lg">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-[#1f7bff] text-white">
            <Zap className="h-4 w-4" aria-hidden />
          </span>
          <span className="text-xs leading-tight">
            <b className="block">DC charger</b>
            0.4 km · availability unknown
          </span>
        </div>

        <div className="absolute bottom-[6%] left-[4%] w-[44%] min-w-[220px] divide-y divide-white/10 rounded-2xl border border-white/20 bg-[#0d1d3a]/80 text-white">
          <div className="flex items-center gap-3 px-3 py-2.5">
            <Car className="h-5 w-5 opacity-90" aria-hidden />
            <div className="text-xs leading-tight">
              <b className="text-sm">18 min</b>
              <div className="opacity-75">9.8 km · 12:36</div>
            </div>
            <ChevronRight className="ml-auto h-4 w-4 opacity-70" aria-hidden />
          </div>
          <div className="flex items-center gap-3 px-3 py-2.5">
            <Zap className="h-5 w-5 text-[#38c6ff]" aria-hidden />
            <div className="text-xs leading-tight">
              <b className="text-sm">Chargers on route</b>
              <div className="opacity-75">Open Charge Map data</div>
            </div>
            <ChevronRight className="ml-auto h-4 w-4 opacity-70" aria-hidden />
          </div>
        </div>

        <div className="absolute bottom-[7%] right-[4%] flex items-center gap-2 rounded-full border border-white/25 bg-[#0d1d3a]/75 px-3 py-1.5 text-sm font-semibold text-white">
          <CloudSun className="h-5 w-5 text-[#ffd36b]" aria-hidden /> 24°C · 15°C
        </div>

        <Navigation2
          className="absolute bottom-[14%] left-1/2 h-12 w-12 -translate-x-1/2 fill-[#2f8cff] text-white drop-shadow-[0_6px_14px_rgba(40,130,255,0.9)]"
          aria-hidden
        />
      </div>
      <figcaption className="mt-3 text-center text-xs text-faint">
        Product preview — demo map data; values are illustrative.
      </figcaption>
    </figure>
  );
}
