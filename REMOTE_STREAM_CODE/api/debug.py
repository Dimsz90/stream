from http.server import BaseHTTPRequestHandler
import json, sys, os


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        results = {
            "python": sys.version,
            "env": "vercel" if os.environ.get("VERCEL") else "local",
        }
        for pkg in ["requests","bs4","yt_dlp","playwright"]:
            try:
                mod = __import__(pkg)
                results[pkg] = f"OK ({getattr(mod,'__version__','?')})"
            except ImportError as e:
                results[pkg] = f"MISSING: {e}"
        try:
            import requests as req
            r = req.get("https://httpbin.org/get", timeout=5)
            results["network"] = f"OK ({r.status_code})"
        except Exception as e:
            results["network"] = f"ERROR: {e}"

        body = json.dumps(results, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass