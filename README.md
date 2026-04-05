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

| Section | Data | Updates |
|---------|------|---------|
| **Solar Conditions** | Solar Flux Index (SFI), Sunspot Number (SSN), A-Index, K-Index with color bar, X-Ray flux class, Solar Wind speed, Bz magnetic field, Geomagnetic storm level | Every 5 minutes |
| **HF Band Conditions** | 80m through 10m bands, Day and Night conditions, color-coded (Green=Good, Yellow=Fair, Red=Poor) | Every 5 minutes |
| **DX Cluster** | Last 30 DX spots from HamQTH, with frequency, band badge, DX callsign, spotter callsign, time, and comment | Every 2 minutes |
| **Clocks** | UTC time and your local time, updating every second | Every second |

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
server.py    — A tiny Python web server (no dependencies beyond Python 3)
               Fetches data from ham radio APIs every 2-5 minutes
               Serves the dashboard page and JSON data on port 8080

index.html   — The dashboard page (HTML + CSS + JavaScript in one file)
               Fetches data from server.py every 30 seconds
               Displays everything with color coding and auto-refresh

install.sh       — Sets up the web server service (headless mode)
kiosk-install.sh — Sets up the web server + fullscreen display on the Pi's monitor
```

Total memory usage: ~15MB. Works on Pi 1 (700MHz, 512MB RAM) and up.

---

## Alternative: Offline Install (No GitHub Needed)

If GitHub is blocked on your network (common on college/school networks), you can install from a USB drive:

### On any computer with internet access:

1. Download the offline installer: [offline-install.sh](https://github.com/Atvriders/hamclock-pi1/raw/master/offline-install.sh)
2. Copy `offline-install.sh` to a USB drive

### On the Raspberry Pi:

1. Plug in the USB drive
2. Mount it:
   ```bash
   sudo mkdir -p /mnt/usb
   sudo mount /dev/sda1 /mnt/usb
   ```
3. Run the installer:
   ```bash
   bash /mnt/usb/offline-install.sh
   ```
4. Unplug the USB drive when done

This installs HamClock without needing git or GitHub access. The Pi still needs internet to fetch ham radio data (solar conditions, DX spots) — just not GitHub specifically.

**Note:** For kiosk mode (fullscreen display), the Pi needs internet access to install the display server packages via `apt`. The offline installer only covers the headless (browser-access) mode.

---

## Alternative: Headless Mode

If you don't have a monitor connected to the Pi, you can run HamClock in headless mode instead. In Step 4, use `./install.sh` instead of `./kiosk-install.sh`:

```bash
chmod +x install.sh
./install.sh
```

This skips the display server and kiosk setup. The dashboard runs as a web server only — access it from any browser on the same network at `http://YOUR_PI_IP_ADDRESS:8080`.

---

*Part of [HamClock Reborn](https://github.com/Atvriders/hamclock-reborn) — the full version for Pi 2 and newer.*
