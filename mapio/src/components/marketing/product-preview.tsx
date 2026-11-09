import { ArrowUp, Car, ChevronRight, CloudSun, Layers3, Navigation2, Search, Settings, Zap } from "lucide-react";

/**
 * Hero showcase: a render of MAPIO's own 3D map (offline demo city, light theme — regenerate
 * with `npm run preview:capture`) inside a floating glass frame, with the app's overlay styles.
 * Labelled as a preview; values are illustrative.
 */
export function ProductPreview() {
  return (
    <figure className="relative mx-auto w-full max-w-[760px] [perspective:1800px]">
      {/* light spill under the device */}
      <div aria-hidden className="absolute inset-x-[8%] -bottom-6 h-24 rounded-[100%] bg-[radial-gradient(closest-side,rgba(60,160,255,0.55),transparent)] blur-2xl" />
      <div className="relative motion-safe:transition-transform motion-safe:duration-700 [transform:rotateX(10deg)_rotateY(-16deg)_rotateZ(2deg)] [transform-style:preserve-3d] hover:[transform:rotateX(6deg)_rotateY(-10deg)_rotateZ(1deg)]">
        <div className="relative aspect-[16/10] overflow-hidden rounded-[30px] border border-[rgba(170,215,255,0.45)] bg-[#cfdcec] shadow-[0_40px_90px_-30px_rgba(0,10,40,0.9),0_0_0_6px_rgba(120,190,255,0.10),0_0_70px_-10px_rgba(70,170,255,0.55)]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/preview/map-3d.png"
            alt="MAPIO 3D map preview: a blue route through a city of extruded buildings to a destination pin"
            className="absolute inset-0 h-full w-full object-cover"
            loading="eager"
            fetchPriority="high"
          />
          {/* glass sheen */}
          <div aria-hidden className="absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.22)_0%,transparent_35%,transparent_70%,rgba(10,40,90,0.25)_100%)]" />

          {/* turn instruction */}
          <div className="absolute left-[4%] top-[6%] rounded-[18px] bg-gradient-to-b from-[#3d93ff] to-[#1260e6] px-4 py-3 text-white shadow-[0_14px_30px_-10px_rgba(20,100,255,0.9),inset_0_1px_0_rgba(255,255,255,0.35)] sm:px-5">
            <div className="flex items-center gap-2">
              <ArrowUp className="h-6 w-6 sm:h-7 sm:w-7" strokeWidth={3} aria-hidden />
              <span className="text-2xl font-bold italic leading-none sm:text-3xl">300</span>
              <span className="text-base font-semibold italic">m</span>
            </div>
            <div className="mt-1 text-xs font-semibold sm:text-sm">Rustaveli Avenue</div>
          </div>

          {/* control column */}
          <div className="absolute right-[3.5%] top-[14%] hidden flex-col gap-2 sm:flex">
            {[Search, Layers3, Settings].map((I, i) => (
              <span key={i} className="grid h-10 w-10 place-items-center rounded-[14px] border border-white/40 bg-[#0e2246]/75 text-white shadow-lg backdrop-blur-sm">
                <I className="h-5 w-5" aria-hidden />
              </span>
            ))}
          </div>

          {/* charger callout */}
          <div className="absolute right-[17%] top-[20%] hidden items-center gap-2 sm:flex rounded-2xl border border-white/70 bg-white/85 px-3 py-2 text-[#0b1830] shadow-[0_12px_24px_-10px_rgba(0,20,60,0.5)] backdrop-blur-sm">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-b from-[#3d93ff] to-[#1260e6] text-white">
              <Zap className="h-4 w-4" aria-hidden />
            </span>
            <span className="text-[11px] leading-tight sm:text-xs">
              <b className="block text-[13px]">DC charger</b>
              0.4 km · availability unknown
            </span>
          </div>

          {/* route summary */}
          <div className="absolute bottom-[5%] left-[4%] w-[46%] min-w-[150px] divide-y divide-white/10 overflow-hidden rounded-[18px] border border-white/25 bg-[#0e2246]/80 text-white shadow-[0_16px_30px_-12px_rgba(0,10,40,0.8)] backdrop-blur-sm">
            <div className="flex items-center gap-3 px-3.5 py-2.5">
              <Car className="h-5 w-5 opacity-90" aria-hidden />
              <div className="text-xs leading-tight">
                <b className="text-sm">18 min</b>
                <div className="opacity-75">9.8 km · 12:36</div>
              </div>
              <ChevronRight className="ml-auto h-4 w-4 opacity-70" aria-hidden />
            </div>
            <div className="hidden items-center gap-3 px-3.5 py-2.5 sm:flex">
              <Zap className="h-5 w-5 text-[#4fd0ff]" aria-hidden />
              <div className="text-xs leading-tight">
                <b className="text-sm">Chargers near route</b>
                <div className="opacity-75">Open Charge Map data</div>
              </div>
              <ChevronRight className="ml-auto h-4 w-4 opacity-70" aria-hidden />
            </div>
          </div>

          {/* weather */}
          <div className="absolute bottom-[6%] right-[4%] flex items-center gap-2 rounded-full border border-white/35 bg-[#0e2246]/75 px-3 py-1.5 text-xs sm:text-sm font-semibold text-white backdrop-blur-sm">
            <CloudSun className="h-5 w-5 text-[#ffd36b]" aria-hidden /> 24°C · 15°C
          </div>

          <Navigation2 aria-hidden className="absolute bottom-[16%] left-[52%] h-11 w-11 -translate-x-1/2 fill-[#2f8cff] text-white drop-shadow-[0_6px_14px_rgba(40,130,255,0.9)]" />
        </div>
      </div>
      <figcaption className="mt-6 text-center text-xs text-faint">Product preview: demo map data, illustrative values.</figcaption>
    </figure>
  );
}
