#!/bin/bash
# HamClock Pi1 — Kiosk Mode Installer
# Displays the dashboard fullscreen on the Pi's own monitor

set -euo pipefail

echo "=== HamClock Pi1 Kiosk Mode Installer ==="
echo "This will set up your Pi to boot directly into HamClock on its monitor."
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required. Run: sudo apt install python3"
    exit 1
fi

# Install minimal X server + lightweight browser
echo "Installing display server and browser (this may take a few minutes)..."
sudo apt update
sudo apt install -y xserver-xorg xinit x11-xserver-utils unclutter curl
# Try surf first (lightest), fall back to midori, then chromium-browser
if sudo apt install -y surf 2>/dev/null; then
    BROWSER="surf"
    BROWSER_CMD="surf -F http://localhost:8080"
elif sudo apt install -y midori 2>/dev/null; then
    BROWSER="midori"
    BROWSER_CMD="midori -e Fullscreen -a http://localhost:8080"
else
    sudo apt install -y chromium-browser
    BROWSER="chromium-browser"
    BROWSER_CMD="chromium-browser --kiosk --noerrdialogs --disable-translate --no-first-run --fast --fast-start --disable-features=TranslateUI --disk-cache-size=0 http://localhost:8080"
fi
echo "Browser installed: $BROWSER"

# Install HamClock service if not already done
INSTALL_DIR="/opt/hamclock-lite"
if [ ! -f "$INSTALL_DIR/server.py" ]; then
    echo "Installing HamClock server..."
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
    sudo cp "$SCRIPT_DIR/index.html" "$INSTALL_DIR/"
    sudo chmod +x "$INSTALL_DIR/server.py"
fi

# Detect the user
SERVICE_USER="${SUDO_USER:-$USER}"

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

# Hide mouse cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Launch browser fullscreen
exec $BROWSER_CMD
KIOSKEOF
sudo chmod +x /opt/hamclock-lite/kiosk.sh

# Create kiosk systemd service
sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display
After=hamclock-lite.service
Wants=hamclock-lite.service

[Service]
Type=simple
User=$SERVICE_USER
Environment=DISPLAY=:0
ExecStart=/usr/bin/xinit /opt/hamclock-lite/kiosk.sh -- :0 -nocursor
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable hamclock-kiosk
sudo systemctl start hamclock-kiosk

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

echo ""
echo "=== Kiosk Mode Installed ==="
echo "HamClock will now display fullscreen on this Pi's monitor."
echo "It will auto-start on every boot."
echo ""
echo "Browser: $BROWSER"
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
