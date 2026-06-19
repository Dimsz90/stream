from http.server import BaseHTTPRequestHandler
import json, re, os, tempfile, mimetypes

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        length    = int(self.headers.get("Content-Length", 0))
        body      = json.loads(self.rfile.read(length) or b"{}")
        url       = body.get("url","").strip()
        format_id = body.get("format_id","bestvideo+bestaudio/best")
        title     = body.get("title","video")

        if not url:
            return self.send_json({"error": "URL required"}, 400)
        if not yt_dlp:
            return self.send_json({"error": "yt-dlp tidak terinstall"}, 500)

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {"format":format_id,
                        "outtmpl":os.path.join(tmpdir,"%(title)s.%(ext)s"),
                        "quiet":True, "merge_output_format":"mp4"}
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                files = os.listdir(tmpdir)
                if not files:
                    return self.send_json({"error":"Download gagal"}, 500)
                filepath = os.path.join(tmpdir, files[0])
                ext      = os.path.splitext(files[0])[1]
                safe     = re.sub(r'[^\w\s-]','',title)[:60].strip() or "video"
                fname    = f"{safe}{ext}"
                mime     = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
                with open(filepath,"rb") as f:
                    data_bytes = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data_bytes)))
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data_bytes)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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