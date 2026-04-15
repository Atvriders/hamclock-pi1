# HamClock Pi1 — Ham Radio Dashboard for Raspberry Pi 1

A lightweight ham radio dashboard that runs on the oldest Raspberry Pi. The Pi boots directly into the dashboard on its own screen — no desktop, no login, just HamClock fullscreen. Shows solar conditions, HF band conditions, and DX cluster spots — the same data as [HamClock Reborn](https://github.com/Atvriders/hamclock-reborn), but designed to run on hardware with just 512MB RAM and a 700MHz processor.

**No build tools. No npm. No Node.js. Just Python 3 and a display.**

---

## What You Need

- A **Raspberry Pi** running Raspberry Pi OS (any model — designed for Pi 1, works on all)
- An **internet connection** (Ethernet or Wi-Fi)
- A **monitor/TV** connected to the Pi (HDMI or composite)
- A **keyboard** (for typing the install commands)
- About **10 minutes** of your time

---

## Installation

These steps assume your Pi is already running Raspberry Pi OS and you're logged in (either via SSH or directly on the Pi with a keyboard).

### Step 1: Update the Pi

Type these commands one at a time, pressing Enter after each:

```bash
sudo apt update
```
Wait for it to finish (may take a few minutes).

```bash
sudo apt upgrade -y
```
Wait for it to finish (may take several minutes on Pi 1).

### Step 2: Make Sure Python 3 and Git Are Installed

Type:
```bash
python3 --version
```
You should see something like `Python 3.11.2`. If you get an error, install it:
```bash
sudo apt install python3 -y
```

Type:
```bash
git --version
```
You should see something like `git version 2.39.2`. If you get an error, install it:
```bash
sudo apt install git -y
```

### Step 3: Download HamClock Pi1

Type:
```bash
git clone https://github.com/Atvriders/hamclock-pi1.git
```

Then go into the folder:
```bash
cd hamclock-pi1
```

### Step 4: Run the Kiosk Installer

Type:
```bash
chmod +x kiosk-install.sh
./kiosk-install.sh
```

The installer will:
- Copy the files to `/opt/hamclock-lite/`
- Create a system service that starts automatically on boot
- Install a minimal display server (no full desktop environment)
- Install the lightest available browser (`surf`, `midori`, or `chromium` as fallback)
- Auto-launch HamClock fullscreen on boot
- Hide the mouse cursor after 3 seconds
- Disable screen blanking so the display stays on 24/7
- Start the dashboard

### Step 5: Your Dashboard Is Live

The dashboard will appear fullscreen on your Pi's monitor — no desktop, just HamClock.

It also works from any other device on the same network. Open a browser and go to:

```
http://YOUR_PI_IP_ADDRESS:8080
```

To find your Pi's IP address, type `hostname -I` on the Pi.

**After a reboot, HamClock starts automatically — no need to log in or type anything.**

---

## What the Dashboard Shows

The dashboard is a 3-column kiosk layout designed for 1440×900 monitors but scales to any resolution.

### Left column

| Panel | Data | Updates |
|-------|------|---------|
| **SOLAR** | Solar Flux Index (SFI), K-index with color bar, Sunspot Number (SSN), A-index, X-Ray flux class, Solar Wind speed, Bz magnetic field, Geomagnetic storm level, Signal-to-Noise, Aurora index, foF2 | Every 5 minutes |
| **BANDS** | 80m–10m HF band conditions, Day and Night, color-coded (Green=Good, Yellow=Fair, Red=Poor) | Every 5 minutes |
| **SDO IMAGE** | Live NASA SDO solar disk (HMI continuum), large enough to spot active regions | Every 15 minutes |
| **GEOMAGNETIC** | Kp index gauge with color bar and storm-level label | Every 5 minutes |
| **X-RAY FLUX** | GOES X-ray class (A/B/C/M/X) with intensity bar | Every 5 minutes |
| **OPEN BANDS** | Quick summary of which HF bands are currently OPEN vs CLOSED | Every 5 minutes |

### Center column

| Panel | Data | Updates |
|-------|------|---------|
| **MUF MAP** | KC2G real-time global Maximum Usable Frequency map with terminator and propagation contours | Every 15 minutes |

### Right column

| Panel | Data | Updates |
|-------|------|---------|
| **DX SPOTS** | Last 5 HF DX spots from HamQTH with frequency, band badge, DX callsign, spotter, and UTC time | Every 2 minutes |
| **BAND ACTIVITY** | HF-only horizontal bar chart of spot counts per band (160m–10m), auto-sized to fit all 10 HF bands | Every 2 minutes |
| **PROPAGATION (tabbed)** | Click **DRAP** / **AURORA** / **ENLIL** to switch between three space-weather images: NOAA D-Region Absorption global map (default), Northern-Hemisphere Aurora forecast, and WSA-Enlil solar wind animation | Every 15 minutes |

### Header & footer

| Element | Content |
|---------|---------|
| **Header** | HAMCLOCK LITE title, your callsign (click to reopen settings), UTC clock, local clock (timezone-aware), connection status dot |
| **Status bar** | Solar / Bands / DX data ages so you can see freshness at a glance |

---

## Useful Commands

Check if HamClock is running:
```bash
sudo systemctl status hamclock-lite
```

Restart HamClock:
```bash
sudo systemctl restart hamclock-lite
```

Stop HamClock:
```bash
sudo systemctl stop hamclock-lite
```

View live logs:
```bash
journalctl -u hamclock-lite -f
```

Run manually (without the service):
```bash
cd /opt/hamclock-lite
python3 server.py
```

Restart the kiosk display:
```bash
sudo systemctl restart hamclock-kiosk
```

Stop the kiosk display:
```bash
sudo systemctl stop hamclock-kiosk
```

Disable kiosk mode (go back to CLI):
```bash
sudo systemctl disable hamclock-kiosk && sudo systemctl stop hamclock-kiosk
```

Re-enable kiosk mode:
```bash
sudo systemctl enable hamclock-kiosk && sudo systemctl start hamclock-kiosk
```

---

## Re-Installing / Updating

To update to the latest version:
```bash
cd ~/hamclock-pi1
git pull
./kiosk-install.sh
```

Running the installer again is safe — it will update the files and restart the service.

---

## Troubleshooting

**"Connection refused" when opening the browser:**
- Make sure the Pi is turned on and connected to the network
- Check the service is running: `sudo systemctl status hamclock-lite`
- Make sure you're using port `8080` in the URL

**"No data available" on the dashboard:**
- The Pi needs an internet connection to fetch ham radio data
- Check internet: `ping google.com` (press Ctrl+C to stop)
- Check the logs: `journalctl -u hamclock-lite -f`

**Dashboard loads but shows stale data:**
- The data auto-refreshes every 2-5 minutes
- Try refreshing the page in your browser (Ctrl+R or F5)

**Forgot the Pi's IP address:**
- On the Pi: press Ctrl+Alt+F2 to get a terminal, log in, type `hostname -I`
- On your router's admin page: look for a device named `raspberrypi`
- On another computer: try `ping raspberrypi.local`

---

## Data Sources

All data comes from free public APIs — no API keys or accounts needed:

- [HamQSL](https://www.hamqsl.com/solarxml.php) — Solar indices and HF band conditions
- [HamQTH](https://www.hamqth.com/) — DX cluster spots
- [HA8TKS](https://www.ha8tks.hu/) — DX cluster fallback

---

## How It Works

```
server.py    — Tiny Python 3 stdlib web server (zero pip dependencies)
               Background thread fetches ham radio APIs on staggered intervals
               (solar 5min, DX 2min, images 15min) and caches results in memory.
               Serves JSON + image proxies on port 8080.

index.html   — Single-file dashboard (HTML + CSS + vanilla JS, no build tools)
               Polls /api/* every 60 seconds, fully fluid vw/vh/clamp() layout.
               First-run setup wizard collects callsign + timezone + theme,
               persisted to localStorage. Click your callsign to re-open it.

install.sh       — Sets up the web server service (headless mode, no display)
kiosk-install.sh — Sets up server + fullscreen kiosk on the Pi's monitor
                   (installs minimal X11 + lightest available browser:
                   surf, midori, or chromium as fallback)
offline-install.sh — Self-contained bundle of all the above; safe for
                     `curl ... | bash` (everything runs inside main()).
```

**Tab interaction**: Click **DRAP** / **AURORA** / **ENLIL** in the bottom-right propagation panel to switch between the three space-weather images. All three refresh on the same 15-minute cycle so switching tabs always shows fresh data.

**Themes**: 4 built-in color themes (Classic, Amber, Blue, Red). Each theme paints the header callsign in its own accent color so your callsign stands out from the rest of the UI. Click your callsign in the header to reopen the setup wizard and switch themes anytime.

**NTP auto-detect**: Leave the "Time Server (NTP)" field blank in the setup wizard and the dashboard will auto-detect the Raspberry Pi's actual NTP server via `/api/ntp` (which probes `timedatectl`, `systemd-timesyncd.conf`, `chrony.conf`, and `ntp.conf` in order). The placeholder in the input shows the detected server so you know what you'll get.

**Total memory usage: ~15MB**. Works on Pi 1 (700MHz, 512MB RAM) and up. Designed for 1440×900 monitors but auto-scales to any resolution via EDID.

---

## Alternative: Quick Install (No GitHub Needed)

If GitHub is blocked on your network (common on college/school networks), you can install with one command directly from hamclock-reborn.org:

```bash
curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash
```

Or if `curl` isn't installed:
```bash
wget -qO- https://hamclock-reborn.org/downloads/pi1-install.sh | bash
```

This downloads and runs a self-contained installer that has everything embedded — no git needed. The Pi still needs internet to fetch ham radio data (solar conditions, DX spots), just not GitHub specifically.

The curl-pipe installer also accepts the same display-mode flags as `kiosk-install.sh` (see [Display Modes](#display-modes-browser-vs-native) below):

```bash
# Default (browser kiosk)
curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash

# Pygame framebuffer mode (no X11, lowest RAM)
curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash -s -- --pygame

# Tkinter native widget mode
curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash -s -- --tkinter
```

Note the `bash -s -- --pygame` pattern: `-s` tells bash to read the script from stdin (the curl output), and `--` separates bash's own arguments from the arguments passed through to the script.

---

## Alternative: Headless Mode

If you don't have a monitor connected to the Pi, you can run HamClock in headless mode instead. In Step 4, use `./install.sh` instead of `./kiosk-install.sh`:

```bash
chmod +x install.sh
./install.sh
```

This skips the display server and kiosk setup. The dashboard runs as a web server only — access it from any browser on the same network at `http://YOUR_PI_IP_ADDRESS:8080`.

---

## Display Modes: Browser vs Native

The default kiosk installer runs a browser (surf/midori/chromium) fullscreen on the Pi's HDMI monitor at **1440×900**. That works great and gives you the full feature set (all 5 themes, setup wizard, MUF map), but a browser on Pi 1 uses 30–80 MB of RAM and takes 3–10 seconds to start.

For lower overhead, `kiosk-install.sh` supports two alternative **native display modes** that replace the browser entirely while still rendering on the same HDMI monitor at 1440×900. All three modes talk to the same `server.py` (no server changes) — they just swap out the display layer. Pick the mode at install time with a flag:

| Flag | Display layer | RAM | Needs X11 | Best for |
|---|---|---|---|---|
| `--browser` (default) | surf/midori/chromium | 30–80 MB | Yes | Full feature set |
| `--tkinter` | Python Tkinter widgets | ~25–35 MB | Yes | Native widget feel |
| `--pygame` | Pygame → /dev/fb0 | ~15–20 MB | **No** | Lowest RAM, no X11 overhead |

Whichever mode you pick, the installer sets up the `hamclock-lite` and `hamclock-kiosk` systemd services so the Pi boots straight into the dashboard on its HDMI display — no manual launching after install.

### Option A — Pygame framebuffer client (`--pygame`)

The lowest-overhead option. Uses Pygame to draw directly to `/dev/fb0` via SDL's `fbcon` driver — skips X11 entirely on the Pi 1. Target footprint: ~15–20 MB RAM, <1 second startup.

Install with the `--pygame` flag:

```bash
cd ~/hamclock-pi1
./kiosk-install.sh --pygame
```

This installs `python3-pygame` (instead of the X11 + browser packages), copies the Python clients to `/opt/hamclock-lite/`, sets up the `hamclock-lite` and `hamclock-kiosk` systemd services, and configures the Pi to boot directly into the native Pygame client on the HDMI display. Reboot once and you're live.

**Testing manually** (without systemd, e.g. to debug rendering):

```bash
python3 /opt/hamclock-lite/hamclock_pygame.py
```

Behavior:
- Renders directly to `/dev/fb0` via SDL's `fbcon` driver — no X needed
- If `$DISPLAY` happens to be set, falls back to an X window (1440×900 or fullscreen)
- Press **Esc** or **Q** to quit
- Click the DRAP / AURORA / ENLIL tabs in the bottom-right panel to switch space-weather images
- Ticks at 10 FPS to keep CPU usage low

Known limitations vs the browser version:
- **No MUF map** (it's an SVG, and Pygame doesn't render SVG without extra dependencies). A text-based "MUF STATUS" panel showing FOF2, KP, SFI, SSN, and geomag is rendered in its place.
- Only the K-State color theme is hardcoded. No theme switching.
- No setup wizard — callsign is displayed from saved settings or left blank.

### Option B — Tkinter native client (`--tkinter`)

A stdlib-based alternative using Tkinter widgets. Slightly heavier than Pygame (~25–35 MB RAM) but uses native GUI widgets — tables via `ttk.Treeview`, tab switcher via `ttk.Notebook`, image panels via `PIL.ImageTk`. Boot time is comparable to Pygame.

Install with the `--tkinter` flag:

```bash
cd ~/hamclock-pi1
./kiosk-install.sh --tkinter
```

This installs `python3-tk` and `python3-pil.imagetk` (plus the minimal X11 stack — Tkinter needs an X server), sets up the same systemd services as the browser kiosk, and configures the Pi to boot directly into the native Tkinter client at 1440×900 fullscreen on the HDMI display.

**Testing manually** (without systemd):

```bash
python3 /opt/hamclock-lite/hamclock_tkinter.py
```

Behavior:
- Fullscreen at 1440×900 (press **F11** to toggle, **Esc** to quit)
- Three-column layout with SOLAR/BANDS/SDO/GEO/X-Ray/OPEN BANDS on the left, MUF STATUS in the middle, DX SPOTS/BAND ACTIVITY/PROPAGATION on the right
- Tab switching for DRAP/AURORA/ENLIL via `ttk.Notebook`
- BAND ACTIVITY drawn on a Canvas with per-band colored bars proportional to spot count
- If Pillow (`python3-pil`) isn't installed, text panels still work; image panels show "(PIL missing)"

Same MUF-map caveat as the Pygame client.

### Option C — Browser kiosk (`--browser`, default)

The original full-feature mode. If you don't pass any flag, this is what you get:

```bash
cd ~/hamclock-pi1
./kiosk-install.sh           # default — same as --browser
./kiosk-install.sh --browser  # explicit
```

Installs the minimal X11 stack (`xserver-xorg`, `xinit`, `matchbox-window-manager`) and the lightest available browser (`surf`, `midori`, or `chromium` as fallback) and boots the Pi straight into HamClock fullscreen at 1440×900.

### Which one should I use?

| | Browser (default) | Pygame client | Tkinter client |
|---|---|---|---|
| Install flag | `./kiosk-install.sh` | `./kiosk-install.sh --pygame` | `./kiosk-install.sh --tkinter` |
| RAM footprint | 30–80 MB | **~15–20 MB** | ~25–35 MB |
| Start time | 3–10 sec | **< 1 sec** | ~1 sec |
| X11 required | Yes | **No** (fbcon) | Yes |
| MUF map | ✅ (SVG) | ❌ (text replacement) | ❌ (text replacement) |
| Theme switching | ✅ (5 themes) | ❌ (K-State only) | ❌ (K-State only) |
| Setup wizard | ✅ | ❌ | ❌ |
| Full interactivity | ✅ | ✅ (tab clicks) | ✅ (tab clicks) |
| Packages installed | xserver-xorg xinit matchbox surf/midori/chromium | python3-pygame | python3-tk python3-pil.imagetk |

**If you want the lowest-RAM Pi 1 kiosk**: `./kiosk-install.sh --pygame`.
**If you want the richest display**: `./kiosk-install.sh` (browser default).
**If you want native GUI widgets on a Pi with X11 already running**: `./kiosk-install.sh --tkinter`.

### Switching modes after install

To switch display modes on an already-installed Pi, just re-run the installer with the new flag:

```bash
cd ~/hamclock-pi1
./kiosk-install.sh --pygame   # or --tkinter or --browser
```

Re-running the installer is safe — it rewrites the `kiosk.sh` launcher and the `hamclock-kiosk.service` unit with the new mode, restarts the service, and picks up immediately on the next boot (or with `sudo systemctl restart hamclock-kiosk`). All three Python client files are always copied to `/opt/hamclock-lite/` regardless of which mode is active, so switching doesn't require re-pulling the repo or reinstalling anything from `apt` you already have.

The `offline-install.sh` curl-pipe installer accepts the exact same flags — see the [Quick Install](#alternative-quick-install-no-github-needed) section above for the `bash -s -- --pygame` syntax.

### Shared data layer (`hamclock_data.py`)

Both native clients use a shared module `hamclock_data.py` that provides the `HamClockData` class. It polls the server's `/api/*` endpoints on a background thread (60-second data cadence, 15-minute image cadence) and caches results in memory. Third-party tools can use it directly:

```python
from hamclock_data import HamClockData
d = HamClockData('http://localhost:8080')
d.start_background()
# Read from d.solar, d.bands, d.dxspots, d.health, d.images
```

Python 3.9+ stdlib only — no dependencies beyond the standard library for the data layer itself.

---

*Part of [HamClock Reborn](https://github.com/Atvriders/hamclock-reborn) — the full version for Pi 2 and newer.*
