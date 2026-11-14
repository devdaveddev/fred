#!/bin/sh
# Start Fred as one service: migrate, run the API privately, serve the website on $PORT.
# If either process exits, the container exits, so the platform restarts it.
set -e

if [ -z "$DATABASE_URL" ]; then
  echo "FATAL: DATABASE_URL is not set. Create a Postgres database and add its connection string as DATABASE_URL." >&2
  exit 1
fi
# On Render the public address is known: use it for links, cookies and OAuth/SSO redirects.
if [ -z "$PUBLIC_URL" ] && [ -n "$RENDER_EXTERNAL_URL" ]; then export PUBLIC_URL="$RENDER_EXTERNAL_URL"; fi
case "$PUBLIC_URL" in https://*) : "${COOKIE_SECURE:=true}"; export COOKIE_SECURE ;; esac
if [ -z "$FRED_SECRET_KEY" ]; then
  echo "WARNING: FRED_SECRET_KEY is not set: connecting Stripe/HubSpot/Slack is disabled until you add one." >&2
fi

cd /app
echo "Running database migrations…"
alembic upgrade head

uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips='*' &
API=$!
cd /web
node server.js &
WEB=$!

trap 'kill $API $WEB 2>/dev/null' TERM INT
# exit as soon as either process stops
while kill -0 $API 2>/dev/null && kill -0 $WEB 2>/dev/null; do sleep 2; done
echo "A process stopped; exiting so the platform restarts the service." >&2
kill $API $WEB 2>/dev/null || true
exit 1
