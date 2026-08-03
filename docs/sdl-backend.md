# Phase 0 record — SDL backend on real hardware

<!-- Machine-readable decision line. tests/test_phase5_phase0_phase2_carry.py
     parses exactly this key; the prose below is for humans. -->
sdl-backend: kmsdrm

chosen backend: `kmsdrm`

This was settled by measurement, not by the probe script: the first diagnostics
report from a Pi in the field carried the answer directly.

## Evidence

Diagnostics report `db923cc6-5b87-40ca-9be4-01335960462d`, received
2026-08-02T17:29:50Z from device `148a4250…`, running app 1.0.0 in pygame mode
from a `kiosk-install.sh` install:

```json
"host":    {"model": "Raspberry Pi Model B Rev 2",
            "cpu": "ARMv6-compatible processor rev 7 (v6l) [BCM2835 rev 000e]",
            "cores": 1, "mem_total_kb": 486464,
            "kernel": "6.12.75+rpt-rpi-v6",
            "os": "Raspbian GNU/Linux 13 (trixie)", "python": "3.13.5"}
"display": {"sdl_driver": "KMSDRM", "bitsize": 32,
            "size": [800, 600], "fullscreen": true}
"versions":{"pygame": "2.6.1", "sdl": "2.32.4", "cairosvg": "2.7.1",
            "cpulimit": true}
"perf":    {"frame_ms": {"p50": 1.2, "p90": 65.1, "p99": 71.4, "n": 600},
            "boot_to_first_paint_s": 25.76}
```

Uptime at capture was 633,403 s (7.3 days) with the kiosk running throughout.

## What this overturns

**`fbcon` is not available.** The driver ladder in `_init_display` tries
fbcon first; the box landed on kmsdrm, so fbcon either does not exist in
SDL 2.32.4 as shipped or fails to open. The Phase 0 risk note ("Bookworm SDL2
may lack fbcon") is confirmed, and the ladder's existence is what made the
kiosk work anyway.

**Colour depth is 32bpp, not 16.** `10-monitor.conf` ships `DefaultDepth 16`,
but that is an X11 configuration and this box never starts X — kmsdrm talks to
DRM directly. So the `pygame.transform.smoothscale` hazard (it raises
`ValueError` on 8/16bpp surfaces) does not apply here. The `_smoothscale_safe`
guard stays as insurance for X11 and tkinter installs, but it was not the cause
of anything on this hardware.

**The framebuffer is 800x600, not the configured 720x450.** Under KMS the
framebuffer is a real DRM mode. The legacy `framebuffer_width` /
`framebuffer_height` keys only worked with the old firmware scaler, which KMS
removed, so they are silently ignored. `set_mode((720,450), FULLSCREEN)` asks
for a mode no connector offers and SDL snaps to the nearest — 800x600 — which
is then scaled to the operator's 1440x900 panel. Fonts and chrome are now
derived from the surface actually in use rather than an assumed 720x450.

## Why the installers do NOT set fkms or gpu_mem=128

`test_installer_carries_sdl_driver` originally required, for a kmsdrm decision,
that both installers carry `gpu_mem=128` and `dtoverlay=vc4-fkms-v3d`. That
expectation was written before any hardware existed, and the hardware
contradicts it:

- The Pi above runs kmsdrm **with `gpu_mem=16`** (the Tier 1c setting, chosen to
  free ~48 MB on a 486 MB box) and reaches a 1.2 ms median frame time. Raising
  it to 128 would surrender RAM for no measured gain.
- `vc4-fkms-v3d` is *fake* KMS, a different and now-deprecated stack. This box
  is on real KMS under kernel 6.12 / Raspbian trixie, where forcing fkms is at
  best a no-op and at worst breaks the display that currently works.

The test has been updated to assert what the measured configuration actually
needs. Reverting it would require re-testing on hardware first.

## Still open

`docs/muf-source.md` is **not** settled by this report: the `server` block came
back `null`, so the cairosvg rasterize timing — the number that decides whether
`PHASE2_TIMEOUT_S = 45` is adequate — is still unmeasured. The client now
records *why* that block is missing instead of sending a bare `null`, so the
next report should either carry the numbers or explain their absence.
