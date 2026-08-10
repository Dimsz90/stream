from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from database import db, Anime, Episode, Bookmark, History, get_utc_now
from scraper import SamehadakuScraper
import json
import re
from datetime import datetime, timezone
import os

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///anime.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

scraper = SamehadakuScraper()

# Create database tables
with app.app_context():
    db.create_all()

# Helper function for episode digit parsing
def extract_digits(val):
    if not val:
        return 0
    match = re.search(r'\d+', str(val))
    return int(match.group()) if match else 0

# ==================== API Routes ====================

@app.route('/')
def index():
    return jsonify({
        'name': 'Anime Streaming API',
        'version': '2.0',
        'endpoints': [
            '/api/home',
            '/api/search?q=',
            '/api/anime/detail',
            '/api/stream',
            '/api/bookmark',
            '/api/bookmarks',
            '/api/history',
            '/api/continue-watching',
            '/api/stats'
        ]
    })

@app.route('/api/home')
def home():
    """Get homepage content"""
    try:
        data = scraper.get_homepage()
        
        # Add bookmark status from database
        for section in ['ongoing', 'completed', 'latest']:
            for anime in data.get(section, []):
                db_anime = Anime.query.filter_by(link=anime['link']).first()
                if db_anime:
                    anime['bookmarked'] = Bookmark.query.filter_by(anime_id=db_anime.id).first() is not None
                else:
                    anime['bookmarked'] = False
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/search')
def search():
    """Search anime"""
    query = request.args.get('q', '')
    if not query:
        return jsonify({'status': 'error', 'message': 'Query required'}), 400
    
    try:
        results = scraper.search_anime(query)
        
        # Check bookmarks
        for anime in results:
            db_anime = Anime.query.filter_by(link=anime['link']).first()
            if db_anime:
                anime['bookmarked'] = Bookmark.query.filter_by(anime_id=db_anime.id).first() is not None
            else:
                anime['bookmarked'] = False
        
        return jsonify({'status': 'success', 'data': results})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/anime/detail', methods=['POST'])
def anime_detail():
    """Get anime detail and cache to database"""
    data = request.json or {}
    url = data.get('url', '')
    
    if not url:
        return jsonify({'status': 'error', 'message': 'URL required'}), 400
    
    try:
        force_refresh = data.get('force_refresh', False)
        anime = Anime.query.filter_by(link=url).first()
        
        if not anime or force_refresh or anime.status == 'Ongoing':
            detail = scraper.get_anime_detail(url)
            if detail:
                if not anime:
                    anime = Anime(
                        title=detail['title'],
                        link=url,
                        thumbnail=detail['thumbnail'],
                        total_episodes=str(detail['total_episodes']),
                        synopsis=detail['synopsis'],
                        genre=detail['genres'],
                        status=detail['status']
                    )
                    db.session.add(anime)
                else:
                    anime.total_episodes = str(detail['total_episodes'])
                    anime.status = detail['status']
                    if detail.get('thumbnail'):
                        anime.thumbnail = detail['thumbnail']
                db.session.commit()
            elif not anime:
                return jsonify({'status': 'error', 'message': 'Failed to fetch detail'}), 500
        
        # Get episodes
        episodes = scraper.get_episodes(url)
        
        # Save episodes to database
        for ep in episodes:
            existing = Episode.query.filter_by(link=ep['link']).first()
            if not existing:
                new_ep = Episode(
                    anime_id=anime.id,
                    episode_number=str(ep['number']),
                    title=ep['title'],
                    link=ep['link']
                )
                db.session.add(new_ep)
        db.session.commit()
        
        # Get watch progress
        watched_eps = Episode.query.filter_by(anime_id=anime.id, watched=True).count()
        
        return jsonify({
            'status': 'success',
            'data': {
                'id': anime.id,
                'title': anime.title,
                'thumbnail': anime.thumbnail,
                'synopsis': anime.synopsis,
                'genre': anime.genre,
                'status': anime.status,
                'total_episodes': anime.total_episodes,
                'watched_episodes': watched_eps,
                'episodes': episodes
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stream', methods=['POST'])
def get_stream():
    """Get stream URL and cache it"""
    data = request.json or {}
    url = data.get('url', '')
    
    if not url:
        return jsonify({'status': 'error', 'message': 'URL required'}), 400
    
    try:
        # Check cache
        episode = Episode.query.filter_by(link=url).first()
        
        if episode and episode.stream_url:
            try:
                stream_data = json.loads(episode.stream_url)
            except Exception:
                stream_data = None
        else:
            stream_data = None
            
        if not stream_data or not stream_data.get('stream_url') or 'servers' not in stream_data:
            stream_data = scraper.get_stream_and_download(url)
            
            if episode and stream_data.get('stream_url'):
                episode.stream_url = json.dumps(stream_data)
                episode.download_links = json.dumps(stream_data.get('downloads', []))
                db.session.commit()
        
        return jsonify({
            'status': 'success',
            'data': stream_data
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/bookmark', methods=['POST'])
def add_bookmark():
    """Add/remove bookmark"""
    data = request.json or {}
    anime_url = data.get('anime_url', '')
    episode_url = data.get('episode_url', '')
    action = data.get('action', 'add')  # add or remove
    
    try:
        anime = Anime.query.filter_by(link=anime_url).first() if anime_url else None
        
        if action == 'add':
            if not anime and anime_url:
                detail = scraper.get_anime_detail(anime_url)
                if detail:
                    anime = Anime(
                        title=detail['title'],
                        link=anime_url,
                        thumbnail=detail['thumbnail'],
                        total_episodes=str(detail['total_episodes']),
                        synopsis=detail['synopsis'],
                        genre=detail['genres'],
                        status=detail['status']
                    )
                    db.session.add(anime)
                    db.session.commit()
            
            if anime:
                episode = Episode.query.filter_by(link=episode_url).first() if episode_url else None
                existing = Bookmark.query.filter_by(anime_id=anime.id).first()
                if not existing:
                    bookmark = Bookmark(
                        anime_id=anime.id,
                        episode_id=episode.id if episode else None
                    )
                    db.session.add(bookmark)
                    db.session.commit()
        else:
            if anime:
                Bookmark.query.filter_by(anime_id=anime.id).delete()
                db.session.commit()
        
        return jsonify({'status': 'success', 'message': f'Bookmark {action}ed'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/bookmarks')
def get_bookmarks():
    """Get all bookmarks"""
    try:
        bookmarks = Bookmark.query.order_by(Bookmark.created_at.desc()).all()
        data = []
        
        for bm in bookmarks:
            anime = Anime.query.get(bm.anime_id)
            if anime:
                data.append({
                    'id': bm.id,
                    'anime_title': anime.title,
                    'anime_url': anime.link,
                    'thumbnail': anime.thumbnail,
                    'progress': bm.progress,
                    'created_at': bm.created_at.isoformat() if bm.created_at else ''
                })
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/history', methods=['POST'])
def add_history():
    """Add to watch history"""
    data = request.json or {}
    anime_url = data.get('anime_url', '')
    episode_url = data.get('episode_url', '')
    progress = data.get('progress', '0')
    
    try:
        anime = Anime.query.filter_by(link=anime_url).first()
        episode = Episode.query.filter_by(link=episode_url).first()
        
        if anime and episode:
            # Mark episode as watched
            episode.watched = True
            episode.watched_at = get_utc_now()
            
            history = History(
                anime_id=anime.id,
                episode_id=episode.id,
                progress=progress
            )
            db.session.add(history)
            db.session.commit()
        
        return jsonify({'status': 'success', 'message': 'History added'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/history')
def get_history():
    """Get watch history"""
    try:
        history = History.query.order_by(History.watched_at.desc()).limit(50).all()
        data = []
        
        for h in history:
            anime = Anime.query.get(h.anime_id)
            episode = Episode.query.get(h.episode_id)
            
            if anime and episode:
                data.append({
                    'id': h.id,
                    'anime_title': anime.title,
                    'anime_url': anime.link,
                    'thumbnail': anime.thumbnail,
                    'episode_number': episode.episode_number,
                    'episode_url': episode.link,
                    'progress': h.progress,
                    'watched_at': h.watched_at.isoformat() if h.watched_at else ''
                })
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/continue-watching')
def continue_watching():
    """Get anime that user hasn't finished"""
    try:
        histories = History.query.order_by(History.watched_at.desc()).all()
        seen_anime = set()
        data = []
        
        for h in histories:
            if h.anime_id not in seen_anime:
                anime = Anime.query.get(h.anime_id)
                episode = Episode.query.get(h.episode_id)
                
                if anime and episode:
                    next_ep = Episode.query.filter(
                        Episode.anime_id == anime.id,
                        Episode.id > episode.id
                    ).first()
                    
                    total_eps = extract_digits(anime.total_episodes)
                    last_ep = extract_digits(episode.episode_number)
                    pct = round((last_ep / total_eps * 100), 1) if total_eps > 0 else 0
                    
                    data.append({
                        'anime_title': anime.title,
                        'anime_url': anime.link,
                        'thumbnail': anime.thumbnail,
                        'last_watched': episode.episode_number,
                        'next_episode': next_ep.episode_number if next_ep else None,
                        'next_episode_url': next_ep.link if next_ep else None,
                        'progress_percentage': pct
                    })
                    seen_anime.add(h.anime_id)
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ==================== Advanced Features ====================

@app.route('/api/anime/random')
def random_anime():
    """Get random anime from bookmarks"""
    try:
        import random
        bookmarks = Bookmark.query.all()
        if not bookmarks:
            return jsonify({'status': 'error', 'message': 'No bookmarks'}), 404
        
        bm = random.choice(bookmarks)
        anime = Anime.query.get(bm.anime_id)
        
        return jsonify({
            'status': 'success',
            'data': {
                'title': anime.title,
                'url': anime.link,
                'thumbnail': anime.thumbnail
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/export/bookmarks')
def export_bookmarks():
    """Export bookmarks as JSON"""
    try:
        bookmarks = Bookmark.query.all()
        data = []
        
        for bm in bookmarks:
            anime = Anime.query.get(bm.anime_id)
            if anime:
                data.append({
                    'title': anime.title,
                    'url': anime.link,
                    'progress': bm.progress,
                    'date': bm.created_at.isoformat() if bm.created_at else ''
                })
        
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get watching statistics"""
    try:
        total_anime = Anime.query.count()
        total_watched = Episode.query.filter_by(watched=True).count()
        total_bookmarks = Bookmark.query.count()
        total_hours = total_watched * 24 / 60  # Assuming ~24 min per episode
        
        return jsonify({
            'status': 'success',
            'data': {
                'total_anime_in_db': total_anime,
                'total_episodes_watched': total_watched,
                'total_bookmarks': total_bookmarks,
                'estimated_hours_watched': round(total_hours, 1)
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')