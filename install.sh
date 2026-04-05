#!/bin/bash
# HamClock Lite — Raspberry Pi 1 Installer
# Run on a fresh Raspberry Pi OS Lite installation

set -e

echo "=== HamClock Lite Installer ==="
echo "Installing for Raspberry Pi 1 (ARMv6)"
echo ""

# Check if running on Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This doesn't appear to be a Raspberry Pi. Continue anyway? (y/n)"
    read -r answer
    if [ "$answer" != "y" ]; then exit 1; fi
fi

# Install directory
INSTALL_DIR="/opt/hamclock-lite"
echo "Installing to $INSTALL_DIR..."

# Copy files
sudo mkdir -p "$INSTALL_DIR"
sudo cp server.py "$INSTALL_DIR/"
sudo cp index.html "$INSTALL_DIR/"
sudo chmod +x "$INSTALL_DIR/server.py"

# Create systemd service
sudo tee /etc/systemd/system/hamclock-lite.service > /dev/null <<'EOF'
[Unit]
Description=HamClock Lite
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/hamclock-lite
ExecStart=/usr/bin/python3 /opt/hamclock-lite/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable hamclock-lite
sudo systemctl start hamclock-lite

echo ""
echo "=== Installation Complete ==="
echo "HamClock Lite is running at: http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "Commands:"
echo "  sudo systemctl status hamclock-lite   — check status"
echo "  sudo systemctl restart hamclock-lite  — restart"
echo "  sudo systemctl stop hamclock-lite     — stop"
echo "  journalctl -u hamclock-lite -f        — view logs"
