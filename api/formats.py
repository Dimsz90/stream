from http.server import BaseHTTPRequestHandler
import json

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


def get_formats(url):
    if not yt_dlp: return "", "", []
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"skip_download":True}) as ydl:
            info = ydl.extract_info(url, download=False)
            fmts, seen = [], set()
            for f in (info.get("formats") or []):
                ext    = f.get("ext","")
                height = f.get("height")
                acodec = f.get("acodec","none")
                vcodec = f.get("vcodec","none")
                size   = f.get("filesize") or f.get("filesize_approx")
                if vcodec != "none" and height:
                    label = f"{height}p {ext.upper()}"
                elif vcodec == "none" and acodec != "none":
                    label = f"Audio {ext.upper()}"
                else: continue
                if label in seen: continue
                seen.add(label)
                fmts.append({"format_id":f.get("format_id",""),"label":label,
                             "ext":ext,"height":height,
                             "size":f"{size//1024//1024} MB" if size else "?"})
            fmts.sort(key=lambda x:(x.get("height") or 0), reverse=True)
            return info.get("title",""), info.get("thumbnail",""), fmts
    except Exception:
        return "", "", []


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")
        url    = body.get("url","").strip()
        if not url:
            return self.send_json({"error": "URL required"}, 400)
        title, thumb, fmts = get_formats(url)
        self.send_json({"title":title,"thumb":thumb,"formats":fmts})

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