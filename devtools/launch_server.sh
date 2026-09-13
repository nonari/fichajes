#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCS_DIR="../docs"
PORT=8000
# Python expects True/False with capital letters
USE_HTTPS="False"

# Check for HTTPS flag
for arg in "$@"; do
  if [ "$arg" == "--https" ] || [ "$arg" == "-s" ]; then
    USE_HTTPS="True"
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