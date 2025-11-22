#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCS_DIR="../docs"
PORT=8000

CERT="$SCRIPT_DIR/cert.pem"
KEY="$SCRIPT_DIR/key.pem"

# --- Check directory ---
if [ ! -d "$DOCS_DIR" ]; then
  echo "❌ Directory '$DOCS_DIR' does not exist."
  exit 1
fi

# --- Generate self-signed certificate if missing ---
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "🔐 Generating self-signed HTTPS certificate..."
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
    -keyout "$KEY" -out "$CERT" -days 365 \
    -subj "/CN=localhost"
  echo "✔ Certificate generated: $CERT, $KEY"
fi

echo "📁 Serving directory (HTTPS): $DOCS_DIR"
echo "🌐 https://localhost:$PORT"
echo "🛑 Press Ctrl+C to stop."
echo

# --- Launch HTTPS server ---
cd "$DOCS_DIR"

python3 - <<EOF
import http.server
import socketserver
import ssl

PORT = $PORT

class Handler(http.server.SimpleHTTPRequestHandler):
    pass

context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(
    certfile="$CERT",
    keyfile="$KEY"
)

httpd = socketserver.TCPServer(("", PORT), Handler)
httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

print(f"🔐 Serving HTTPS on https://localhost:{PORT}")
httpd.serve_forever()
EOF