# MAPIO design system

Premium midnight glass, modelled on the approved landing and dashboard renders.
All tokens live in `src/app/globals.css`; components in `src/components/ui/`.

## Tokens (`:root`)
| Token | Value | Use |
| --- | --- | --- |
| `--bg` / `--bg-2` | `#050B1A` / `#09162B` | page background gradient |
| `--panel` | `rgba(18,39,66,.45)` | glass surface |
| `--panel-elevated` | `rgba(30,59,91,.55)` | raised glass |
| `--panel-strong` | `rgba(10,24,46,.88)` | opaque panels **over the live map** (no backdrop blur) |
| `--line` / `--line-strong` | `rgba(160,207,255,.18/.30)` | hairline borders |
| `--accent` / `--accent-2` | `#287BFF` / `#36C7FF` | primary blue / cyan accent |
| `--text` / `--muted` / `--faint` | `#F5F8FF` / `#A6B9D3` / `#7088A8` | type hierarchy |
| `--radius` `--radius-sm` | 24px / 16px | card / control radius |
| `--blur` | 20px (0 in low-effects mode) | backdrop blur |
| `--ease` `--dur` | `cubic-bezier(.22,1,.36,1)`, 180ms | all transitions |
| `--dock-h` | 4.5rem | mobile dock height (clearance math) |

Light mode redefines the same tokens under `[data-theme="light"]`.
Typography: **Inter Variable**, self-hosted via `@fontsource-variable/inter`, with a system-font fallback.

## Material tiers (CSS classes)
- `.glass`: standard frosted surface (gradient fill, hairline border, inner highlight, blur).
- `.glass-elevated`: raised surface for primary cards.
- `.glass-strong`: near-opaque, no blur. Use it over the map, for the dock, and for search and sheets.
- `.glass-accent`: luminous edge and glow. **Hero objects only:** the showcase, the premium card, the auth card, and the live plan card.
- `.btn` + `.btn-primary | .btn-ghost | .btn-danger`, `.ctl` (map/icon control), `.field`, `.chip`, `.eyebrow`, `.text-gradient`.
- Motion: `.rise` / `.rise-2` / `.rise-3` (one-shot entrance, off in low-effects mode), `.lift` (hover).
- `.dock-clearance`: bottom padding = dock + safe-area inset on phones.

## Components
- `ui/glass.tsx`:
  - Surfaces: `GlassPanel`, `GlassCard`.
  - Actions and input: `GlassButton`, `GlassLink`, `GlassInput`, `MapControlButton`.
  - Status and decoration: `StatusWidget`, `IconTile`, `AmbientBackground` (aurora, mountains, road streaks; CSS/SVG only, static).
- `ui/overlay.tsx`: `Modal` (native `<dialog>`, focus trap, Esc) and `Toast`.
- `app/nav-links.tsx`: `NavLinks` (sidebar) and `BottomNavigation` (safe-area-aware dock).
- `marketing/*`: `SiteHeader` (floating glass nav), `ProductPreview` (hero showcase), `FeatureTile`, `PricingCard`, `PlanMark`, `SubscriptionIllustration`.

## Rules
- **Hierarchy over effects.** Most content sits on `.glass`; glow appears on at most one object per screen.
- **No blur over the live map.** Map overlays use `.glass-strong` to protect frame rate on car computers.
- **Low-effects mode** (`data-perf="low"`):
  - Triggered by reduced motion, `deviceMemory ≤ 2`, `hardwareConcurrency ≤ 2`, `?perf=low`, or the **Reduce effects** switch in the map layers menu.
  - Effect: no blur, no entrance animation, 2D map by default.
- **Touch targets** are at least 44px on phones. Map controls are 52px. The visual QA script audits this.
- **QA:** `node scripts/visual-snapshots.mjs <dir>` captures landing, dashboard, map, drive, billing, devices and inbox at 1440×900, 1280×800, 1920×1200, 390×844 and 375×667, and reports horizontal overflow and small touch targets.
