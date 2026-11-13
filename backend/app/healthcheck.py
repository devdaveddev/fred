"""Container health probe: `python -m app.healthcheck` exits 0 when the API answers /health."""
import sys
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)
except Exception:
    sys.exit(1)
