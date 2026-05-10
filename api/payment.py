"""
Vercel serverless handlers for Bayar.gg payments.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))

from lib.bayar_gg import BayarGGError, check_payment, create_payment, payment_methods, verify_webhook
from lib.subscription import handle_subscription_webhook


def _public_base_url(headers) -> str:
    host = headers.get("Host", "")
    proto = headers.get("X-Forwarded-Proto") or (
        "http" if "localhost" in host or "127.0.0.1" in host else "https"
    )
    return f"{proto}://{host}" if host else ""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        try:
            if parsed.path.endswith("/methods"):
                data, code = payment_methods()
                return self._send_json(data, code)

            if parsed.path.endswith("/check"):
                data, code = check_payment(params.get("invoice", ""))
                return self._send_json(data, code)

            return self._send_json({"error": "Route pembayaran tidak ditemukan"}, 404)
        except Exception as exc:
            return self._send_error(exc)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()

            if parsed.path.endswith("/create"):
                data, code = create_payment(payload, _public_base_url(self.headers))
                return self._send_json(data, code)

            if parsed.path.endswith("/webhook"):
                valid, message = verify_webhook(payload, self.headers)
                if not valid:
                    return self._send_json({"status": "error", "message": message}, 401)
                sub_body, sub_code = handle_subscription_webhook(payload)
                if sub_body.get("status") == "success":
                    return self._send_json(sub_body, sub_code)
                return self._send_json({
                    "status": "success",
                    "event": payload.get("event"),
                    "invoice_id": payload.get("invoice_id"),
                    "payment_status": payload.get("status"),
                    "subscription": sub_body,
                }, 200)

            return self._send_json({"error": "Route pembayaran tidak ditemukan"}, 404)
        except Exception as exc:
            return self._send_error(exc)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    def _send_error(self, exc):
        payload = {"status": "error", "message": str(exc)}
        if isinstance(exc, BayarGGError) and exc.detail:
            payload["detail"] = exc.detail
        self._send_json(payload, getattr(exc, "status_code", 500))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Webhook-Signature, X-Webhook-Timestamp")

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
