"""
api/get-video.py
Vidgf video extractor — menggunakan shared lib.
"""
from http.server import BaseHTTPRequestHandler
import json, os, sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from lib import vidgf


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        params   = parse_qs(urlparse(self.path).query)
        video_id = params.get("id", [None])[0]

        if not video_id:
            return self.send_json({"status": "error", "message": "ID kosong"}, 400)

        if "/" in video_id:
            video_id = video_id.strip("/").split("/")[-1].split("?")[0]

        url = vidgf.extract(video_id)
        if url:
            self.send_json({"status": "success", "link": url, "id": video_id})
        else:
            self.send_json({"status": "error", "message": "Link tidak ditemukan"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass