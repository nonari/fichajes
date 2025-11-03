#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="fichaxe.service"

# ──────────────────────────────────────────────
# 1. Detect or accept BOT_PATH
# ──────────────────────────────────────────────
BOT_PATH="${1:-$(pwd)}"  # use argument or current directory
PYTHON_PATH="${BOT_PATH}/.venv/bin/python3"
LOG_FILE="${BOT_PATH}/fichaje.log"

echo "📦 Installing ${SERVICE_NAME}"
echo "➡️ BOT_PATH=${BOT_PATH}"

# Check that the package exists
if [ ! -f "${BOT_PATH}/fichaxebot/bot.py" ]; then
    echo "❌ fichaxebot/bot.py not found in ${BOT_PATH}"
    exit 1
fi

# Check python binary
if [ ! -x "${PYTHON_PATH}" ]; then
    echo "⚠️ Virtual env not found; using system python"
    PYTHON_PATH="$(command -v python3)"
fi

# ──────────────────────────────────────────────
# 2. Generate systemd service file
# ──────────────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=Bot Telegram fichaje USC
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_PATH}
ExecStart=${PYTHON_PATH} -m fichaxebot.bot
Restart=always
RestartSec=10
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=multi-user.target
EOF

echo "📝 Created ${SERVICE_FILE}"

# ──────────────────────────────────────────────
# 3. Enable, start, and verify service
# ──────────────────────────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "🚀 Starting service..."
if ! sudo systemctl start "${SERVICE_NAME}"; then
    echo "❌ Failed to start service. Rolling back..."
    sudo systemctl disable "${SERVICE_NAME}" || true
    sudo rm -f "${SERVICE_FILE}"
    sudo systemctl daemon-reload
    exit 1
fi

sleep 3

if ! sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "❌ Service failed to stay active. Showing last logs:"
    echo "───────────────────────────────"
    sudo journalctl -u "${SERVICE_NAME}" -n 20 --no-pager || true
    echo "───────────────────────────────"
    echo "🧹 Cleaning up..."
    sudo systemctl disable "${SERVICE_NAME}" || true
    sudo systemctl stop "${SERVICE_NAME}" || true
    sudo rm -f "${SERVICE_FILE}"
    sudo systemctl daemon-reload
    exit 1
fi

echo "✅ Service ${SERVICE_NAME} installed and running."
echo "   → Logs: ${LOG_FILE}"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l | head -n 15
