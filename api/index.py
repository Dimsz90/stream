"""
api/index.py — Vercel Serverless Router
Semua request /api/* masuk ke sini dan di-dispatch ke modul yang tepat.
"""
from http.server import BaseHTTPRequestHandler
import sys
import os
import json
import re
import importlib
from urllib.parse import urlparse, parse_qs

# Tambah api/ ke path agar bisa import lib.*
sys.path.insert(0, os.path.dirname(__file__))

from lib.subscription import check_subscription

PROTECTED_PATHS = (
    "/api/get-video",
    "/api/tmdb-stream",
    "/api/scan",
    "/api/formats",
    "/api/download",
    "/api/imdb",
    "/api/subtitle/search",
    "/api/subtitle/download",
)

# ── Route Publik ──────────────────────────────────────────────────────────────


class handler(BaseHTTPRequestHandler):
    """Single entry-point untuk semua /api/* di Vercel."""

    # ── GET ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if self._subscription_denied(path):
            return

        # /api/debug
        if path == "/api/debug":
            return self._dispatch_module("debug", "GET")

        if path == "/api/subscription/config":
            return self._dispatch_module("subscription", "GET")

        if path in ("/api/subscription/me", "/api/subscription/plans"):
            return self._dispatch_module("subscription", "GET")

        if path in ("/api/payments/check", "/api/payment/check", "/api/bayargg/check"):
            return self._dispatch_module("payment", "GET")

        if path in ("/api/payments/methods", "/api/payment/methods", "/api/bayargg/methods"):
            return self._dispatch_module("payment", "GET")

        if path == "/api/proxy/sign":
            return self._dispatch_module("proxy_sign", "GET")

        # /api/imdb
        if path == "/api/imdb":
            return self._dispatch_module("imdb", "GET")

        # /api/imdb-proxy
        if path == "/api/imdb-proxy" or path.startswith("/api/imdb-proxy/"):
            return self._dispatch_module("imdb", "GET")

        # /api/tmdb-proxy
        if path == "/api/tmdb-proxy" or path.startswith("/api/tmdb-proxy/"):
            return self._dispatch_module("tmdb", "GET")

        # /api/proxy
        if path == "/api/proxy":
            return self._dispatch_module("imdb", "GET")

        # /api/get-video
        if path == "/api/get-video":
            return self._dispatch_module("get-video", "GET")

        # /api/tmdb-stream
        if path == "/api/tmdb-stream":
            return self._dispatch_module("tmdb", "GET")

        # /api/subtitle/search
        if path == "/api/subtitle/search":
            return self._dispatch_module("subtitle", "GET")

        # /api/subtitle/download
        if path == "/api/subtitle/download":
            return self._dispatch_module("subtitle", "GET")

        # /api/formats
        if path == "/api/formats":
            return self._dispatch_module("formats", "GET")


        # /api/dracin/*
        if path.startswith("/api/dracin/"):
            # Extract subpath
            subpath = path.replace("/api/dracin/", "")
            # Dispatch ke modul dracin.py (biar dracin.py handle path parsing)
            return self._dispatch_module("dracin", "GET")

        self._send_json({"error": "Route tidak ditemukan"}, 404)
    # ── POST ───────────────────────────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path
        if self._subscription_denied(path):
            return

        if path == "/api/scan":
            return self._dispatch_module("scan", "POST")

        if path == "/api/download":
            return self._dispatch_module("download", "POST")

        if path == "/api/formats":
            return self._dispatch_module("formats", "POST")

        if path in (
            "/api/subscription/login",
            "/api/subscription/register",
            "/api/subscription/payment/create",
            "/api/subscription/payment/verify",
        ):
            return self._dispatch_module("subscription", "POST")

        if path in ("/api/payments/create", "/api/payment/create", "/api/bayargg/create"):
            return self._dispatch_module("payment", "POST")

        if path in ("/api/payments/webhook", "/api/payment/webhook", "/api/bayargg/webhook"):
            return self._dispatch_module("payment", "POST")

        if path == "/webhook/payment":
            return self._dispatch_module("payment", "POST")

        self._send_json({"error": "Route tidak ditemukan"}, 404)

    # ── OPTIONS (CORS preflight) ──────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── Dispatch ke modul spesifik ────────────────────────────────────────────
    def _dispatch_module(self, module_name: str, method: str):
        """Load modul dan panggil handler-nya."""
        try:
            mod_path = os.path.join(os.path.dirname(__file__), f"{module_name}.py")
            spec = importlib.util.spec_from_file_location(f"api_{module_name}", mod_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Buat instance handler dari modul target
            h = mod.handler.__new__(mod.handler)
            h.client_address = self.client_address
            h.server = self.server
            h.headers = self.headers
            h.path = self.path
            h.rfile = self.rfile
            h.wfile = self.wfile
            h.requestline = self.requestline
            h.command = self.command
            h.request_version = self.request_version

            # Dispatch ke method yang tepat
            if method == "GET" and hasattr(h, "do_GET"):
                h.do_GET()
            elif method == "POST" and hasattr(h, "do_POST"):
                h.do_POST()
            else:
                self._send_json({"error": "Method tidak didukung"}, 405)

        except FileNotFoundError:
            self._send_json({"error": f"Modul '{module_name}' tidak ditemukan"}, 404)
        except Exception as e:
            self._send_json({"error": f"Internal error: {e}"}, 500)

    def _subscription_denied(self, path: str) -> bool:
        protected = path in PROTECTED_PATHS or path.startswith("/api/dracin/")
        if not protected:
            return False
        ok, payload, status_code = check_subscription(self.headers)
        if ok:
            return False
        self._send_json(payload, status_code)
        return True

    # ── Utilities ─────────────────────────────────────────────────────────────
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Subscription-Token, x-api-token, X-Webhook-Signature, X-Webhook-Timestamp")

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
