#!/usr/bin/env bash
# Apply the service account without reinstalling dependencies or replacing the main unit.
set -euo pipefail

BOT_PATH="$(cd "${1:-$(pwd)}" && pwd)"
SERVICE_USER="${2:-${SUDO_USER:-}}"
SERVICE_NAME="fichaxe.service"

if [[ -z "$SERVICE_USER" ]] || ! SERVICE_UID="$(id -u "$SERVICE_USER")" || [[ "$SERVICE_UID" == 0 ]]; then
    echo "Choose an existing non-root service user as the second argument." >&2
    exit 1
fi
SERVICE_GID="$(id -g "$SERVICE_USER")"
if [[ "$(id -u)" != 0 ]]; then
    echo "Run this command with sudo to change the service and file ownership." >&2
    exit 1
fi
if [[ ! -f "$BOT_PATH/fichaxebot/bot.py" ]]; then
    echo "Bot not found at $BOT_PATH" >&2
    exit 1
fi
# Check access before stopping the running service. Root-owned runtime files are repaired below.
runuser -u "$SERVICE_USER" -- test -w "$BOT_PATH"
runuser -u "$SERVICE_USER" -- test -r "$BOT_PATH/config.json"
runuser -u "$SERVICE_USER" -- test -x "$BOT_PATH/.venv/bin/python3"

DROPIN_DIR="/etc/systemd/system/$SERVICE_NAME.d"
TEMP_UNIT="$(mktemp)"
trap 'rm -f "$TEMP_UNIT"' EXIT
cat > "$TEMP_UNIT" <<EOF
[Service]
User=$SERVICE_USER
Group=$SERVICE_GID
EOF

# Stop before changing ownership, so shutdown cannot recreate root-owned state afterwards.
systemctl daemon-reload
systemctl stop "$SERVICE_NAME"
for runtime_path in "$BOT_PATH/.schedule.data" "$BOT_PATH/.plugin_data" "$BOT_PATH/fichaje.log" \
                    "${FICHAXE_LOG_DIR:-/tmp/fichaxe_app}"; do
    if [[ -e "$runtime_path" && ! -L "$runtime_path" ]]; then
        # Never follow symlinks or change files already owned by another non-root user.
        find -P "$runtime_path" -xdev -uid 0 ! -type l -exec chown "$SERVICE_UID:$SERVICE_GID" -- {} +
    fi
done
install -d -m 755 "$DROPIN_DIR"
install -m 644 "$TEMP_UNIT" "$DROPIN_DIR/user.conf"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"
sleep 3
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Service did not stay active. Inspect: sudo journalctl -u $SERVICE_NAME -n 30" >&2
    exit 1
fi
systemctl show "$SERVICE_NAME" -p User -p Group -p ActiveState -p MainPID --no-pager
