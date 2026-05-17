#!/bin/bash
# ─── oatInvestor Public Website Launcher ───────────────────────────────────
PORT=8081
DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill old server on this port if any
lsof -ti:$PORT | xargs kill -9 2>/dev/null
sleep 0.5

echo "🌐  Starting oatInvestor website on http://localhost:$PORT"
echo "    Press Ctrl+C to stop"
echo ""

# Start server
cd "$DIR"
python3 -m http.server $PORT &
SERVER_PID=$!

# Open browser after short delay
sleep 0.8
open "http://localhost:$PORT"

# Keep running until Ctrl+C
trap "kill $SERVER_PID 2>/dev/null; echo ''; echo 'Server stopped.'" EXIT
wait $SERVER_PID
