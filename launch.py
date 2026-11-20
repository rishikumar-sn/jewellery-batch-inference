"""One process serves the built frontend and API; opens UI after model readiness."""
import argparse
import json
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=5050)
parser.add_argument('--no-browser', action='store_true')
args = parser.parse_args()
if not (Path(__file__).parent / 'frontend/dist/index.html').exists():
    raise SystemExit('Frontend build missing. Run setup.ps1 first.')
with socket.socket() as probe:
    try:
        probe.bind(('127.0.0.1', args.port))
    except OSError:
        raise SystemExit(f'Port {args.port} is in use. Run .\\run.ps1 -Port 5052 (or another free port).')

url = f'http://127.0.0.1:{args.port}'
def open_when_ready():
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(480):
        try:
            with http.open(url + '/api/health', timeout=2) as response:
                status = json.load(response)['status']
            if status in ('ready', 'error'):
                webbrowser.open(url)
                return
        except Exception:
            pass
        time.sleep(.5)

if not args.no_browser:
    threading.Thread(target=open_when_ready, daemon=True).start()
print(f'Embsys Jewellery Inspection: {url}\nLoading Qwen3.5-2B. Press Ctrl+C to stop.')
from backend.app import app
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='info'))

def request_shutdown():
    server.should_exit = True

app.state.shutdown_callback = request_shutdown
server.run()
