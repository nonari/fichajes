#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="fichaxe.service"

# ──────────────────────────────────────────────
# 1. Detect or accept BOT_PATH
# ──────────────────────────────────────────────
BOT_PATH="${1:-$(pwd)}"  # use argument or current directory
VENV_PATH="${BOT_PATH}/.venv"
PYTHON_PATH="${VENV_PATH}/bin/python3"
LOG_FILE="${BOT_PATH}/fichaje.log"
REQ_FILE="${BOT_PATH}/requirements.txt"

echo "📦 Installing ${SERVICE_NAME}"
echo "➡️ BOT_PATH=${BOT_PATH}"

if [ ! -f "${BOT_PATH}/fichaxebot/bot.py" ]; then
    echo "❌ fichaxebot/bot.py not found in ${BOT_PATH}"
    exit 1
fi

# ──────────────────────────────────────────────
# 2. Ensure Virtual Environment
# ──────────────────────────────────────────────
if [ ! -d "${VENV_PATH}" ]; then
    echo "🐍 Creating virtual environment: ${VENV_PATH}"
    python3 -m venv "${VENV_PATH}"
    source "${VENV_PATH}/bin/activate"

    if [ -f "${REQ_FILE}" ]; then
        echo "📦 Installing dependencies from requirements.txt"
        pip install --upgrade pip
        pip install -r "${REQ_FILE}"
    else
        echo "⚠️ No requirements.txt found — skipping dependency install"
    fi

    deactivate
else
    echo "✔️ Reusing existing virtualenv"
    if [ ! -x "${PYTHON_PATH}" ]; then
        echo "❌ Virtualenv broken: python binary not found!"
        exit 1
    fi
fi

if [ ! -x "${PYTHON_PATH}" ]; then
    echo "⚠️ Virtual env not found or invalid; using system python"
    PYTHON_PATH="$(command -v python3)"
fi

# ──────────────────────────────────────────────
# 3. Generate systemd service file
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
# 4. Enable, start, verify service
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
