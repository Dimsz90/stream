import requests
import cloudscraper
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urljoin
import time

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
                time.sleep(2)
        return None
    
    def search_anime(self, query):
        """Search anime with complete details"""
        url = f"{self.base_url}/?s={query}"
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
                
                # Get thumbnail
                img = article.find('img')
                thumb = img.get('src') or img.get('data-src') or '' if img else ''
                
                # Get episode info / type
                eps_elem = article.find('div', class_='eps') or article.find('div', class_='type')
                eps = eps_elem.text.strip() if eps_elem else '?'
                
                # Get genres
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
            # Title
            title_elem = soup.find(['h1', 'h2'], class_=lambda c: c and ('entry-title' in c or 'title' in c))
            title = title_elem.text.strip() if title_elem else 'Unknown'
            
            # Thumbnail
            thumb_img = soup.select_one('.thumb img, .poster img, .animainfo img, .infoanime img, article img.anmsa, img[itemprop="image"]')
            thumb = thumb_img.get('src') or thumb_img.get('data-src') or '' if thumb_img else ''
            
            # Synopsis
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
            
            # Info box
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
            
            # Genres
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
            # Find episode lists
            ep_lists = soup.find_all(['div', 'ul'], class_=['epsleft', 'episodelist', 'daftar-eps', 'lchx', 'eplister'])
            
            for ep_list in ep_lists:
                links = ep_list.find_all('a')
                for link in links:
                    ep_url = link.get('href', '')
                    if not ep_url or ep_url in seen_links:
                        continue
                    
                    seen_links.add(ep_url)
                    ep_text = link.text.strip()
                    
                    # Extract episode number
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
            
            # Reverse so episode 1 comes first
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
            # 1. Parse player streaming servers from div.east_player_option
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
            
            # Prefer Premium HD, 1080p, 720p, or active server as default stream_url
            if result['servers']:
                best_server = None
                active_opt = soup.select_one('.east_player_option.on')
                active_name = active_opt.text.strip().lower() if active_opt else ''
                
                # Priority 1: Premium HD, 1080p, 720p
                for srv in result['servers']:
                    srv_lower = srv['name'].lower()
                    if 'premium' in srv_lower or '1080' in srv_lower or '720' in srv_lower:
                        best_server = srv
                        break
                
                # Priority 2: Active server on page load
                if not best_server and active_name:
                    for srv in result['servers']:
                        if srv['name'].lower() == active_name:
                            best_server = srv
                            break
                            
                # Priority 3: First available server
                if not best_server:
                    best_server = result['servers'][0]
                    
                result['stream_url'] = best_server['url']
            
            # Fallback for stream_url if player_ajax didn't work
            if not result['stream_url']:
                for iframe in soup.find_all('iframe'):
                    src = iframe.get('src', '')
                    if src and 'facebook.com' not in src and 'twitter.com' not in src and 'ads' not in src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        result['stream_url'] = src
                        break
            
            # 2. Download links from div.download-eps
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
        if not soup:
            return {'ongoing': [], 'completed': [], 'latest': []}
        
        result = {
            'ongoing': [],
            'completed': [],
            'latest': []
        }
        
        try:
            # 1. Latest episodes from div.post-show
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
            
            # 2. Top 10 / Ongoing from topten-animesu
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
                    
        except Exception as e:
            print(f"Error getting homepage: {e}")
        
        return result