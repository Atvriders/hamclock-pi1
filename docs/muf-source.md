# Phase 2 record — MUF map rasterization on real hardware

muf-subprocess-timeout-s: 45

Settled by measurement from a Pi in the field, not by the install-time probe.

## The decision rule this answers

The Phase 2 plan set the rule up front: benchmark the rasterize on a real
Pi 1B, then

  * median <= 20 s  -> ship cairosvg as-is
  * 20-30 s         -> ship it with a raised timeout
  * > 30 s          -> change the source

## What the hardware actually said

Diagnostics report of 2026-08-30 (app 1.0.6), from a Pi 1B — BCM2835 ARMv6,
486 MB, Raspbian trixie, cairosvg 2.7.1, cpulimit present:

```json
"muf": {"rasterize_ok": 0, "rasterize_timeout": 3, "rasterize_fail": 0,
        "last_timeout_budget_s": 88, "timeout_s": 110,
        "slim_ok": 3, "svg_bytes": 375583, "slim_bytes": 352798,
        "png_source": "none", "last_served": "none"}
```

cairosvg never completed once. It was still timing out at an **88 second**
budget, after the adaptive escalation had widened it twice. That is not
"20-30 s, raise the timeout" territory — it is nearly triple the point at which
the rule says to change something. Every earlier estimate here (34-57 s) was
about 2x optimistic.

The visible symptom was the centre panel permanently reading "(map loading)".

## What changed

The engine, not the source. Profiling had put ~61% of cairosvg's time in an
O(n^2) cssselect2 re-walk performed once per `<use>` element, and this map has
257 of them. librsvg is C and has no equivalent path.

Measured on the real KC2G SVG (x86, same file, same 360 px output):

| engine | time | output |
|---|---|---|
| cairosvg 2.7.1 | 1.204 s | 71,930 B PNG |
| rsvg-convert 2.54.7 | 0.181 s | 79,814 B PNG |

**6.7x**, and the two renders are visually identical — same contours, station
dots, colorbar and labels.

## Confirmed back on the hardware

Report of 2026-09-06 (app 1.0.7), same Pi:

```json
"muf": {"engine_used": "rsvg", "engines": ["rsvg", "cairosvg"],
        "rasterize_ok": 19, "rasterize_timeout": 0, "rasterize_fail": 0,
        "engine_absent": 0,
        "last_render_s": 10.527, "render_ewma_s": 10.47, "timeout_s": 45,
        "png_bytes": 63929, "png_source": "live", "last_served": "png"}
```

**10.5 seconds**, 19 consecutive successes, no timeouts, no escalation. Inside
the original rule's "<= 20 s, ship it" band — so the timeout stays at its 45 s
floor, which is 4.3x the observed render.

## Why the timeout is 45 and not tighter

`_muf_timeout()` adapts to `4 x EWMA` with 45 s as the floor, so at a 10.47 s
EWMA the computed budget is ~42 s and the floor governs. Headroom worth
keeping: the render competes for a single core against the 10 FPS loop under a
50% cpulimit duty cycle, and a box under memory pressure or serving a data
refresh at the same moment will be slower than a quiet one.

## Fallback

cairosvg remains as the second rung of the ladder and must stay there. A Pi
upgraded in place gets a new `server.py` without necessarily having
`librsvg2-bin` installed; falling back to a slow rasterize beats a blank panel.
An engine that is simply absent is reported as `engine_absent` rather than
counted as a render failure, so that fallback does not look like a fault.
