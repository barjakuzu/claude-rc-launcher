# In-car browser testing (Tesla)

**Status: PENDING.** No vehicle was available. Emulated landscape viewports (1280×800, 1920×1080) pass the E2E layout checks, but **nothing below has been verified on real hardware.** Record results here with the date, car model, MCU (2 or 3), and software version.

## Assumptions we do not make
- That the browser exposes `navigator.geolocation`, or that it is accurate, fresh, or includes heading/speed.
- That background tabs keep running. Guidance only runs while MAPIO is visible.
- That `speechSynthesis` or audio works.
- That WebGL performance is good enough on MCU2 (Intel Atom).
- That browser use is allowed while driving. It is often restricted.

## Test cases
| # | Case | Expected | Result (MCU2) | Result (MCU3) |
| --- | --- | --- | --- | --- |
| 1 | Open `/`, sign in | Pages readable, tap targets ≥ 48 px | pending | pending |
| 2 | `/drive` first load | Map visible < 5 s; no blank canvas | pending | pending |
| 3 | 3D ↔ 2D toggle; pan/zoom fluidity | Usable frame rate; 2D smooth | pending | pending |
| 4 | `?perf=low` | Blur off, 2D default, lower pixel ratio | pending | pending |
| 5 | Location permission prompt | Prompt appears; denial → clear message | pending | pending |
| 6 | GPS freshness while parked / driving (passenger operates) | Fix age < 5 s; accuracy noted | pending | pending |
| 7 | Heading from browser vs computed from movement | Puck rotates sensibly | pending | pending |
| 8 | Start guidance; turn card updates; reroute when off route | Instructions change at maneuvers | pending | pending |
| 9 | Tab hidden / screen switch for 2 min, return | Inbox refreshes, GPS resumes, stale warning shown meanwhile | pending | pending |
| 10 | QR pairing (car shows QR, phone approves) | Car paired within 5 s of approval | pending | pending |
| 11 | Send to Car while car browser closed, then open | Destination appears (replay) | pending | pending |
| 12 | Voice on | Speaks or fails silently (no errors) | pending | pending |
| 13 | Light mode in daylight | Map and text legible | pending | pending |
| 14 | Weak connectivity (garage) | Offline chip; recovers automatically | pending | pending |

If GPS proves unreliable (cases 6 and 8), keep guidance in "route preview" mode for that browser and say so on the Features page.
