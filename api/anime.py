"""
api/anime.py
Handler endpoint Anime streaming berbasis scraper Samehadaku.
"""
import os, sys, json, time, re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cloudscraper
from bs4 import BeautifulSoup

class SamehadakuScraper:
    def __init__(self):
        self.base_url = "https://v2.samehadaku.how"
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': self.base_url
        }
    
    def _get_soup(self, url, retries=3):
        """Get BeautifulSoup object with retry logic"""
        for i in range(retries):
            try:
                response = self.scraper.get(url, headers=self.headers, timeout=10)
                if response.status_code == 200:
                    return BeautifulSoup(response.text, 'html.parser')
                time.sleep(1)
            except Exception as e:
                if i == retries - 1:
                    print(f"Scraper request error for {url}: {e}")
                time.sleep(1)
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
                    
                    ep_num = re.search(r'Episode\s*(\d+)', ep_text, re.IGNORECASE)
                    if not ep_num:
                        ep_num = re.search(r'Eps\s*(\d+)', ep_text, re.IGNORECASE)
                    if not ep_num:
                        ep_num = re.search(r'(\d+)', ep_text)
                    
                    episode_info = {
                        'number': ep_num.group(1) if ep_num else ep_text,
                        'title': ep_text,
                        'link': ep_url
                    }
                    episodes.append(episode_info)
            
            episodes.reverse()
            
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
            'servers': [],
            'downloads': []
        }
        
        try:
            opts = soup.find_all('div', class_='east_player_option') or soup.find_all('div', id=lambda i: i and 'player-option' in i)
            ajax_url = f"{self.base_url}/wp-admin/admin-ajax.php"
            
            for opt in opts:
                post_id = opt.get('data-post')
                nume = opt.get('data-nume')
                type_val = opt.get('data-type', 'schtml')
                server_name = opt.text.strip()
                
                if not post_id or not nume:
                    continue
                
                headers = self.headers.copy()
                headers['X-Requested-With'] = 'XMLHttpRequest'
                headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
                
                try:
                    res = self.scraper.post(ajax_url, data={'action': 'player_ajax', 'post': post_id, 'nume': nume, 'type': type_val}, headers=headers, timeout=5)
                    if res.status_code == 200:
                        iframe_soup = BeautifulSoup(res.text, 'html.parser')
                        iframe = iframe_soup.find('iframe')
                        if iframe and iframe.get('src'):
                            src = iframe['src']
                            if src.startswith('//'):
                                src = 'https:' + src
                            result['servers'].append({
                                'name': server_name,
                                'url': src
                            })
                except Exception as ex:
                    print(f"Error requesting player_ajax for {server_name}: {ex}")
            
            if result['servers']:
                best_server = None
                active_opt = soup.select_one('.east_player_option.on')
                active_name = active_opt.text.strip().lower() if active_opt else ''
                
                for srv in result['servers']:
                    srv_lower = srv['name'].lower()
                    if 'premium' in srv_lower or '1080' in srv_lower or '720' in srv_lower:
                        best_server = srv
                        break
                
                if not best_server and active_name:
                    for srv in result['servers']:
                        if srv['name'].lower() == active_name:
                            best_server = srv
                            break
                            
                if not best_server:
                    best_server = result['servers'][0]
                    
                result['stream_url'] = best_server['url']
            
            if not result['stream_url']:
                for iframe in soup.find_all('iframe'):
                    src = iframe.get('src', '')
                    if src and 'facebook.com' not in src and 'twitter.com' not in src and 'ads' not in src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        result['stream_url'] = src
                        break
            
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
    scraper = get_scraper()
    
    if path.endswith("/home") or path.endswith("/anime"):
        cache_key = "anime:home"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200
        try:
            data = scraper.get_homepage()
            if data and (data.get('latest') or data.get('ongoing')):
                ANIME_CACHE.set(cache_key, data, ttl=600)  # 10 mins cache
            return {"status": "success", "data": data}, 200
        except Exception as e:
            print(f"Error in anime home response: {e}")
            return {"status": "error", "message": str(e)}, 500

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
        results = scraper.get_popular_anime(page)
        if results:
            ANIME_CACHE.set(cache_key, results, ttl=600)
        return {"status": "success", "data": results}, 200
        
    elif path.endswith("/search"):
        q = params.get("q", [""])[0] if isinstance(params.get("q"), list) else params.get("q", "")
        if not q:
            return {"status": "error", "message": "Query 'q' required"}, 400
        cache_key = f"anime:search:{q.strip().lower()}"
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200
        results = scraper.search_anime(q)
        if results:
            ANIME_CACHE.set(cache_key, results, ttl=600)
        return {"status": "success", "data": results}, 200
        
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
        cached = ANIME_CACHE.get(cache_key)
        if cached:
            return {"status": "success", "data": cached, "cached": True}, 200

        stream_data = scraper.get_stream_and_download(url)
        if stream_data and stream_data.get('stream_url'):
            ANIME_CACHE.set(cache_key, stream_data, ttl=3600)  # 1 hour cache
        return {"status": "success", "data": stream_data}, 200

    return {"status": "error", "message": f"Route tidak dikenal: {path}"}, 400

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
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
