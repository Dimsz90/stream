from http.server import BaseHTTPRequestHandler
import json
from urllib.parse import urlparse

from lib.config import REQUIRE_SUBSCRIPTION
from lib.subscription import (
    SUBSCRIPTION_PLANS,
    create_subscription_payment,
    login_subscriber,
    me,
    register_subscriber,
    verify_subscription_payment,
)


def _public_base_url(headers) -> str:
    host = headers.get("Host", "")
    proto = headers.get("X-Forwarded-Proto") or (
        "http" if "localhost" in host or "127.0.0.1" in host else "https"
    )
    return f"{proto}://{host}" if host else ""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/me"):
            body, status_code = me(self.headers)
            return self._send_json(body, status_code)
        if path.endswith("/plans"):
            return self._send_json({"status": "success", "plans": list(SUBSCRIPTION_PLANS.values())})
        self._send_json({"enabled": REQUIRE_SUBSCRIPTION})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        if path.endswith("/register"):
            body, status_code = register_subscriber(data.get("username", ""), data.get("password", ""))
        elif path.endswith("/payment/create"):
            body, status_code = create_subscription_payment(
                self.headers,
                data.get("plan", ""),
                _public_base_url(self.headers),
                data.get("redirect_url", ""),
            )
        elif path.endswith("/payment/verify"):
            body, status_code = verify_subscription_payment(
                self.headers,
                data.get("invoice", ""),
                data.get("payment_token", ""),
            )
        else:
            body, status_code = login_subscriber(data.get("username", ""), data.get("password", data.get("pin", "")))
        self._send_json(body, status_code)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
