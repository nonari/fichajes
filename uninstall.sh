#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="fichaxe.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

if sudo systemctl list-units --full -all | grep "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "🧹 Stopping and disabling ${SERVICE_NAME}..."
    sudo systemctl stop "$SERVICE_NAME" || true
    sudo systemctl disable "$SERVICE_NAME" || true
fi

# Remove service file if present
if [ -f "$SERVICE_PATH" ]; then
    echo "🗑 Removing $SERVICE_PATH"
    sudo rm -f "$SERVICE_PATH"
else
    echo "⚠️ Service file not found at $SERVICE_PATH"
fi

# Remove only the account override managed by the installer; preserve any other overrides.
sudo rm -f "$SERVICE_PATH.d/user.conf"
sudo rmdir "$SERVICE_PATH.d" 2>/dev/null || true

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "✅ Uninstallation completed successfully."
