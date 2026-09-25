#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCS_DIR="$SCRIPT_DIR/../docs"
PORT=8000
# Python expects True/False with capital letters
USE_HTTPS="False"
USE_NGROK="False"
# Reserved ngrok domain; override with NGROK_URL=https://other.ngrok-free.dev
NGROK_URL="${NGROK_URL:-https://nonrevertible-alene-sciential.ngrok-free.dev}"

# Check for HTTPS and ngrok flags
for arg in "$@"; do
  if [ "$arg" == "--https" ] || [ "$arg" == "-s" ]; then
    USE_HTTPS="True"
  fi
  if [ "$arg" == "--ngrok" ] || [ "$arg" == "-n" ]; then
    USE_NGROK="True"
  fi
done

# --- Check directory ---
if [ ! -d "$DOCS_DIR" ]; then
  echo "❌ Directory '$DOCS_DIR' does not exist."
  exit 1
fi

# --- Logic for HTTPS Setup ---
CERT="$SCRIPT_DIR/cert.pem"
KEY="$SCRIPT_DIR/key.pem"

if [ "$USE_HTTPS" = "True" ]; then
  PROTOCOL="https"
  if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "🔐 Generating self-signed HTTPS certificate..."
    openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
      -keyout "$KEY" -out "$CERT" -days 365 \
      -subj "/CN=localhost"
  fi
else
  PROTOCOL="http"
fi

echo "📁 Serving directory: $DOCS_DIR"
echo "🌐 $PROTOCOL://localhost:$PORT"

# --- ngrok tunnel ---
if [ "$USE_NGROK" = "True" ]; then
  if ! command -v ngrok >/dev/null 2>&1; then
    echo "❌ ngrok is not installed."
    exit 1
  fi
  NGROK_LOG="$SCRIPT_DIR/ngrok.log"
  ngrok http "$PROTOCOL://localhost:$PORT" --url="$NGROK_URL" \
    --log="$NGROK_LOG" --log-format=logfmt >/dev/null &
  NGROK_PID=$!
  trap 'kill "$NGROK_PID" 2>/dev/null' EXIT
  sleep 3
  if ! kill -0 "$NGROK_PID" 2>/dev/null; then
    echo "❌ ngrok failed to start:"
    grep -E 'lvl=(eror|crit)' "$NGROK_LOG" | tail -1
    exit 1
  fi
  echo "🚇 ngrok tunnel: $NGROK_URL (log: $NGROK_LOG)"
fi

echo "🛑 Press Ctrl+C to stop."
echo

cd "$DOCS_DIR"

# --- Launch Server ---
python3 - <<EOF
import http.server
import socketserver
import ssl

PORT = $PORT
# Now this will correctly inject True or False
USE_HTTPS = $USE_HTTPS

handler = http.server.SimpleHTTPRequestHandler

# Allow port reuse so you don't get "Address already in use" errors on restart
socketserver.TCPServer.allow_reuse_address = True

with socketserver.TCPServer(("", PORT), handler) as httpd:
    if USE_HTTPS:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile="$CERT", keyfile="$KEY")
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print("🔐 HTTPS enabled.")
    else:
        print("🔓 HTTP enabled.")

    httpd.serve_forever()
EOF