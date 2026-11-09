#!/usr/bin/env bash
# One command to try Loan-Saathi locally (macOS / Linux / Git Bash):   ./demo.sh
# Installs what's missing, builds the dashboard, starts the API on 127.0.0.1:8000 (never on the network)
# and opens the dashboard. Speech needs Gnani keys in .env; without them, type instead of talking.
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || command -v python >/dev/null || { echo "Python 3.10+ is required."; exit 1; }
command -v npm >/dev/null || { echo "Node.js 20.19+ is required (https://nodejs.org)."; exit 1; }
PY_SYS=$(command -v python3 || command -v python)

[ -d .venv ] || { echo "Creating .venv..."; "$PY_SYS" -m venv .venv; }
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
"$PY" -c "import fastapi, lightgbm, uvicorn" 2>/dev/null || { echo "Installing Python packages..."; "$PY" -m pip install -q -r requirements.txt; }

# A local .env with a fresh random WEBHOOK_KEY if there isn't one yet (never commit .env).
NEW_KEY=""
[ -f .env ] || cp .env.example .env
if grep -Eq '^WEBHOOK_KEY=(replace-with.*|[[:space:]]*)$' .env; then
  NEW_KEY=$("$PY" -c "import secrets; print(secrets.token_hex(16))")
  "$PY" - "$NEW_KEY" <<'EOF'
import re, sys
p = ".env"
s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(re.sub(r"(?m)^WEBHOOK_KEY=.*$", "WEBHOOK_KEY=" + sys.argv[1], s))
EOF
fi

if [ ! -f dashboard/dist/index.html ] || [ -n "$(find dashboard/src -newer dashboard/dist/index.html -print -quit)" ]; then
  echo "Building the dashboard..."
  (cd dashboard && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
fi

echo
echo "Loan-Saathi is starting at http://127.0.0.1:8000/dashboard/"
if [ -n "$NEW_KEY" ]; then echo "Your new local key (also saved in .env as WEBHOOK_KEY): $NEW_KEY";
else echo "Sign in with the WEBHOOK_KEY from your .env file."; fi
echo "Press Ctrl+C to stop."
[ -n "${SAATHI_NO_BROWSER:-}" ] || ( sleep 3; (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000/dashboard/) \
  || (command -v open >/dev/null && open http://127.0.0.1:8000/dashboard/) || true ) >/dev/null 2>&1 &
exec "$PY" -I -X utf8 src/server.py
