# Pi 1 native pygame client as the default — design

**Date:** 2026-06-03
**Status:** Approved design after adversarial multi-lens review (workflow `wf_8da41a43-b53`); ready for implementation plan.
**Sources:** brainstorming chat transcript + research workflow `wf_d3454b75-c50` + review workflow `wf_8da41a43-b53`.

## Decisions locked in

From the brainstorming session:

- **Architecture choice:** native pygame client replaces the browser as the default Pi 1 install path. No browser process in the default flow.
- **MUF map:** server-side rasterize the KC2G SVG to PNG; native client blits the PNG.
- **First-boot setup:** a one-time pygame wizard on the HDMI display (no second-device requirement).
- **Themes:** all 4 ported, settings-driven palette. No runtime switching UI in v1.
- **Approach:** iterate on the existing `hamclock_pygame.py` — not a clean rewrite.
- **Target hardware:** Raspberry Pi 1 Model B, 700 MHz armv6, 512 MB RAM, HDMI at 1440×900, Raspberry Pi OS Bookworm.

## Goal

Make the Pi 1 dashboard genuinely responsive — perceived sub-200 ms click-to-photons on the propagation tabs — by dropping the browser entirely while preserving every information feature the browser version has today.

## Success criteria

Every numeric criterion below is a **target measured by the phase whose verification it cites**, not a load-bearing pre-implementation claim:

- A fresh `kiosk-install.sh` (no flags) on a Pi 1 brings up a pygame fullscreen dashboard with: SOLAR / BANDS / SDO / Geomag / X-Ray / Open-Bands / MUF / DX-Spots / Band-Activity / Propagation-tabbed, in any of 4 themes selected at first boot.
- Tab clicks (DRAP / Aurora / Enlil) put the new image on screen with **p99 click-to-photons ≤ 200 ms** at 10 FPS — measured by the synthetic-event harness in Phase 1 verification over 100 injected clicks at random sub-frame offsets. (A 10 FPS loop has a 100 ms frame period; the click can arrive immediately after `event.get()` ran, so single-frame worst case is ~100 ms before the loop sees it and another ~100 ms before the new frame is on screen. Sub-100 ms end-to-end is arithmetically impossible at 10 FPS — that was the first draft's error.) If measured p99 exceeds 200 ms, Phase 1 escalates the propagation panel area to 20 FPS or implements full dirty-rect updates.
- Resident set size of the pygame client **≤ 90 MB steady-state** (CPython 3.11 baseline ~12–15 MB + SDL2 / SDL_image / SDL_ttf / libfreetype / libpng / libjpeg ~10 MB + 1440×900 double-buffer surface ~10 MB + decoded MUF surface ~5 MB + scaled-surface LRU ~6 MB + glyph cache ~2 MB + headroom). Combined `hamclock-lite` + `hamclock-kiosk` RSS **≤ 160 MB on a quiescent box** (definition: idle dashboard, 5 min post-boot, caches warm), measured by `ps -o rss`. The cairosvg subprocess transient (~25–30 MB during render) is excluded from steady-state but must fit inside the Pi 1's free-RAM headroom (~370 MB post-kernel/systemd).
- No regressions in the kiosk crash-resilience work landed earlier this session (`Restart=always`, `StartLimitIntervalSec=0`, while-loop relaunch wrapper, render-loop guard with consecutive-error counter, no-CLI-fallback).
- Existing browser and tkinter install paths still work via opt-in flags (`--browser`, `--tkinter`); existing pygame-mode installs upgrade without losing user settings; users can revert with `sudo ./kiosk-install.sh --browser` (rollback documented in Phase 5 / README).

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                                  Pi 1                                  │
│                                                                        │
│  ┌──────────────────────────┐         ┌─────────────────────────────┐  │
│  │  hamclock-lite.service   │         │  hamclock-kiosk.service     │  │
│  │  (python3 server.py)     │   /api  │  (kiosk.sh → python3        │  │
│  │  - JSON endpoints        │ ◄────── │   hamclock_pygame.py)       │  │
│  │  - image proxies         │   PNG   │  - render loop @ 10 FPS     │  │
│  │  - background fetcher    │ ──────► │  - SDL → fbcon / X / kms    │  │
│  │  - NEW: SVG→PNG          │         │  - settings.json reader     │  │
│  │    rasterize subprocess  │         │  - first-boot wizard        │  │
│  │    (cpulimit 50% one core)         │  - --inject-events (debug)  │  │
│  └──────────────────────────┘         └─────────────────────────────┘  │
│                                                                        │
│   /etc/hamclock-lite/    ── created by installer:                      │
│     ├─ settings.json     ── mode 0644, owned by SERVICE_USER           │
│     └─ (settings.json.tmp.* during atomic writes)                      │
│   directory mode 0755 owned by SERVICE_USER so the wizard writes       │
│   directly without setuid.                                             │
│                                                                        │
│   /usr/local/bin/hamclock-setup ── shell wrapper that execs:           │
│     exec python3 /opt/hamclock-lite/hamclock_pygame.py --setup-cli "$@"│
│   When invoked as sudo, drops to SERVICE_USER before writing so the    │
│   resulting file is owned by the service user.                         │
└────────────────────────────────────────────────────────────────────────┘
```

Browser and tkinter paths preserved unchanged behind their flags. Only the default flips.

## Phase 0 — SDL driver verification (blocks Phase 5)

**Risk:** SDL2 on Raspberry Pi OS Bookworm may not include the `fbcon` video driver — only `dummy`, `x11`, and `kmsdrm`. The current pygame kiosk.sh sets `SDL_VIDEODRIVER=fbcon`. If that backend isn't available, the pygame service silently fails at startup.

**Hardware-specific context (corrected after review):** the original Pi 1B (BCM2835) implements VideoCore IV but **does not** ship a V3D pipeline. The `vc4-kms-v3d` overlay targets V3D and typically **will not bind on Pi 1B**; the older `vc4-fkms-v3d` (firmware-KMS) may or may not yield a usable `/dev/dri/card0` depending on the kernel build. Treat `kmsdrm` on Pi 1B as low-confidence; expect the realistic fallback ladder to be `fbcon` (if available) → minimal X (xinit). The earlier draft's "Pi 1 supports the legacy vc4 driver" was incorrect.

### Probe (must do `set_mode` + `flip` on real hardware, not just `init`)

A driver that succeeds at `pygame.display.init()` may still fail at `set_mode` — the original probe gave false positives. Run on a real Pi 1B with HDMI attached:

```bash
python3 - <<'PY'
import os, pygame, time
for drv in ('fbcon','kmsdrm','x11','dummy'):
    os.environ['SDL_VIDEODRIVER'] = drv
    print('--- trying', drv, '---')
    try:
        pygame.display.init()
        scr = pygame.display.set_mode((1440, 900), pygame.FULLSCREEN)
        scr.fill((40, 20, 80))
        pygame.display.flip()
        time.sleep(1)
        print(drv, '→ OK driver=', pygame.display.get_driver())
    except Exception as e:
        print(drv, '→ FAIL', type(e).__name__, e)
    finally:
        try: pygame.display.quit()
        except Exception: pass
PY
```

For the `kmsdrm` branch, set `gpu_mem=128` in `/boot/config.txt`, enable `dtoverlay=vc4-fkms-v3d` (firmware-KMS, the variant most likely to work on Pi 1B), and verify `/dev/dri/card0` exists before declaring success.

Also log the active `/etc/default/keyboard` value for debug context on non-US installs.

### Decide and record

Record the outcome in `docs/sdl-backend.md` (committed alongside the rest of Phase 0):

- `fbcon` works on this Pi 1B Bookworm → keep current `kiosk.sh`.
- Only `kmsdrm` works (and `fbcon` doesn't) → switch `SDL_VIDEODRIVER=kmsdrm`; carry `gpu_mem=128` + `dtoverlay=vc4-fkms-v3d` into Phase 5's installer diff.
- Neither works → run pygame under minimal X (`xinit` + matchbox-window-manager, same X stack as the `--tkinter` mode). +~20 MB RSS overhead vs `fbcon`; Phase 1 perf wins preserved.

This phase ships as documentation only (the `docs/sdl-backend.md` record). Phase 5 cannot merge until this record exists.

## Phase 1 — Pygame perf fixes

Files touched: `hamclock_pygame.py` only.

The earlier audit flagged two per-frame allocations; the fresh audit found eight; review identified one more bottleneck (full-surface `display.flip()` memcpy) that's not allocation-related but is the actual click-latency limiter. Top 4 deliver >90 % of the win. Items 5–9 are Phase 1b cleanup.

### Top 4 (this phase)

**1. Cache scaled image surfaces.** `draw_image` at L180 calls `pygame.transform.smoothscale(surface, (nw, nh))` on every frame for every visible image (~20 large-surface allocations/sec, estimate).

Implementation: module-level `_scaled_cache: collections.OrderedDict` keyed by `(image_key, image_refresh_ts, (nw, nh))`. `image_refresh_ts` source: per-image `fetched_at` epoch second written by the data-layer's background fetcher when a new bytes blob lands (`CACHE[image_key].get('fetched_at', data.last_data_refresh)`). Eviction: `move_to_end` on hit, `popitem(last=False)` on overflow. Cap 16 — the dashboard has 5 image slots × 1 active scale each = 5; 16 leaves margin.

**2. Fix the loading-state Font allocation.** `draw_image` at L172 constructs `pygame.font.Font(None, 18)` every frame while images haven't loaded yet (~20/sec for the first ~10 s of boot, estimate). Add `'tiny': mk(14)` to `_make_fonts()`; replace the inline construction. Post-fix `Font(None, 18)` is constructed **zero** times after init.

**3. Glyph cache for `_blit_text`.** L81's `font.render(str(text), True, color)` runs an estimated ~700 times/sec on static labels (panel titles, column headers, "SFI", "BAND", "OPEN:" etc.).

Implementation: module-level `_glyph_cache: collections.OrderedDict` keyed by `(font_name_or_None, font_size, text, color)` — **explicitly NOT `id(font)`**. CPython reuses `id()` after GC; if `_make_fonts()` is ever re-invoked (resize, theme reload), stale glyphs would leak through. `_make_fonts()` clears `_glyph_cache` on every call. Eviction: same LRU pattern as `_scaled_cache`. Cap 256 entries — typical dashboard frame uses ~70 distinct labels; 256 leaves headroom for tab and data changes.

**4. Dirty-rect `display.update()` instead of full `display.flip()`.** Reviewer-identified bottleneck not present in the original audit. SDL2 on `fbcon` does a software memcpy of the full 1440×900×4 surface (~5 MB) on every `flip()` — bounded by Pi 1 memory bandwidth (~150–250 MB/s), this adds 20–35 ms per frame regardless of any caching above. Track changed panel rects in the main loop (a panel only redraws when its data updates, the second ticks over, or the active tab changes); call `pygame.display.update(rects)` with that list instead of `flip()`. Force a full `flip()` only at startup and on a tab click (so the new image fully repaints).

If Phase 0 lands on `kmsdrm` rather than `fbcon`, item 4 may be redundant — KMS pageflip avoids the memcpy. Verify in Phase 1 testing and downgrade item 4 to Phase 1b if so.

### Phase 1b (separately shippable, ships as one commit after Phase 5 lands)

5. Recompute layout `pygame.Rect`s only on screen-size change; cache in a `layout` dict.
6. `draw_band_activity` pre-allocated counts list instead of `{b: 0 for b in HF_BANDS}` per frame.
7. `draw_open_bands` cached `'OPEN: …'` / `'CLOSED: …'` strings keyed by `data.last_data_refresh`.
8. Status-bar / header / Kp string formatting cached by `(int(time.time()), data.last_data_refresh)`.
9. `solar_view` precompute snapshot to skip per-frame `_safe(data.solar, ...)` chains.

**Phase 1b acceptance:** re-run the Phase 1 monkey-patch harness; smoothscale and font.render call counts each drop by **≥ 50 % vs the Phase 1 baseline** on a 30-frame headless run.

### Verification (Phase 1)

- **Allocation harness (headless, `SDL_VIDEODRIVER=dummy`).** Monkey-patch `pygame.transform.smoothscale` and `pygame.font.Font.render` to count calls; run the loop 30 frames; assert smoothscale called ≤ (N_visible_images) times across the 30 frames, `Font(None, 18)` constructed **zero** times after init, glyph cache hit rate ≥ 90 % on the static-label set.
- **Click-latency micro-bench on a real Pi 1B** (gates Phase 5). Ship a `--inject-events <path>` debug flag (gated by `HAMCLOCK_DEBUG=1` env so it never runs in production — argparse errors with "debug builds only" otherwise) that reads a JSON list of pygame event dicts and posts them one-per-frame. Instrument the MOUSEBUTTONDOWN timestamp → screen-pixel-change timestamp for 100 injected clicks at random sub-frame offsets. Assert p99 ≤ 200 ms. If p99 exceeds 200 ms, escalate the propagation panel's redraw rate to 20 FPS or implement deeper dirty-rect work before Phase 5 ships.

## Phase 2 — Server-side MUF rasterize (gated by a pre-merge benchmark)

Files touched: `server.py`, `kiosk-install.sh` (apt install gated by `[ "$KIOSK_MODE" = "pygame" ]`), `offline-install.sh` (embedded server.py heredoc). Mirror the offline installer to `hamclock-reborn/public/downloads/pi1-install.sh` per the existing dual-repo rule.

### Pre-merge gate (real Pi 1B, before any code in Phase 2 lands)

The research-cited 8–20 s estimate has **no Pi-1B-armv6 measurement basis** — review estimates 30–90 s is plausible given the 1058-element SVG with `clipPath`s and embedded raster tiles on a 700 MHz single-core. The 45 s subprocess timeout designed below sits in the most-likely range, so without a measurement the "fallback" may be the default path. Run on a real Pi 1B (5 iterations, take median):

```bash
sudo apt install -y python3-cairosvg
for i in 1 2 3 4 5; do
  time python3 -c "import cairosvg; cairosvg.svg2png(url='https://prop.kc2g.com/renders/current/mufd-normal-now.svg', output_width=720, write_to='/tmp/m_$i.png')"
done
```

Decision rule:

- median ≤ 20 s → ship cairosvg path with default 45 s subprocess timeout.
- median 20–30 s → ship cairosvg path; raise subprocess timeout to `max(60, 3 × measured_median)`.
- **median > 30 s → do not ship cairosvg.** Skip to the BOM World I-Map GIF source (designed below as the fallback).

The decision is recorded in `docs/muf-source.md` alongside Phase 0's record.

### Installer change

Add to the pygame-mode apt install list only:

```bash
if [ "$KIOSK_MODE" = "pygame" ]; then
    sudo apt install -y python3-pygame python3-cairosvg
fi
```

Footprint: `python3-cairosvg` is arch:all (~179 KB); pulls `python3-cairocffi`, `python3-lxml` (armel native, ~4.27 MB installed), `python3-pil`. Worst-case new install footprint ~25–35 MB (estimate); often near-zero if cairo/pixbuf libs are already present.

### `server.py` change

```python
PHASE2_TIMEOUT_S = 45  # raised by installer if pre-merge measurement >20 s.

def _rasterize_muf(svg_bytes: bytes) -> bytes | None:
    """Render the KC2G MUF SVG to PNG in a subprocess so the multi-second
    render does not block the request thread or the background fetcher.

    output_width=720 because the MUF panel is ~720x420 in the 1440x900 layout;
    rendering at native panel width halves cairo's CPU cost vs. full screen.
    cairosvg.svg2png preserves aspect ratio when only output_width is given —
    the 1526x905 SVG becomes 720x427 PNG.

    cpulimit caps the subprocess to 50% of one core. nice -n 19 from the first
    draft would be ineffective on an idle single-core box because the render
    loop sleeps between 10 FPS frames; the cairosvg job gets the core anyway.
    cpulimit enforces a hard duty cycle.
    """
    try:
        p = subprocess.run(
            ['cpulimit', '-l', '50', '-q', '--',
             'python3', '-c',
             'import sys, cairosvg; cairosvg.svg2png('
             'bytestring=sys.stdin.buffer.read(), '
             'output_width=720, write_to=sys.stdout.buffer)'],
            input=svg_bytes, capture_output=True,
            timeout=PHASE2_TIMEOUT_S, check=True,
        )
        return p.stdout
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print('[muf] rasterize failed: %s' % e, file=sys.stderr)
        return None
```

Installer also adds `cpulimit` to the pygame-mode apt list.

`fetch_muf()` keeps `CACHE['muf_image']` as the SVG bytes (for browser compatibility) AND adds `CACHE['muf_image_png']` populated by `_rasterize_muf()` when the SVG refreshes. `/api/muf-map` serves the PNG when present, falls back to SVG when not. `Cache-Control: no-store` already applied by `send_binary`.

### Fallback

If the pre-merge gate fails (median > 30 s) or cairosvg becomes chronically slow post-deploy, swap the upstream source to `https://www.sws.bom.gov.au/Images/HF%20Systems/Global%20HF/Ionospheric%20Map/WorldIMap.gif` — 25 KB GIF, already raster, 562×576, 15-min cadence. Lower resolution (will look soft scaled to panel size) but a known-credible ham source. The native client decodes GIF natively via SDL_image.

### Observability

Subprocess stderr is logged via `print(..., file=sys.stderr)` and surfaces via `journalctl -u hamclock-lite`. No structured logging in v1. The README documents `journalctl -u hamclock-lite -u hamclock-kiosk` as the diagnostic entrypoint.

### Verification (Phase 2)

- **Pre-merge:** the timing gate above.
- **Post-deploy:** `curl -s -o /tmp/m.png http://localhost:8080/api/muf-map && file /tmp/m.png` → `PNG image data, 720 x 427`. Response size between 20 KB and 200 KB.

## Phase 3 — Themes (independently shippable)

File: `hamclock_pygame.py`.

Replace hardcoded color constants with a `THEMES` dict matching the browser palettes. The palette must enumerate **every** color used in any draw function: `bg`, `card`, `fg`, `muted`, `label`, `accent`, `good`, `fair`, `poor`, `band_palette` (list of 10 per-band colors for `draw_band_activity`), `sdo_accent` (SDO panel border tint).

Four themes:

- `'kstate'` (current default): K-State purple/violet — `bg=(42, 20, 80)`, `card=(58, 29, 101)`, accents preserve the existing K-State palette.
- `'classic'`: dark navy and steel grey.
- `'amber'`: dark brown background, amber accents.
- `'blue'`: deep blue, cyan accents.

Exact RGB tuples for all four palettes are copied verbatim from the browser dashboard's CSS variables (`hamclock-reborn/public/themes/*.css` and the in-tree `index.html` `<style>` block) into the implementation plan as part of Phase 3's deliverable. **This spec locks in only the schema** (key names) and the requirement that the four pygame palettes match the browser palettes exactly.

`main()` reads `settings.json['theme']`. **On any failure to load** (missing file, malformed JSON, missing key, unknown theme name), falls back to `'kstate'` and logs a warning to stderr. This makes Phase 3 ship-safe without Phase 4 — pygame mode keeps working with a default theme even if `settings.json` doesn't exist yet.

No runtime switcher in v1. To change theme: edit `settings.json` (or run `hamclock-setup --theme=...`) and `sudo systemctl restart hamclock-kiosk` (~1 s for pygame).

The wizard itself renders in `'kstate'` because the user hasn't picked a theme yet at that point.

### Verification (Phase 3)

For each theme name, point settings.json at that theme, render one frame headlessly with `SDL_VIDEODRIVER=dummy`, sample the BG pixel at `(0, sh//2)`, assert it matches `THEMES[name]['bg']`. Repeat with settings.json absent — assert pygame still renders, in `kstate`.

## Phase 4 — First-boot setup wizard

Files touched: `hamclock_pygame.py` (new `setup_screen()` GUI + `--setup-cli` argparse mode), `kiosk-install.sh` (creates `/etc/hamclock-lite/`, installs `hamclock-setup` wrapper), `offline-install.sh`. Mirror to `hamclock-reborn/public/downloads/pi1-install.sh`.

### Settings directory ownership and atomic writes

Installer creates the settings directory with explicit ownership during install:

```bash
sudo install -d -o $SERVICE_USER -g $SERVICE_USER -m 0755 /etc/hamclock-lite
```

This is where wizard, CLI, and dashboard all read/write. Owning the dir as `SERVICE_USER` lets the wizard write directly without `setuid` or `sudo`.

All writers (wizard and `hamclock-setup` CLI) use one shared helper:

```python
def write_settings(d: dict, path='/etc/hamclock-lite/settings.json'):
    """Atomic write. Both wizard (as SERVICE_USER) and CLI (sudo dropping to
    SERVICE_USER) call this. tempfile in the same directory so os.replace is
    a rename, not a cross-filesystem copy."""
    dirpath = os.path.dirname(path)
    tmp = os.path.join(dirpath, 'settings.json.tmp.%d' % os.getpid())
    with open(tmp, 'w') as f:
        json.dump(d, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    try:
        os.chown(path, SERVICE_UID, SERVICE_GID)
    except PermissionError:
        # Expected when wizard runs as SERVICE_USER and the dir is already
        # SERVICE_USER-owned — the file inherits the right ownership anyway.
        pass
```

Readers (`load_settings()`) tolerate a transient `JSONDecodeError` by retrying once after 200 ms before treating the file as missing — this dodges the race where a writer is mid-`os.replace`.

### UI layout (1440×900, centered 700×500 panel)

```
                  HAMCLOCK SETUP                      y=180
[Callsign]    [W1ABC___________________]              y=280
[Timezone]    [America/Chicago_________]              y=360
[Theme]                < kstate >                     y=440   (← → cycles)
                       [ Save ]                       y=540
              Tab to move, Enter to save              y=620
```

### `TextField` class — public surface

Full ~40-line implementation in the plan; the spec locks in the public surface:

```python
class TextField:
    def __init__(self, rect, initial='', max_len=32, validator=None, label=''):
        # rect: pygame.Rect
        # validator: callable(text) -> (ok: bool, error_msg: str) or None
        # label: rendered to the left of the box (or '' for none)
        ...
    text: str        # current contents
    cursor: int      # caret position (chars from left)
    error: str       # last validation error ('' if none, drives red border)
    def handle_event(self, ev) -> 'submit'|'next'|'cancel'|None: ...
    def draw(self, surface, theme, focused: bool): ...
```

### Key handling

- `pygame.key.set_repeat(400, 40)` set once after `pygame.init()` so held backspace works (standard SDL key-repeat defaults).
- **Skipped if the SDL driver is `x11`** (X already does key repeat; setting it again double-fires). Detect via `pygame.display.get_driver() == 'x11'`.
- Focus cycles with `Tab` / `Down` / `Shift+Tab` / `Up` across `[callsign, timezone, theme, save]`.
- `Enter` on Save → validate all fields → write JSON atomically → return to main loop. If any field has `error != ''`, refuse to save and focus the first invalid field.
- `Esc` → exit code 1 → kiosk.sh while-loop relaunches → wizard reappears.

### Validation rules (exact)

- **Callsign:** regex `^[A-Z0-9/]{3,10}$` is necessary but not sufficient. Additional rule: stripped of `/`, the result must be 3–8 characters, contain **at least one letter and at least one digit**. Uppercased on save.
  - Accepted: `W1ABC`, `W1ABC/P`, `KH6/W1ABC`, `W1ABC/QRP`, `K1A`.
  - Rejected: `///` (no letter or digit), `/W1` (too short stripped), `ABCDEF` (no digit), `123456` (no letter), `AB` (too short stripped).
- **Timezone:** must be a member of `zoneinfo.available_timezones()`. Bad input → red border, no save.
- **Theme:** cycled with arrow keys; cannot be invalid.

### `ntp` field

```json
{ "callsign": "W1ABC", "timezone": "America/Chicago",
  "theme": "kstate", "ntp": "" }
```

- `ntp` empty string → leave the system NTP configuration untouched.
- `ntp` non-empty → validated by `socket.gethostbyname` at save time; the installer (or `hamclock-setup --apply-ntp`) writes it to `/etc/systemd/timesyncd.conf.d/hamclock.conf`:
  ```ini
  [Time]
  NTP=<value>
  ```
  and runs `systemctl restart systemd-timesyncd`. The pygame client never reads this field — it is consumer-side configuration only.

### Locale / date / time (v1 fixed)

Wizard, dashboard, and log timestamps render in fixed formats: 24-hour `HH:MM:SS` for time, ISO `YYYY-MM-DD` for date. Locale-aware formats are explicitly out of scope for v1.

### `hamclock-setup` CLI

The headless config path is a thin shell wrapper installed by the installer:

```bash
# /usr/local/bin/hamclock-setup, mode 0755
#!/bin/sh
exec python3 /opt/hamclock-lite/hamclock_pygame.py --setup-cli "$@"
```

`hamclock_pygame.py` gains a `--setup-cli` argparse mode that accepts the same fields as flags:

```
hamclock-setup --callsign W1ABC --timezone America/Chicago --theme kstate [--ntp pool.ntp.org]
```

The CLI uses the same validators and the same `write_settings()` helper as the GUI wizard.

When invoked under `sudo` (`os.geteuid() == 0`), the CLI drops to `SERVICE_USER` via `os.setuid/setgid` before writing settings.json, so the resulting file is owned by the service user. This avoids the mixed-ownership race a naive sudo CLI would create.

### `--inject-events` debug flag

`hamclock_pygame.py` accepts `--inject-events <path>` to feed synthetic pygame events from a JSON list (one event per frame). **Gated by `HAMCLOCK_DEBUG=1` env** — argparse errors with "debug builds only" if `HAMCLOCK_DEBUG != '1'`. The installer never sets `HAMCLOCK_DEBUG`; this is a dev-only test affordance, not a shipped feature. Used by Phase 1 click-latency bench and Phase 4 wizard verification.

### Wizard caveats from research

- Caps Lock and shift are honored by SDL on fbcon/kmsdrm; no special handling needed.
- `pygame.scrap` (clipboard) is NOT available without X. No paste support — acceptable for one-time setup.
- USB keyboard layout follows `/etc/default/keyboard`. Phase 0 probe logs the active value for debugging non-US installs. ASCII-only fields (callsign, tz) dodge layout surprises.
- `pygame.mouse.set_visible(False)` in the wizard; keyboard-only.
- Wizard waits up to 10 s for `timedatectl show -p NTPSynchronized=yes` before its first save; otherwise proceeds with a stderr warning that the file mtime may be wrong.

### Verification (Phase 4)

- Delete `settings.json`; launch pygame with `HAMCLOCK_DEBUG=1` and `--inject-events events.json` containing the synthetic sequence: `"W1ABC"`, Tab, `"America/Chicago"`, Tab, Right (theme cycle), Tab, Enter; assert the written JSON matches expected, mode 0644, owned by `SERVICE_USER`.
- Invalid-timezone test: inject `"Atlantis/Lost"`; assert tz field shows red border and Save is rejected.
- Callsign edge cases: `W1ABC/P` accepted; `///` rejected; `ABCDEF` rejected; `123456` rejected.
- CLI test: `sudo hamclock-setup --callsign W1ABC --timezone UTC --theme kstate` writes settings.json with correct contents, mode 0644, owned by `SERVICE_USER` (not root).
- Concurrency test: two simultaneous `hamclock-setup` runs with different values — last writer wins, file parses cleanly.

## Phase 5 — Flip the default

Files touched: `kiosk-install.sh`, `offline-install.sh`, `README.md`. Mirror to `hamclock-reborn/public/downloads/pi1-install.sh`.

```diff
-KIOSK_MODE="browser"  # default
+KIOSK_MODE="pygame"   # default — native client, no browser, ≤200ms p99 clicks
```

Plus any installer changes Phase 0 mandated (`SDL_VIDEODRIVER` line, `/boot/config.txt` overlay, `gpu_mem` setting, apt package list). **Phase 5 cannot merge until `docs/sdl-backend.md` and `docs/muf-source.md` exist with Phase 0's and Phase 2's recorded decisions.**

`--browser` and `--tkinter` flags remain available. README's "Display Modes" section is rewritten so pygame is the default and browser/tkinter become opt-in alternatives.

### Upgrade behavior

The installer adds reinstall detection:

- If `/etc/hamclock-lite/settings.json` already exists → leave it untouched; do NOT auto-launch the wizard on next boot.
- If a pre-existing pygame service is detected but `settings.json` is absent (a Pi 1 running the OLD pygame mode) → write a default `settings.json` (`theme=kstate`, `callsign=''`, `timezone=UTC`, `ntp=''`) and print: `Run 'sudo hamclock-setup' to personalize your settings.` The wizard does NOT auto-launch.
- Truly fresh install (no service unit yet) → wizard auto-launches on first boot.

### Rollback procedure (documented in README under "Reverting to browser mode")

```bash
cd ~/hamclock-pi1 && sudo ./kiosk-install.sh --browser
```

Re-runs the installer in browser mode on the existing box, swapping the kiosk service unit and leaving `/etc/hamclock-lite/settings.json` in place for a later switch back.

### Browser localStorage migration

Out of scope for v1. The installer, when switching an existing browser-mode box to pygame, prints a one-time notice that browser localStorage (theme, callsign) is not migrated and the user should re-enter via wizard or `hamclock-setup`. README documents this caveat.

### Verification (Phase 5)

- `bash -n` on both installers.
- Install in a fresh Bookworm armv6 container/VM with no flags; confirm `systemctl status hamclock-kiosk` is active and `journalctl -u hamclock-kiosk` shows pygame client log lines, not a browser process.
- Re-install with `--browser` on the same VM and confirm the browser path still works (rollback test).
- Reinstall over an existing settings.json — confirm the wizard does NOT appear.
- p99 click-latency micro-bench on real Pi 1B reproduces Phase 1 result.

## Data flow per click

```
USB keyboard / mouse ──► SDL ──► pygame.event.MOUSEBUTTONDOWN
                                        │
                       up to one frame period (~100 ms at 10 FPS) before
                       the loop's next event.get() picks it up
                                        ▼
                            active_tab = clicked tab
                                        │
                       next render frame (~100 ms) builds the dirty rects
                                        ▼
                       display.update(rects) — only the changed panels are
                       blitted to fbdev (or pageflip on kmsdrm)
                                        │
                                        ▼
                       photons on screen
```

Worst case = ~100 ms event-poll latency + ~100 ms frame draw = ~200 ms. No network, no DOM, no layout.

## Error handling

- **MUF rasterize failure** → fall through to SVG; native client shows "MUF UNAVAILABLE" text panel for the one slot. Other panels unaffected.
- **`settings.json` missing or invalid** at any boot → re-enter wizard (Phase 4 behavior) or render in default `kstate` theme (Phase 3 ship-alone behavior). Reader tolerates transient `JSONDecodeError` with 200 ms retry to avoid racing a concurrent writer.
- **Theme key absent or invalid** → fall back to `kstate`, log warning to stderr.
- All crash guards from the prior session stay: render-loop try/except with consecutive-error counter (per-session, resets on a successful render), kiosk.sh while-loop relaunch, systemd `Restart=always` + `StartLimitIntervalSec=0` + `OOMScoreAdjust=-250` (all carried from prior crash-resilience work).
- **Degraded-window behavior during the 15-error backoff.** On each caught `pygame.error` the loop:
  1. Sleeps 100 ms; total per-retry SDL backoff capped at 500 ms so the worst-case degraded window is ≤ 7.5 s (well below systemd's kiosk restart cooldown).
  2. Renders a solid theme-bg fill with a `RECOVERING…` label centered on screen, so the user never sees the bare console or a frozen partial frame.

  After 15 consecutive errors the process exits with code 1; kiosk.sh restarts it with a fresh SDL context.

## Risks (ranked)

1. **SDL backend selection on real Pi 1B Bookworm is unverified.** Mitigation: Phase 0 probe with proper `set_mode` + `flip`. Acceptance: at least one backend reaches `flip` without exception. Worst case: minimal X fallback (+~20 MB RSS).
2. **cairosvg render time on Pi 1B armv6 is unknown.** Research-cited 8–20 s has no measurement basis; review estimates 30–90 s. Mitigation: pre-merge benchmark gate. If median > 30 s, ship BOM World I-Map GIF path. The fallback is fully designed.
3. **End-to-end click latency cannot reach 100 ms at 10 FPS.** Target rewritten as ≤ 200 ms p99 (one frame period alone is 100 ms). If measured p99 exceeds 200 ms, escalate the propagation panel to 20 FPS or implement deeper dirty-rect.
4. **Memory budget violated by cairosvg subprocess transient.** Cap raised to ≤ 90 MB pygame steady-state + ≤ 160 MB combined; rasterize transient (~25–30 MB) excluded from steady-state. `cpulimit` caps the subprocess duty so it doesn't both consume RAM and starve the render loop.
5. **First-boot wizard requires a USB keyboard.** Mitigation: `hamclock-setup` CLI for headless config; README documents both paths.
6. **Theme palette contrast on the actual HDMI display.** Pi 1 + HDMI + fbdev / kmsdrm renders colors differently than a dev monitor. Check all four themes on the target before declaring Phase 3 done.

## Out of scope (deliberately)

- Tkinter client (left as opt-in via `--tkinter`; only the installer default flips).
- Browser dashboard (left as opt-in via `--browser`).
- Multi-resolution support beyond the existing auto-scale.
- Runtime theme switcher UI in pygame mode (edit + restart is enough).
- Pi 2+ specific optimizations (those Pis run `hamclock-reborn` proper).
- Locale-aware date/time formatting.
- High-contrast accessibility theme. (May be added in v2.)
- Browser localStorage → pygame settings migration.
- Structured / JSON logging.

## Fallback decision tree

```
Phase 0 SDL probe (real Pi 1B, set_mode + flip, not just init):
   fbcon works?
   ├─ yes → keep current kiosk.sh
   ├─ no, kmsdrm works → switch SDL_VIDEODRIVER, set gpu_mem=128 and
   │                     dtoverlay=vc4-fkms-v3d
   └─ neither works (most likely outcome on Pi 1B) → run pygame under minimal X
                                                     (still ~50 MB win vs browser)

Phase 2 pre-merge cairosvg benchmark (real Pi 1B, 5 runs):
   median ≤ 20 s → ship as designed (45 s subprocess timeout)
   ├─ median 20-30 s → ship; raise timeout to max(60, 3 × median)
   └─ median > 30 s → DO NOT ship cairosvg; swap to BOM World I-Map GIF source

Phase 1 click-latency bench on real Pi 1B (100 injected clicks):
   p99 ≤ 200 ms → ship
   ├─ p99 200-500 ms → raise propagation panel to 20 FPS, re-measure
   └─ p99 > 500 ms → escalate; deeper dirty-rect implementation
```

## Implementation order

1. **Phase 0** — knowledge change only; produces `docs/sdl-backend.md`. Blocks Phase 5.
2. **Phase 1** — pygame mode (opt-in) becomes snappy. Browser default unchanged.
3. **Phase 2** — pygame mode gains the MUF visual (gated on pre-merge benchmark); produces `docs/muf-source.md`. Browser path unaffected.
4. **Phase 3** — pygame mode reaches theme parity. Ship-safe even without Phase 4 (default kstate fallback).
5. **Phase 4** — first-boot wizard self-contained; `hamclock-setup` CLI shipped as sub-deliverable.
6. **Phase 5** — default install now uses the polished pygame stack. Carries any installer changes Phase 0 and Phase 2 mandated. Reinstall detection preserves user settings; rollback documented.

Phase 1b (perf-fix cleanup items 5–9) ships as one commit AFTER Phase 5 lands; acceptance ≥ 50 % drop in smoothscale / font.render call counts vs Phase 1 baseline.

Mid-phase upgraders keep a working system; only Phase 5 changes the default.

## Sources of facts cited in this spec

- **Research workflow `wf_d3454b75-c50`** (2026-06-03): `python3-cairosvg` apt availability on Bookworm armel; KC2G SVG measurements (376 565 bytes, 1058 elements, 1526×905 px); BOM World I-Map verification; pygame text-input patterns and fbcon caveats; per-frame allocation audit of current `hamclock_pygame.py` (8 ranked items).
- **Review workflow `wf_8da41a43-b53`** (2026-06-03): 5-lens spec review (HW feasibility, implementer clarity, completeness, hygiene, adversarial refuter) plus 5 adversarial verifiers. Major spec changes driven by this review: corrected latency math, expanded RSS budget, pre-merge cairosvg benchmark gate, ship-safe Phase 3 fallback, atomic settings write helper, settings dir ownership setup, rollback procedure, reinstall detection, `hamclock-setup` CLI form, callsign edge-case rules, NTP semantics, dirty-rect `display.update()` path, `cpulimit` instead of `nice`, render-loop degraded-window behavior, full TextField public surface.
- **Earlier crash-to-CLI investigation** (same session): `Restart=always`, `StartLimitIntervalSec=0`, `OOMScoreAdjust=-250` (carried in), kiosk.sh while-loop relaunch, render-loop try/except + consecutive-error counter.
- **Memory:** `project_hamclock_pi1_deployment.md` (1440×900 HDMI kiosk is the primary target), `project_pi1_installer_dual_repo.md` (mirror to `hamclock-reborn/public/downloads/pi1-install.sh`).
