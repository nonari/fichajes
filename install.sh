#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="fichaxe.service"
SERVICE_USER="${FICHAXE_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_UID="$(id -u "$SERVICE_USER")"
if [[ "$SERVICE_UID" == 0 ]]; then
    echo "❌ Run the installer as your normal user, or set FICHAXE_SERVICE_USER to a non-root account." >&2
    exit 1
fi

# ──────────────────────────────────────────────
# 1. Detect or accept BOT_PATH
# ──────────────────────────────────────────────
BOT_PATH="${1:-$(pwd)}"  # use argument or current directory
VENV_PATH="${BOT_PATH}/.venv"
PYTHON_PATH="${VENV_PATH}/bin/python3"
REQ_FILE="${BOT_PATH}/requirements.txt"
LOG_FILE="${BOT_PATH}/fichaje.log"

echo "📦 Installing ${SERVICE_NAME}"
echo "➡️ BOT_PATH=${BOT_PATH}"

# Check that the package exists
if [ ! -f "${BOT_PATH}/fichaxebot/bot.py" ]; then
    echo "❌ fichaxebot/bot.py not found in ${BOT_PATH}"
    exit 1
fi

# Prepare dependencies before creating or starting the service.
if [ ! -f "${REQ_FILE}" ]; then
    echo "❌ requirements.txt not found in ${BOT_PATH}"
    exit 1
fi

if [ ! -e "${VENV_PATH}" ] && [ ! -L "${VENV_PATH}" ]; then
    echo "🐍 Creating virtual environment: ${VENV_PATH}"
    python3 -m venv "${VENV_PATH}"
else
    echo "✔️ Reusing existing virtual environment"
fi

if [ ! -x "${PYTHON_PATH}" ]; then
    echo "❌ Virtual environment is broken: ${PYTHON_PATH} is missing or not executable."
    echo "Repair or recreate ${VENV_PATH}, then run this installer again."
    exit 1
fi

echo "📦 Installing dependencies from requirements.txt"
"${PYTHON_PATH}" -m pip install -r "${REQ_FILE}"

echo "📦 Checking enabled plugin dependencies"
"${PYTHON_PATH}" "${BOT_PATH}/devtools/install_plugins.py" "${BOT_PATH}"

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
echo "🚀 Configuring and starting service as ${SERVICE_USER}..."
sudo bash "${BOT_PATH}/devtools/configure_service_user.sh" "${BOT_PATH}" "${SERVICE_USER}"

echo "✅ Service ${SERVICE_NAME} installed and running."
echo "   → Logs: ${LOG_FILE}"
