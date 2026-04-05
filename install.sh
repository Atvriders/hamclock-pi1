#!/bin/bash
# HamClock Lite — Raspberry Pi 1 Installer
# Run on a fresh Raspberry Pi OS Lite installation

set -euo pipefail

echo "=== HamClock Lite Installer ==="
echo "Installing for Raspberry Pi 1 (ARMv6)"
echo ""

# Check if running on Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This doesn't appear to be a Raspberry Pi. Continue anyway? (y/n)"
    read -r answer
    if [ "$answer" != "y" ]; then exit 1; fi
fi

# Check Python 3 is available
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed. Install it with: sudo apt install python3"
    exit 1
fi

# Detect the user who will run the service (the invoking user, not root)
SERVICE_USER="${SUDO_USER:-$USER}"

# Install directory
INSTALL_DIR="/opt/hamclock-lite"
echo "Installing to $INSTALL_DIR..."
echo "Service will run as user: $SERVICE_USER"

# Copy files
sudo mkdir -p "$INSTALL_DIR"
sudo cp server.py "$INSTALL_DIR/"
sudo cp index.html "$INSTALL_DIR/"
sudo chmod +x "$INSTALL_DIR/server.py"

# Create systemd service
sudo tee /etc/systemd/system/hamclock-lite.service > /dev/null <<EOF
[Unit]
Description=HamClock Lite
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=/opt/hamclock-lite
ExecStart=/usr/bin/python3 /opt/hamclock-lite/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start (restart if already running)
sudo systemctl daemon-reload
sudo systemctl enable hamclock-lite
if sudo systemctl is-active --quiet hamclock-lite; then
    echo "Service already running, restarting..."
    sudo systemctl restart hamclock-lite
else
    sudo systemctl start hamclock-lite
fi

# Verify the service started
sleep 2
if sudo systemctl is-active --quiet hamclock-lite; then
    echo ""
    echo "=== Installation Complete ==="
    echo "HamClock Lite is running at: http://$(hostname -I | awk '{print $1}'):8080"
else
    echo ""
    echo "=== Warning: Service failed to start ==="
    echo "Check logs with: journalctl -u hamclock-lite -n 20"
fi

echo ""
echo "Commands:"
echo "  sudo systemctl status hamclock-lite   — check status"
echo "  sudo systemctl restart hamclock-lite  — restart"
echo "  sudo systemctl stop hamclock-lite     — stop"
echo "  journalctl -u hamclock-lite -f        — view logs"
