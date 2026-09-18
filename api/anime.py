"""
api/anime.py
Handler endpoint Anime streaming berbasis scraper Samehadaku.
"""
import os, sys, json, time, re, html as html_lib, base64, mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import cloudscraper
except Exception:
    cloudscraper = None

from bs4 import BeautifulSoup

try:
    import yt_dlp
except Exception:
    yt_dlp = None

try:
    from Crypto.Cipher import AES
except Exception:
    AES = None


BLOGGER_HOSTS = {
    "blogger.com",
    "www.blogger.com",
    "blogspot.com",
}

FILEDON_HOSTS = {
    "filedon.co",
    "www.filedon.co",
}

MEGA_HOSTS = {
    "mega.nz",
    "www.mega.nz",
    "mega.co.nz",
    "www.mega.co.nz",
}

MEGA_INFO_CACHE = {}
MEGA_INFO_TTL = 5 * 60

DIRECT_MEDIA_EXTENSIONS = (".mp4", ".m3u8", ".webm", ".ogg")


def _is_filedon_url(url):
    try:
        return (urlparse(url).hostname or "").lower() in FILEDON_HOSTS
    except Exception:
        return False


def _filedon_proxy_url(url):
    """Return the local relay URL used for Filedon VIP embeds."""
    if not _is_filedon_url(url):
        return url
    # Reuse the application's proxy signature format, but point it at the
    # HTML relay route. The iframe navigation cannot carry X-Subscription-
    # Token headers, so a short-lived signed query is required here.
    try:
        try:
            from .lib.proxy_signing import sign_proxy_url
        except ImportError:
            from lib.proxy_signing import sign_proxy_url
        signed = sign_proxy_url(url)
        query = signed.split("?", 1)[1]
        return f"/api/anime/embed?{query}"
    except Exception:
        # Development environments without subscription configuration can use
        # the unsigned path; production validation below still rejects it.
        return f"/api/anime/embed?url={quote(url, safe='')}"


def _media_proxy_url(url):
    """Return a signed same-origin relay URL for expiring media sources."""
    if not url:
        return ""
    try:
        try:
            from .lib.proxy_signing import sign_proxy_url
        except ImportError:
            from lib.proxy_signing import sign_proxy_url
        return sign_proxy_url(url)
    except Exception:
        return f"/api/proxy?url={quote(url, safe='')}"


def _mega_proxy_url(url):
    """Return a signed local URL that decrypts a public Mega file."""
    if not url:
        return ""
    try:
        try:
            from .lib.proxy_signing import sign_proxy_url
        except ImportError:
            from lib.proxy_signing import sign_proxy_url
        signed = sign_proxy_url(url)
        return signed.replace("/api/proxy?", "/api/anime/mega?", 1)
    except Exception:
        return f"/api/anime/mega?url={quote(url, safe='')}"


def _mega_b64decode(value):
    value = str(value or "").replace("-", "+").replace("_", "/")
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.b64decode(value)


def _mega_link_parts(url):
    """Parse public Mega /embed/id#key, /file/id#key, and legacy links."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if (parsed.hostname or "").lower() not in MEGA_HOSTS:
        return None

    file_id = ""
    key = parsed.fragment or ""
    match = re.search(r"/(?:embed|file)/([^/#?]+)", parsed.path or "", re.I)
    if match:
        file_id = match.group(1)
    elif parsed.fragment.startswith("!"):
        legacy = parsed.fragment.lstrip("!").split("!")
        if len(legacy) >= 2:
            file_id, key = legacy[0], legacy[1]
    if not file_id or not key:
        return None
    return file_id, key


def get_mega_file_info(url):
    """Resolve Mega's encrypted CDN URL and derive its AES-CTR parameters."""
    if AES is None:
        raise RuntimeError("Dukungan AES Mega belum terpasang")
    parts = _mega_link_parts(url)
    if not parts:
        raise ValueError("URL Mega tidak valid")

    cached = MEGA_INFO_CACHE.get(url)
    if cached and cached.get("cached_until", 0) > time.time():
        return cached

    file_id, public_key = parts
    raw_key = _mega_b64decode(public_key)
    if len(raw_key) != 32:
        raise ValueError("Kunci file Mega tidak valid")
    aes_key = bytes(raw_key[i] ^ raw_key[i + 16] for i in range(16))
    base_iv = raw_key[16:24] + (b"\0" * 8)

    import requests
    response = requests.post(
        "https://g.api.mega.co.nz/cs",
        params={"id": str(int(time.time() * 1000))},
        json=[{"a": "g", "g": 1, "p": file_id}],
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    item = payload[0] if isinstance(payload, list) and payload else payload
    if isinstance(item, int):
        raise RuntimeError(f"Mega API error {item}")
    if not isinstance(item, dict) or not item.get("g"):
        raise RuntimeError("Mega tidak memberikan URL file")

    name = f"{file_id}.mp4"
    encrypted_attributes = item.get("at") or ""
    if encrypted_attributes:
        try:
            encrypted = _mega_b64decode(encrypted_attributes)
            cipher = AES.new(aes_key, AES.MODE_CBC, iv=b"\0" * 16)
            decoded = cipher.decrypt(encrypted).rstrip(b"\0").decode("utf-8", "replace")
            if decoded.startswith("MEGA"):
                attributes = json.loads(decoded[4:])
                name = str(attributes.get("n") or name)
        except Exception as exc:
            print(f"Mega attribute decode failed: {exc}")

    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else "mp4"
    info = {
        "file_id": file_id,
        "file_url": item["g"],
        "size": int(item.get("s") or 0),
        "name": name,
        "type": extension,
        "mime": mime,
        "aes_key": aes_key,
        "base_iv": base_iv,
        "cached_until": time.time() + MEGA_INFO_TTL,
    }
    MEGA_INFO_CACHE[url] = info
    return info


def create_mega_ctr_cipher(info, byte_offset=0):
    """Create an AES-CTR cipher positioned at an aligned byte offset."""
    if AES is None:
        raise RuntimeError("Dukungan AES Mega belum terpasang")
    if byte_offset < 0 or byte_offset % 16:
        raise ValueError("Offset Mega harus kelipatan 16 byte")
    counter = (int.from_bytes(info["base_iv"], "big") + (byte_offset // 16)) % (1 << 128)
    return AES.new(info["aes_key"], AES.MODE_CTR, nonce=b"", initial_value=counter)


def _resolve_mega_media(url):
    try:
        info = get_mega_file_info(url)
    except Exception as exc:
        print(f"Mega media resolve failed: {exc}")
        return None
    return {
        "url": _mega_proxy_url(url),
        "type": info["type"],
        "mime": info["mime"],
        "headers": {},
        "expires_at": None,
        "size": info["size"],
        "name": info["name"],
    }


def _rewrite_filedon_embed(html_text):
    """Make a Filedon embed usable from our player without leaking secrets."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    fallback_media_url = ""
    fallback_mime = "video/mp4"
    app_node = soup.find(id="app")
    if app_node:
        raw_page = app_node.get("data-page") or ""
        try:
            page = json.loads(html_lib.unescape(raw_page))
            props = page.get("props") if isinstance(page, dict) else None

            if isinstance(props, dict):
                media = props.get("media") if isinstance(props.get("media"), dict) else {}
                fallback_media_url = media.get("hls_url") or props.get("url") or ""
                if media.get("hls_url"):
                    fallback_mime = "application/vnd.apple.mpegurl"
                player_setting = props.get("player_setting")
                if isinstance(player_setting, dict):
                    # The upstream embed only allows Samehadaku domains. The
                    # relay is already fetching the page on behalf of that site.
                    player_setting["domain_whitelist_enabled"] = False
                    player_setting["domain_whitelist"] = []

                # The upstream data-page contains S3 access credentials in the
                # storage config. They are not required by the public player.
                files = props.get("files")
                if isinstance(files, dict):
                    fallback_mime = files.get("mime_type") or fallback_mime
                storage = files.get("storage") if isinstance(files, dict) else None
                if isinstance(storage, dict):
                    storage.pop("config", None)

            app_node["data-page"] = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    # Filedon's JS uses relative routes/assets. Keep those requests on the
    # original host even though the document itself is served by our relay.
    if soup.head and not soup.head.find("base"):
        base = soup.new_tag("base", href="https://filedon.co/")
        soup.head.insert(0, base)

    # Cross-origin module loading can vary between Filedon/Cloudflare
    # deployments. If their normal player did not render a <video>, fall back
    # to the signed media URL already present in the public Inertia payload.
    if soup.body and fallback_media_url:
        fallback = soup.new_tag("script")
        source_json = json.dumps(fallback_media_url, ensure_ascii=False)
        mime_json = json.dumps(fallback_mime, ensure_ascii=False)
        fallback.string = f"""
        (() => {{
          const source = {source_json};
          const mime = {mime_json};
          window.setTimeout(() => {{
            if (document.querySelector('video')) return;
            document.body.innerHTML = '';
            document.body.style.cssText = 'margin:0;background:#000;display:flex;align-items:center;justify-content:center;min-height:100vh';
            const video = document.createElement('video');
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.style.cssText = 'width:100%;height:100vh;background:#000';
            const item = document.createElement('source');
            item.src = source;
            item.type = mime;
            video.appendChild(item);
            document.body.appendChild(video);
          }}, 2500);
        }})();
        """
        soup.body.append(fallback)
    return str(soup)


def proxy_filedon_embed(url):
    """Fetch and sanitize a Filedon embed for the VIP Streaming provider."""
    if not _is_filedon_url(url):
        return None, 400

    import requests
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Referer": "https://v2.samehadaku.how/",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"Filedon embed relay failed: {exc}")
        return None, 502

    final_url = getattr(response, "url", url)
    if not _is_filedon_url(final_url):
        print(f"Filedon embed relay rejected redirect: {final_url}")
        return None, 502
    if response.status_code != 200:
        return None, response.status_code
    return _rewrite_filedon_embed(response.text), 200


def _filedon_page_props(html_text):
    soup = BeautifulSoup(html_text or "", "html.parser")
    app_node = soup.find(id="app")
    if not app_node:
        return None
    raw_page = app_node.get("data-page") or ""
    try:
        page = json.loads(raw_page)
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            page = json.loads(html_lib.unescape(raw_page))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    props = page.get("props") if isinstance(page, dict) else None
    return props if isinstance(props, dict) else None


def _filedon_expiry(media_url):
    """Read expiry from an S3/R2 signed URL when available."""
    try:
        query = parse_qs(urlparse(media_url).query)
        signed_at = query.get("X-Amz-Date", [""])[0]
        lifetime = int(query.get("X-Amz-Expires", [0])[0])
        if not signed_at or not lifetime:
            return None
        created = datetime.strptime(signed_at, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return int(created.timestamp()) + lifetime
    except (TypeError, ValueError):
        return None


def _resolve_filedon_media(url, referer="https://v2.samehadaku.how/"):
    """Resolve the signed media URL embedded in Filedon's Inertia payload."""
    if not _is_filedon_url(url):
        return None

    import requests
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Referer": referer or "https://v2.samehadaku.how/",
            },
            timeout=12,
        )
        final_url = getattr(response, "url", url)
        if not _is_filedon_url(final_url) or response.status_code != 200:
            return None
    except requests.RequestException as exc:
        print(f"Filedon media resolve failed: {exc}")
        return None

    props = _filedon_page_props(response.text)
    if not props:
        return None

    media = props.get("media") if isinstance(props.get("media"), dict) else {}
    media_url = media.get("hls_url") or props.get("url") or ""
    if not media_url:
        return None

    files = props.get("files") if isinstance(props.get("files"), dict) else {}
    extension = (files.get("extension") or urlparse(media_url).path.rsplit(".", 1)[-1] or "mp4").lower()
    mime = files.get("mime_type") or (
        "application/vnd.apple.mpegurl" if media_url.lower().split("?", 1)[0].endswith(".m3u8")
        else "video/x-matroska" if extension == "mkv"
        else f"video/{extension}"
    )
    return {
        "url": media_url,
        "type": "m3u8" if media_url.lower().split("?", 1)[0].endswith(".m3u8") else extension,
        "mime": mime,
        "headers": {"Referer": "https://v2.samehadaku.how/"},
        "expires_at": _filedon_expiry(media_url),
    }


def _direct_media_from_url(url):
    """Return metadata when a provider response is already a media URL."""
    if not url:
        return None
    path = (urlparse(url).path or "").lower()
    if not path.endswith(DIRECT_MEDIA_EXTENSIONS):
        return None

    media_type = path.rsplit(".", 1)[-1]
    mime = "application/vnd.apple.mpegurl" if media_type == "m3u8" else f"video/{media_type}"
    return {
        "url": url,
        "type": media_type,
        "mime": mime,
        "headers": {},
        "expires_at": None,
    }


def _resolve_blogger_media(url):
    """Resolve a public Blogger video.g URL to its temporary media URL.

    Blogger obtains the googlevideo URL through a batchexecute call.  yt-dlp
    already implements that protocol, so use it server-side instead of
    attempting to read the cross-origin iframe from the browser.
    """
    if not url or yt_dlp is None:
        return None

    try:
        host = (urlparse(url).hostname or "").lower()
        if host not in BLOGGER_HOSTS:
            return None

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "best[ext=mp4]/best",
            "socket_timeout": 10,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return None
        formats = [f for f in (info.get("formats") or []) if f.get("url")]
        progressive = [
            f for f in formats
            if (f.get("ext") or "").lower() == "mp4"
            and f.get("vcodec") != "none"
            and f.get("acodec") != "none"
        ]
        def format_score(fmt):
            try:
                height = float(fmt.get("height") or 0)
            except (TypeError, ValueError):
                height = 0
            try:
                bitrate = float(fmt.get("tbr") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            return height, bitrate

        progressive.sort(key=format_score, reverse=True)
        selected_format = progressive[0] if progressive else None
        media_url = (selected_format or {}).get("url") or info.get("url") or ""
        if not media_url and formats:
            selected_format = formats[-1]
            media_url = selected_format["url"]
        if not media_url:
            return None

        expiry = info.get("expiry")
        if not expiry:
            try:
                expiry = int(parse_qs(urlparse(media_url).query).get("expire", [0])[0])
            except (TypeError, ValueError):
                expiry = None

        return {
            "url": media_url,
            "type": (selected_format or {}).get("ext") or info.get("ext") or "mp4",
            "mime": (selected_format or {}).get("http_headers", {}).get(
                "Content-Type",
                info.get("http_headers", {}).get("Content-Type", "video/mp4"),
            ),
            "headers": (selected_format or {}).get("http_headers") or info.get("http_headers") or {},
            "expires_at": expiry,
        }
    except Exception as exc:
        print(f"Blogger media resolve failed: {exc}")
        return None

class SamehadakuScraper:
    def __init__(self):
        self.base_url = "https://v2.samehadaku.how"
        import requests
        self.req_session = requests.Session()
        try:
            if cloudscraper is not None:
                self.scraper = cloudscraper.create_scraper(
                    browser={
                        'browser': 'chrome',
                        'platform': 'windows',
                        'mobile': False
                    }
                )
            else:
                self.scraper = self.req_session
        except Exception as e:
            print(f"Cloudscraper creation error, fallback to requests: {e}")
            self.scraper = self.req_session

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': self.base_url
        }
    
    def _get_soup(self, url, retries=2):
        """Get BeautifulSoup object with retry logic and requests fallback"""
        for i in range(retries):
            try:
                response = self.scraper.get(url, headers=self.headers, timeout=8)
                if response.status_code == 200:
                    return BeautifulSoup(response.text, 'html.parser')
            except Exception as e:
                pass

            try:
                response = self.req_session.get(url, headers=self.headers, timeout=8)
                if response.status_code == 200:
                    return BeautifulSoup(response.text, 'html.parser')
            except Exception as e:
                pass

            time.sleep(0.5)
        return None
    
    def search_anime(self, query):
        """Search anime with complete details"""
        url = f"{self.base_url}/?s={quote(query)}"
        soup = self._get_soup(url)
        if not soup:
            return []
        
        results = []
        articles = soup.find_all('article', class_='animpost')
        
        for article in articles:
            try:
                a = article.find('a')
                if not a:
                    continue
                
                link = a.get('href', '')
                h2 = article.find(['h2', 'h3'])
                title = h2.text.strip() if h2 else a.get('title', 'Unknown')
                
                img = article.find('img')
                thumb = img.get('src') or img.get('data-src') or '' if img else ''
                
                eps_elem = article.find('div', class_='eps') or article.find('div', class_='type')
                eps = eps_elem.text.strip() if eps_elem else '?'
                
                genres = [c.replace('genre-', '').replace('-', ' ').title() for c in article.get('class', []) if c.startswith('genre-')]
                if not genres:
                    genre_elem = article.find('div', class_='genres')
                    if genre_elem:
                        genres = [g.text.strip() for g in genre_elem.find_all('a')]
                
                results.append({
                    'title': title,
                    'link': link,
                    'thumbnail': thumb,
                    'episodes': eps,
                    'genres': genres
                })
            except Exception as e:
                print(f"Error parsing search result: {e}")
                continue
        
        return results
    
    def get_anime_detail(self, anime_url):
        """Get complete anime details"""
        soup = self._get_soup(anime_url)
        if not soup:
            return None
        
        try:
            title_elem = soup.find(['h1', 'h2'], class_=lambda c: c and ('entry-title' in c or 'title' in c))
            title = title_elem.text.strip() if title_elem else 'Unknown'
            
            thumb_img = soup.select_one('.thumb img, .poster img, .animainfo img, .infoanime img, article img.anmsa, img[itemprop="image"]')
            thumb = thumb_img.get('src') or thumb_img.get('data-src') or '' if thumb_img else ''
            
            art = soup.find('article')
            paragraphs = []
            if art:
                for p in art.find_all('p'):
                    txt = p.text.strip()
                    if txt and not txt.startswith('Nonton Streaming') and not txt.startswith('Download'):
                        paragraphs.append(txt)
            synopsis = '\n\n'.join(paragraphs) if paragraphs else ''
            if not synopsis:
                syn_div = soup.select_one('.entry-content, .desc, .synopsis, .sinopsis')
                if syn_div:
                    synopsis = syn_div.text.strip()
            
            info = {}
            spe = soup.find(class_='spe') or soup.find('div', class_='infoanime') or soup.find('div', class_='infox')
            if spe:
                for span in spe.find_all('span'):
                    b = span.find('b')
                    if b:
                        key = b.text.strip().rstrip(':').lower()
                        b.extract()
                        val = span.text.strip()
                        info[key] = val
                    elif ':' in span.text:
                        key, value = span.text.split(':', 1)
                        info[key.strip().lower()] = value.strip()
            
            genre_container = soup.find(class_='genre-info') or soup.find(class_='genxed')
            genres = ', '.join([g.text.strip() for g in genre_container.find_all('a')]) if genre_container else info.get('genre', '')
            
            return {
                'title': title,
                'thumbnail': thumb,
                'synopsis': synopsis,
                'status': info.get('status', 'Unknown'),
                'total_episodes': info.get('total episode', info.get('episode', info.get('total_episodes', '?'))),
                'genres': genres or info.get('genre', ''),
                'release_date': info.get('released', info.get('release date', '')),
                'studio': info.get('studio', '')
            }
        except Exception as e:
            print(f"Error getting anime detail: {e}")
            return None
    
    def get_episodes(self, anime_url):
        """Get all episodes with pagination support"""
        soup = self._get_soup(anime_url)
        if not soup:
            return []
        
        episodes = []
        seen_links = set()
        
        try:
            ep_lists = soup.find_all(['div', 'ul'], class_=['epsleft', 'episodelist', 'daftar-eps', 'lchx', 'eplister'])
            
            for ep_list in ep_lists:
                links = ep_list.find_all('a')
                for link in links:
                    ep_url = link.get('href', '')
                    if not ep_url or ep_url in seen_links:
                        continue
                    
                    seen_links.add(ep_url)
                    ep_text = link.text.strip()
                    
                    ep_num = re.search(r'Episode\s*(\d+(?:\.\d+)?)', ep_text, re.IGNORECASE)
                    if not ep_num:
                        ep_num = re.search(r'Eps\s*(\d+(?:\.\d+)?)', ep_text, re.IGNORECASE)
                    if not ep_num:
                        ep_num = re.search(r'(\d+(?:\.\d+)?)', ep_text)
                    
                    episode_info = {
                        'number': ep_num.group(1) if ep_num else ep_text,
                        'title': ep_text,
                        'link': ep_url
                    }
                    episodes.append(episode_info)
            
            # HTML order differs between Samehadaku templates. Sort by the
            # numeric episode number so the newest episode is always first.
            def episode_sort_key(item):
                match = re.search(r'(\d+(?:\.\d+)?)', str(item.get('number') or ''))
                return float(match.group(1)) if match else -1

            episodes.sort(key=episode_sort_key, reverse=True)
            
        except Exception as e:
            print(f"Error getting episodes: {e}")
        
        return episodes
    
    def get_stream_and_download(self, episode_url):
        """Get streaming URLs across servers and qualities + download links"""
        soup = self._get_soup(episode_url)
        if not soup:
            return {'stream_url': None, 'servers': [], 'downloads': []}
        
        result = {
            'stream_url': None,
            'embed_url': None,
            'direct_url': None,
            'direct_type': None,
            'direct_mime': None,
            'direct_headers': {},
            'expires_at': None,
            'resolver': None,
            'servers': [],
            'downloads': []
        }
        
        try:
            # Samehadaku currently renders all providers as east_player_option
            # nodes (Blogspot, VIP Streaming, Wibufile, Mega, ...). Keep the
            # id selector as a fallback for theme changes.
            opts = soup.select('.east_player_option, [id*="player-option"]')
            ajax_url = f"{self.base_url}/wp-admin/admin-ajax.php"
            seen_option_keys = set()
            
            for opt in opts:
                post_id = opt.get('data-post')
                nume = opt.get('data-nume')
                type_val = opt.get('data-type', 'schtml')
                server_name = opt.get_text(' ', strip=True)
                
                if not post_id or not nume:
                    continue

                option_key = (str(post_id), str(nume), str(type_val))
                if option_key in seen_option_keys:
                    continue
                seen_option_keys.add(option_key)
                
                headers = self.headers.copy()
                headers['X-Requested-With'] = 'XMLHttpRequest'
                headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
                headers['Accept'] = '*/*'
                headers['Origin'] = self.base_url
                headers['Referer'] = episode_url
                
                try:
                    res = self.scraper.post(ajax_url, data={'action': 'player_ajax', 'post': post_id, 'nume': nume, 'type': type_val}, headers=headers, timeout=5)
                    if res.status_code == 200:
                        iframe_soup = BeautifulSoup(res.text, 'html.parser')
                        iframe = iframe_soup.find('iframe')
                        if iframe and (iframe.get('src') or iframe.get('data-src')):
                            src = iframe.get('src') or iframe.get('data-src')
                            src = html_lib.unescape(src).strip()
                            if src.startswith('//'):
                                src = 'https:' + src
                            provider = 'vip_streaming' if (
                                _is_filedon_url(src)
                                or ('vip' in server_name.lower() and 'stream' in server_name.lower())
                            ) else 'blogspot' if (urlparse(src).hostname or '').lower() in BLOGGER_HOSTS else 'mega' if (urlparse(src).hostname or '').lower() in MEGA_HOSTS else 'embed'
                            display_name = 'VIP Streaming' if provider == 'vip_streaming' else server_name
                            player_url = _filedon_proxy_url(src) if provider == 'vip_streaming' else src
                            direct_server = _direct_media_from_url(src)
                            server = {
                                'name': display_name or 'Server',
                                'url': player_url,
                                'source_url': src,
                                'provider': provider,
                                'type': 'direct' if direct_server else 'embed'
                            }
                            if direct_server:
                                server['direct_url'] = direct_server['url']
                                server['direct_type'] = direct_server['type']
                                server['direct_mime'] = direct_server['mime']
                                server['direct_headers'] = direct_server.get('headers') or {}
                                server['expires_at'] = direct_server.get('expires_at')
                            elif provider == 'blogspot':
                                blogger_media = _resolve_blogger_media(src)
                                if blogger_media:
                                    server['direct_url'] = _media_proxy_url(blogger_media['url'])
                                    server['source_direct_url'] = blogger_media['url']
                                    server['direct_type'] = blogger_media['type']
                                    server['direct_mime'] = blogger_media['mime']
                                    server['direct_headers'] = blogger_media['headers']
                                    server['expires_at'] = blogger_media['expires_at']
                                    server['type'] = 'direct'
                            elif provider == 'mega':
                                mega_media = _resolve_mega_media(src)
                                if mega_media:
                                    server['direct_url'] = mega_media['url']
                                    server['direct_type'] = mega_media['type']
                                    server['direct_mime'] = mega_media['mime']
                                    server['direct_headers'] = mega_media['headers']
                                    server['expires_at'] = mega_media['expires_at']
                                    server['type'] = 'direct'
                            elif provider == 'vip_streaming' and _is_filedon_url(src):
                                # Filedon wraps the signed R2/S3 URL in its
                                # Inertia payload. Keep it as metadata while
                                # the normal player URL remains the sanitized
                                # relay above.
                                filedon_media = _resolve_filedon_media(src, referer=episode_url)
                                if filedon_media:
                                    server['direct_url'] = filedon_media['url']
                                    server['direct_type'] = filedon_media['type']
                                    server['direct_mime'] = filedon_media['mime']
                                    server['direct_headers'] = filedon_media['headers']
                                    server['expires_at'] = filedon_media['expires_at']
                            result['servers'].append(server)
                except Exception as ex:
                    print(f"Error requesting player_ajax for {server_name}: {ex}")
            
            if result['servers']:
                best_server = None
                active_opt = soup.select_one('.east_player_option.on')
                active_name = active_opt.text.strip().lower() if active_opt else ''

                # Prefer browser-native progressive/HLS sources. Filedon's
                # VIP source is commonly MKV, which Chrome/Safari cannot play
                # reliably even though the signed URL itself is valid.
                def server_score(server):
                    name = (server.get('name') or '').lower()
                    media_type = (server.get('direct_type') or '').lower()
                    direct = bool(server.get('direct_url'))
                    browser_native = media_type in ('mp4', 'm3u8', 'webm', 'ogg')
                    quality = 1080 if '1080' in name or 'fullhd' in name else 720 if '720' in name or 'hd' in name else 480 if '480' in name else 0
                    return (
                        3 if direct and browser_native else 2 if direct else 1,
                        quality,
                        1 if server.get('provider') == 'blogspot' and direct else 0,
                    )

                best_server = max(result['servers'], key=server_score)
                
                if best_server and server_score(best_server)[0] <= 1 and active_name:
                    for srv in result['servers']:
                        if srv['name'].lower() == active_name:
                            best_server = srv
                            break
                    
                result['stream_url'] = best_server.get('direct_url') or best_server['url']
                result['embed_url'] = best_server['url']
                if best_server.get('direct_url'):
                    result['direct_url'] = best_server['direct_url']
                    result['direct_type'] = best_server.get('direct_type')
                    result['direct_mime'] = best_server.get('direct_mime')
                    result['direct_headers'] = best_server.get('direct_headers') or {}
                    result['expires_at'] = best_server.get('expires_at')
                    result['resolver'] = 'filedon' if best_server.get('provider') == 'vip_streaming' else 'direct'
            
            if not result['stream_url']:
                for iframe in soup.find_all('iframe'):
                    src = iframe.get('src', '')
                    if src and 'facebook.com' not in src and 'twitter.com' not in src and 'ads' not in src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        result['stream_url'] = _filedon_proxy_url(src)
                        result['embed_url'] = result['stream_url']
                        break

            direct_media = _direct_media_from_url(result.get('embed_url'))
            if direct_media and not result.get('direct_url'):
                result['direct_url'] = direct_media['url']
                result['direct_type'] = direct_media['type']
                result['direct_mime'] = direct_media['mime']
                result['direct_headers'] = direct_media['headers']
                result['expires_at'] = direct_media['expires_at']
                result['resolver'] = 'direct'
            elif not result.get('direct_url'):
                blogger_media = _resolve_blogger_media(result.get('embed_url'))
                if blogger_media:
                    result['direct_url'] = _media_proxy_url(blogger_media['url'])
                    result['direct_type'] = blogger_media['type']
                    result['direct_mime'] = blogger_media['mime']
                    result['direct_headers'] = blogger_media['headers']
                    result['expires_at'] = blogger_media['expires_at']
                    result['resolver'] = 'blogger'
            
            for container in soup.find_all('div', class_='download-eps'):
                format_p = container.find('p')
                format_name = format_p.text.strip() if format_p else ''
                for li in container.find_all('li'):
                    quality_elem = li.find(['strong', 'b'])
                    quality = quality_elem.text.strip() if quality_elem else 'Unknown'
                    for span in li.find_all('span'):
                        if span.find('strike'):
                            continue
                        a = span.find('a')
                        if a and a.get('href'):
                            result['downloads'].append({
                                'format': format_name,
                                'quality': quality,
                                'provider': a.text.strip(),
                                'url': a['href']
                            })
                            
        except Exception as e:
            print(f"Error getting stream/download: {e}")
        
        return result
    
    def get_homepage(self):
        """Get latest anime from homepage"""
        soup = self._get_soup(self.base_url)
        result = {
            'ongoing': [],
            'completed': [],
            'latest': []
        }
        if not soup:
            return result
        
        try:
            post_show = soup.find('div', class_='post-show')
            if post_show:
                for li in post_show.find_all('li'):
                    a_title = li.select_one('.dtla .entry-title a') or li.find('a')
                    img = li.find('img')
                    eps_author = li.find('author')
                    if a_title:
                        result['latest'].append({
                            'title': a_title.text.strip(),
                            'link': a_title.get('href', ''),
                            'thumbnail': img.get('src', '') if img else '',
                            'episode': eps_author.text.strip() if eps_author else '?'
                        })
            
            for li in soup.select('.topten-animesu li'):
                a = li.find('a')
                judul = li.find('span', class_='judul')
                img = li.find('img')
                if a:
                    result['ongoing'].append({
                        'title': judul.text.strip() if judul else a.text.strip(),
                        'link': a.get('href', ''),
                        'thumbnail': img.get('src', '') if img else '',
                        'episodes': 'Ongoing'
                    })

            # Fetch popular anime page 1 with try-catch so it never breaks homepage
            try:
                pop1 = self.get_popular_anime(1)
                if pop1:
                    seen_links = set(x['link'] for x in result['ongoing'] if isinstance(x, dict) and x.get('link'))
                    for item in pop1:
                        if isinstance(item, dict) and item.get('link') and item['link'] not in seen_links:
                            result['ongoing'].append(item)
                            seen_links.add(item['link'])
            except Exception as pe:
                print(f"Error fetching popular anime page 1: {pe}")
                    
        except Exception as e:
            print(f"Error getting homepage: {e}")
        
        return result

    def get_popular_anime(self, page=1):
        """Scrape popular anime directory list"""
        url = f"{self.base_url}/daftar-anime-2/page/{page}/?order=popular"
        soup = self._get_soup(url)
        if not soup:
            return []
        
        results = []
        articles = soup.find_all('article', class_='animpost')
        for article in articles:
            try:
                a = article.find('a')
                if not a:
                    continue
                link = a.get('href', '')
                h2 = article.find(['h2', 'h3'])
                title = h2.text.strip() if h2 else a.get('title', 'Unknown')
                img = article.find('img')
                thumb = img.get('src') or img.get('data-src') or '' if img else ''
                score_elem = article.find('div', class_='score') or article.find('div', class_='type')
                eps = score_elem.text.strip() if score_elem else 'Anime'
                
                results.append({
                    'title': title,
                    'link': link,
                    'thumbnail': thumb,
                    'episodes': eps
                })
            except Exception as e:
                continue
        return results

import threading

# ── In-Memory TTL Cache ────────────────────────────────────────────────────────
class TTLCache:
    def __init__(self, default_ttl=300, max_size=300):
        self._store = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry and time.time() < entry[1]:
                return entry[0]
            return None

    def set(self, key, value, ttl=None):
        with self._lock:
            if len(self._store) >= self._max_size:
                oldest = min(self._store, key=lambda k: self._store[k][1])
                del self._store[oldest]
            self._store[key] = (value, time.time() + (ttl or self._default_ttl))

ANIME_CACHE = TTLCache(default_ttl=300)

_scraper_instance = None
def get_scraper():
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = SamehadakuScraper()
    return _scraper_instance

def build_response(path: str, params: dict, body_data: dict = None):
    try:
        scraper = get_scraper()
    except Exception as e:
        print(f"Error initializing scraper: {e}")
        return {"status": "success", "data": {"ongoing": [], "completed": [], "latest": []}}, 200

    if path.endswith("/home") or path.endswith("/anime"):
        cache_key = "anime:home"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200
        try:
            data = scraper.get_homepage() or {"ongoing": [], "completed": [], "latest": []}
            if data and (data.get('latest') or data.get('ongoing')):
                ANIME_CACHE.set(cache_key, data, ttl=600)  # 10 mins cache
            return {"status": "success", "data": data}, 200
        except Exception as e:
            print(f"Error in anime home response: {e}")
            return {"status": "success", "data": {"ongoing": [], "completed": [], "latest": []}}, 200

    elif path.endswith("/popular"):
        page_val = params.get("page", ["1"])[0] if isinstance(params.get("page"), list) else params.get("page", "1")
        try:
            page = int(page_val)
        except ValueError:
            page = 1
        cache_key = f"anime:popular:{page}"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200
        try:
            results = scraper.get_popular_anime(page)
            if results:
                ANIME_CACHE.set(cache_key, results, ttl=600)
            return {"status": "success", "data": results or []}, 200
        except Exception as e:
            print(f"Error in anime popular response: {e}")
            return {"status": "success", "data": []}, 200
        
    elif path.endswith("/search"):
        q = params.get("q", [""])[0] if isinstance(params.get("q"), list) else params.get("q", "")
        if not q:
            return {"status": "error", "message": "Query 'q' required"}, 400
        cache_key = f"anime:search:{q.strip().lower()}"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200
        try:
            results = scraper.search_anime(q)
            if results:
                ANIME_CACHE.set(cache_key, results, ttl=600)
            return {"status": "success", "data": results or []}, 200
        except Exception as e:
            print(f"Error in anime search response: {e}")
            return {"status": "success", "data": []}, 200
        
    elif path.endswith("/detail"):
        url = ""
        if body_data and isinstance(body_data, dict):
            url = body_data.get("url", "")
        if not url and "url" in params:
            url = params.get("url", [""])[0] if isinstance(params.get("url"), list) else params.get("url", "")
        if not url:
            return {"status": "error", "message": "Parameter 'url' required"}, 400
            
        cache_key = f"anime:detail:{url.strip()}"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200

        detail = scraper.get_anime_detail(url)
        if not detail:
            return {"status": "error", "message": "Failed to fetch anime detail"}, 500
        episodes = scraper.get_episodes(url)
        detail["episodes"] = episodes
        ANIME_CACHE.set(cache_key, detail, ttl=1800)  # 30 mins cache
        return {"status": "success", "data": detail}, 200
        
    elif path.endswith("/stream"):
        url = ""
        if body_data and isinstance(body_data, dict):
            url = body_data.get("url", "")
        if not url and "url" in params:
            url = params.get("url", [""])[0] if isinstance(params.get("url"), list) else params.get("url", "")
        if not url:
            return {"status": "error", "message": "Parameter 'url' required"}, 400

        cache_key = f"anime:stream:{url.strip()}"

        # --- Integrasi Cloudflare Stream & R2: Cek Arsip Terlebih Dahulu ---
        try:
            from api.r2_storage import is_configured as r2_is_configured, get_presigned_url, key_exists
            from api.cloudflare_stream import is_configured as stream_is_configured, get_hls_url, get_iframe_url
            from api.anime_archiver import get_r2_episode, trigger_background_archive, _upsert_r2_episode, is_storage_configured
        except ImportError:
            from r2_storage import is_configured as r2_is_configured, get_presigned_url, key_exists
            from cloudflare_stream import is_configured as stream_is_configured, get_hls_url, get_iframe_url
            from anime_archiver import get_r2_episode, trigger_background_archive, _upsert_r2_episode, is_storage_configured

        storage_stream_data = None
        archived_episode = get_r2_episode(url)

        if archived_episode and archived_episode.get("status") == "done":
            # 1. Cek jika video tersimpan di Cloudflare Stream
            stream_uid = archived_episode.get("stream_uid")
            if stream_uid or archived_episode.get("storage_provider") == "stream":
                hls_url = archived_episode.get("stream_hls_url") or get_hls_url(stream_uid or "")
                embed_url = archived_episode.get("stream_embed_url") or get_iframe_url(stream_uid or "")
                if hls_url:
                    storage_stream_data = {
                        'stream_url': hls_url,
                        'embed_url': embed_url,
                        'direct_url': hls_url,
                        'direct_type': 'm3u8',
                        'direct_mime': 'application/vnd.apple.mpegurl',
                        'direct_headers': {},
                        'expires_at': None,
                        'resolver': 'cloudflare_stream',
                        'servers': [
                            {
                                'name': 'Cloudflare Stream (Adaptive HLS)',
                                'url': hls_url,
                                'source_url': hls_url,
                                'provider': 'cloudflare_stream',
                                'type': 'hls',
                                'direct_url': hls_url,
                                'direct_type': 'm3u8',
                                'direct_mime': 'application/vnd.apple.mpegurl',
                                'direct_headers': {},
                                'expires_at': None
                            }
                        ],
                        'downloads': []
                    }
                    ANIME_CACHE.set(cache_key, storage_stream_data, ttl=7200 - 300)
                    return {"status": "success", "data": storage_stream_data, "source": "cloudflare_stream"}, 200

            # 2. Cek jika video tersimpan di Cloudflare R2
            r2_key = archived_episode.get("r2_key") or ""
            r2_quality = archived_episode.get("quality") or "mp4"
            if r2_key and (r2_is_configured() or archived_episode.get("r2_url")):
                r2_url = archived_episode.get("r2_url") or get_presigned_url(r2_key)
                if r2_url:
                    r2_mime = "video/mp4" if r2_quality == "mp4" else "application/vnd.apple.mpegurl" if r2_quality == "m3u8" else f"video/{r2_quality}"
                    storage_stream_data = {
                        'stream_url': r2_url,
                        'embed_url': r2_url,
                        'direct_url': r2_url,
                        'direct_type': r2_quality,
                        'direct_mime': r2_mime,
                        'direct_headers': {},
                        'expires_at': None,
                        'resolver': 'r2',
                        'servers': [
                            {
                                'name': 'Cloudflare R2 (Self-Hosted)',
                                'url': r2_url,
                                'source_url': r2_url,
                                'provider': 'r2',
                                'type': 'direct',
                                'direct_url': r2_url,
                                'direct_type': r2_quality,
                                'direct_mime': r2_mime,
                                'direct_headers': {},
                                'expires_at': None
                            }
                        ],
                        'downloads': []
                    }
                    ANIME_CACHE.set(cache_key, storage_stream_data, ttl=7200 - 300)
                    return {"status": "success", "data": storage_stream_data, "source": "r2"}, 200

        # Cek Cache umum jika belum di-resolve dari storage
        nocache = "nocache" in params or "refresh" in params
        if not nocache:
            cached = ANIME_CACHE.get(cache_key)
            if cached:
                # Trigger background archive jika belum jalan
                if is_storage_configured():
                    trigger_background_archive(url)
                return {"status": "success", "data": cached, "cached": True}, 200

        # Fallback ke scraper biasa jika belum ada di arsip
        stream_data = scraper.get_stream_and_download(url)
        if stream_data and stream_data.get('stream_url'):
            ttl = 300 if stream_data.get('direct_url') else 3600
            expires_at = stream_data.get('expires_at')
            if expires_at:
                try:
                    ttl = max(30, min(ttl, int(expires_at) - int(time.time()) - 60))
                except (TypeError, ValueError):
                    pass
            ANIME_CACHE.set(cache_key, stream_data, ttl=ttl)

            # Trigger background download ke Cloudflare Stream / R2
            if is_storage_configured():
                trigger_background_archive(url)

                
        return {"status": "success", "data": stream_data}, 200

    return {"status": "error", "message": f"Route tidak dikenal: {path}"}, 400

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path.endswith("/embed"):
            embed_url = params.get("url", [""])[0].strip()
            try:
                try:
                    from .lib.proxy_signing import validate_proxy_signature
                except ImportError:
                    from lib.proxy_signing import validate_proxy_signature
                valid = validate_proxy_signature(embed_url, params.get("exp", [""])[0], params.get("sig", [""])[0])
            except Exception:
                valid = False
            if not valid:
                self._send_json({"status": "error", "message": "URL Filedon tidak valid atau sudah kedaluwarsa"}, 403)
                return
            html, code = proxy_filedon_embed(embed_url)
            if html is None:
                self._send_json({"status": "error", "message": "Filedon embed tidak tersedia"}, code)
                return
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        data, code = build_response(path, params)
        self._send_json(data, code)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        content_length = int(self.headers.get('Content-Length', 0))
        body_data = {}
        if content_length > 0:
            try:
                body_bytes = self.rfile.read(content_length)
                body_data = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                body_data = {}
        data, code = build_response(path, params, body_data)
        self._send_json(data, code)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Subscription-Token")

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
