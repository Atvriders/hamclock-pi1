# HamClock Pi1 — Ham Radio Dashboard for Raspberry Pi 1

A lightweight ham radio dashboard that runs on the oldest Raspberry Pi. Shows solar conditions, HF band conditions, and DX cluster spots — the same data as [HamClock Reborn](https://github.com/Atvriders/hamclock-reborn), but designed to run on hardware with just 512MB RAM and a 700MHz processor.

**No build tools. No npm. No Node.js. Just Python 3 and a web browser.**

---

## What You Need

- A **Raspberry Pi** (any model — designed for Pi 1, works on all)
- A **microSD card** (8GB or larger)
- A **power supply** for your Pi
- An **Ethernet cable** or **Wi-Fi adapter** (Pi needs internet)
- A **computer** on the same network to view the dashboard in a web browser
- About **15 minutes** of your time

---

## Step-by-Step Installation

### Step 1: Install Raspberry Pi OS on the SD Card

If your Pi already has Raspberry Pi OS installed and you can connect to it, skip to Step 3.

1. On your regular computer, download the **Raspberry Pi Imager** from:
   - https://www.raspberrypi.com/software/
2. Insert your microSD card into your computer
3. Open Raspberry Pi Imager
4. Click **"Choose OS"** → select **"Raspberry Pi OS (other)"** → select **"Raspberry Pi OS Lite (32-bit)"**
5. Click **"Choose Storage"** → select your SD card
6. Click the **gear icon** (⚙️) to open settings:
   - **Enable SSH** — check the box
   - **Set username and password** — pick something you'll remember (example: username `pi`, password `hamclock`)
   - **Configure Wi-Fi** — enter your Wi-Fi name and password (if not using Ethernet)
7. Click **"Write"** and wait for it to finish
8. Remove the SD card from your computer

### Step 2: Boot the Pi

1. Insert the SD card into your Raspberry Pi
2. Connect the Ethernet cable (if not using Wi-Fi)
3. Connect the power supply — the Pi will boot automatically
4. Wait about **60 seconds** for it to start up

### Step 3: Find Your Pi's IP Address

You need to know the Pi's IP address to connect to it. Try one of these:

**Option A — Check your router:**
Log into your router's admin page (usually `192.168.1.1`) and look for a device named `raspberrypi`.

**Option B — Use your computer's terminal:**
```
ping raspberrypi.local
```
If it responds, your Pi's address is shown (like `192.168.1.42`).

**Option C — If you have a monitor connected to the Pi:**
Log in and type:
```
hostname -I
```
It will show the IP address.

Write down the IP address — you'll need it.

### Step 4: Connect to Your Pi via SSH

On your computer, open a terminal (or PuTTY on Windows) and type:

```bash
ssh pi@YOUR_PI_IP_ADDRESS
```

Replace `YOUR_PI_IP_ADDRESS` with the actual address (like `192.168.1.42`).

Example:
```bash
ssh pi@192.168.1.42
```

It will ask for your password — type the one you set in Step 1.

If it asks "Are you sure you want to continue connecting?" type `yes` and press Enter.

You should now see a command prompt like:
```
pi@raspberrypi:~ $
```

### Step 5: Update the Pi (Recommended)

Type these commands one at a time, pressing Enter after each:

```bash
sudo apt update
```
Wait for it to finish (may take a few minutes).

```bash
sudo apt upgrade -y
```
Wait for it to finish (may take several minutes on Pi 1).

### Step 6: Make Sure Python 3 and Git Are Installed

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

### Step 7: Download HamClock Pi1

Type:
```bash
git clone https://github.com/Atvriders/hamclock-pi1.git
```

Then go into the folder:
```bash
cd hamclock-pi1
```

### Step 8: Run the Installer

Type:
```bash
chmod +x install.sh
./install.sh
```

The installer will:
- Copy the files to `/opt/hamclock-lite/`
- Create a system service that starts automatically on boot
- Start the dashboard

You should see:
```
=== Installation Complete ===
HamClock Lite is running at: http://192.168.1.42:8080
```

### Step 9: Open the Dashboard

On any computer, phone, or tablet on the same Wi-Fi network, open a web browser and go to:

```
http://YOUR_PI_IP_ADDRESS:8080
```

Example:
```
http://192.168.1.42:8080
```

**You should see the HamClock dashboard with solar data, band conditions, and DX spots!**

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

---

## Re-Installing / Updating

To update to the latest version:
```bash
cd ~/hamclock-pi1
git pull
./install.sh
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
- On the Pi (if you have a monitor): type `hostname -I`
- On your router's admin page: look for `raspberrypi`
- On your computer: try `ping raspberrypi.local`

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

install.sh   — Sets up a systemd service so HamClock starts on boot
```

Total memory usage: ~15MB. Works on Pi 1 (700MHz, 512MB RAM) and up.

---

*Part of [HamClock Reborn](https://github.com/Atvriders/hamclock-reborn) — the full version for Pi 2 and newer.*
