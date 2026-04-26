#!/bin/bash
# HamClock Pi1 — Kiosk Mode Installer
# Displays the dashboard fullscreen on the Pi's own monitor

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse mode flag
KIOSK_MODE="browser"  # default
for arg in "$@"; do
    case "$arg" in
        --pygame)  KIOSK_MODE="pygame" ;;
        --tkinter) KIOSK_MODE="tkinter" ;;
        --browser) KIOSK_MODE="browser" ;;
        --help|-h) echo "Usage: $0 [--browser|--pygame|--tkinter]"; exit 0 ;;
        *) echo "Unknown arg: $arg (try --help)"; exit 1 ;;
    esac
done
echo "Kiosk mode: $KIOSK_MODE"

echo "=== HamClock Pi1 Kiosk Mode Installer ==="
echo "This will set up your Pi to boot directly into HamClock on its monitor."
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required. Run: sudo apt install python3"
    exit 1
fi

# Check internet connectivity
if ! ping -c 1 -W 3 google.com &>/dev/null && ! ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    echo "ERROR: No internet connection detected."
    echo "Please connect the Pi to the internet and try again."
    exit 1
fi

# Install minimal X server + lightweight browser
echo "Installing display server and browser (this may take 15-30 minutes on a Pi 1)..."
sudo apt update
sudo apt install -y curl unclutter

# X11 packages needed for browser and tkinter modes, not pygame
if [ "$KIOSK_MODE" != "pygame" ]; then
    sudo apt install -y xserver-xorg xinit x11-xserver-utils matchbox-window-manager xfonts-base dmz-cursor-theme
fi

# Mode-specific dependencies
if [ "$KIOSK_MODE" = "pygame" ]; then
    echo "Installing Pygame for native framebuffer display..."
    sudo apt install -y python3-pygame
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    echo "Installing Tkinter + PIL for native widget display..."
    sudo apt install -y python3-tk python3-pil python3-pil.imagetk
fi

# Allow any user to start X (needed for systemd service)
sudo mkdir -p /etc/X11
sudo tee /etc/X11/Xwrapper.config > /dev/null <<XEOF
allowed_users=anybody
needs_root_rights=yes
XEOF

BROWSER=""
BROWSER_CMD=""
if [ "$KIOSK_MODE" = "browser" ]; then
    # Try browsers in order of lightness
    for pkg in surf epiphany-browser midori chromium-browser chromium; do
        if sudo apt install -y "$pkg" 2>&1 | tail -1; then
            case "$pkg" in
                surf) BROWSER="surf"; BROWSER_CMD="surf http://localhost:8080" ;;
                epiphany-browser) BROWSER="epiphany"; BROWSER_CMD="epiphany-browser --application-mode http://localhost:8080" ;;
                midori) BROWSER="midori"; BROWSER_CMD="midori -e Fullscreen -a http://localhost:8080" ;;
                chromium-browser|chromium) BROWSER="chromium"; BROWSER_CMD="$pkg --kiosk --noerrdialogs --disable-translate --no-first-run --disable-features=TranslateUI --disk-cache-size=0 http://localhost:8080" ;;
            esac
            break
        fi
    done

    if [ -z "$BROWSER" ]; then
        echo "ERROR: Could not install any browser (tried surf, epiphany, midori, chromium)."
        echo "Please install a browser manually and re-run this script."
        exit 1
    fi
    echo "Browser installed: $BROWSER"
fi

# Install HamClock service if not already done
INSTALL_DIR="/opt/hamclock-lite"
if [ ! -f "$INSTALL_DIR/server.py" ]; then
    echo "Installing HamClock server..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
    sudo cp "$SCRIPT_DIR/index.html" "$INSTALL_DIR/"
    sudo chmod +x "$INSTALL_DIR/server.py"
fi

# Always copy the native Python clients so users can switch modes later
sudo mkdir -p "$INSTALL_DIR"
sudo cp "$SCRIPT_DIR/hamclock_data.py" "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/hamclock_pygame.py" "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/hamclock_tkinter.py" "$INSTALL_DIR/"

# Copy X11 monitor config for auto-detect resolution (16-bit saves RAM on Pi 1)
sudo cp "$SCRIPT_DIR/10-monitor.conf" /usr/share/X11/xorg.conf.d/10-monitor.conf 2>/dev/null || true

# Detect the user
SERVICE_USER="${SUDO_USER:-$USER}"

# Add user to video and tty groups for X server access
sudo usermod -aG video,tty,input "$SERVICE_USER"

# Create the hamclock service if not exists
if ! systemctl is-enabled hamclock-lite &>/dev/null; then
    sudo tee /etc/systemd/system/hamclock-lite.service > /dev/null <<EOF
[Unit]
Description=HamClock Lite Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable hamclock-lite
    sudo systemctl start hamclock-lite
fi

# Create kiosk launch script
if [ "$KIOSK_MODE" = "browser" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<KIOSKEOF
#!/bin/bash
# Wait for HamClock server to be ready
for i in \$(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Show a real cursor (fbdev has no HW cursor; without this nothing is drawn)
# then auto-hide it after 3s idle.
xsetroot -cursor_name left_ptr
unclutter -idle 3 -root &

# Start matchbox window manager (auto-maximizes all windows)
matchbox-window-manager -use_titlebar no -use_desktop_mode plain &
sleep 1

# Launch browser (matchbox will maximize it)
exec $BROWSER_CMD
KIOSKEOF
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
# Wait for HamClock server to be ready
for i in $(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
# Disable screen blanking
xset s off
xset -dpms
xset s noblank
# Launch Tkinter native client (replaces browser; still needs X)
exec python3 /opt/hamclock-lite/hamclock_tkinter.py
KIOSKEOF
elif [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
# Wait for HamClock server to be ready
for i in $(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
# Pygame framebuffer mode: no X server, SDL draws directly to /dev/fb0
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0
exec python3 /opt/hamclock-lite/hamclock_pygame.py
KIOSKEOF
fi
sudo chmod +x /opt/hamclock-lite/kiosk.sh

# Create kiosk systemd service
if [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display (Pygame framebuffer)
After=hamclock-lite.service
Wants=hamclock-lite.service

[Service]
Type=simple
User=$SERVICE_USER
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
ExecStart=/opt/hamclock-lite/kiosk.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
else
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display
After=hamclock-lite.service
Wants=hamclock-lite.service

[Service]
Type=simple
User=$SERVICE_USER
Environment=DISPLAY=:0
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
ExecStart=/usr/bin/xinit /opt/hamclock-lite/kiosk.sh -- :0 vt7
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
fi

sudo systemctl daemon-reload
sudo systemctl enable hamclock-lite hamclock-kiosk
# Always restart to pick up any file changes
sudo systemctl restart hamclock-lite
sudo systemctl restart hamclock-kiosk

# Disable console blanking (must stay on a single line in cmdline.txt)
CMDLINE=""
if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE="/boot/firmware/cmdline.txt"
elif [ -f /boot/cmdline.txt ]; then
    CMDLINE="/boot/cmdline.txt"
fi
if [ -n "$CMDLINE" ]; then
    if ! grep -q "consoleblank=0" "$CMDLINE"; then
        sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
    fi
fi

# Force HDMI output even if no monitor detected at boot
BOOT_CONFIG=""
if [ -f /boot/firmware/config.txt ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    BOOT_CONFIG="/boot/config.txt"
fi
if [ -n "$BOOT_CONFIG" ]; then
    grep -q "hdmi_force_hotplug" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_force_hotplug=1' >> $BOOT_CONFIG"
    grep -q "hdmi_drive" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_drive=2' >> $BOOT_CONFIG"
fi

echo ""
echo "=== Kiosk Mode Installed ($KIOSK_MODE) ==="
echo "HamClock will now display fullscreen on this Pi's monitor."
echo "It will auto-start on every boot."
echo ""
if [ "$KIOSK_MODE" = "browser" ]; then
    echo "Browser: $BROWSER"
elif [ "$KIOSK_MODE" = "pygame" ]; then
    echo "Display: Native Pygame (framebuffer, no X11)"
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    echo "Display: Native Tkinter (xinit + X11)"
fi
echo ""
echo "Commands:"
echo "  sudo systemctl status hamclock-kiosk   — check kiosk status"
echo "  sudo systemctl restart hamclock-kiosk  — restart display"
echo "  sudo systemctl stop hamclock-kiosk     — stop display"
echo "  sudo systemctl disable hamclock-kiosk  — disable auto-start"
echo ""
echo "To go back to normal CLI, run:"
echo "  sudo systemctl disable hamclock-kiosk"
echo "  sudo systemctl stop hamclock-kiosk"
echo ""
PI_IP=$(hostname -I | awk '{print $1}')
echo "Also accessible from any browser at: http://${PI_IP}:8080"
