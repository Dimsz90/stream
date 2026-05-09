from http.server import BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse

from lib.proxy_signing import sign_proxy_url
from lib.subscription import check_subscription


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok, payload, status_code = check_subscription(self.headers)
        if not ok:
            return self._send_json(payload, status_code)

        params = parse_qs(urlparse(self.path).query)
        target_url = (params.get("url", [""])[0] or "").strip()
        parsed = urlparse(target_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return self._send_json({"error": "Invalid url param"}, 400)

        host = self.headers.get("Host", "")
        protocol = "http" if "localhost" in host or "127.0.0.1" in host else "https"
        base = f"{protocol}://{host}" if host else ""
        self._send_json({"status": "success", "url": sign_proxy_url(target_url, base)})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Subscription-Token")

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
