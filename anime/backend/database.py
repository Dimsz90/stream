from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

def get_utc_now():
    return datetime.now(timezone.utc)

class Anime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500))
    link = db.Column(db.String(500), unique=True)
    thumbnail = db.Column(db.String(500))
    total_episodes = db.Column(db.String(50))
    synopsis = db.Column(db.Text)
    genre = db.Column(db.String(500))
    status = db.Column(db.String(50))
    added_at = db.Column(db.DateTime, default=get_utc_now)

class Episode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))
    episode_number = db.Column(db.String(50))
    title = db.Column(db.String(500))
    link = db.Column(db.String(500))
    stream_url = db.Column(db.Text)
    download_links = db.Column(db.Text)  # JSON string
    watched = db.Column(db.Boolean, default=False)
    watched_at = db.Column(db.DateTime)

class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))
    episode_id = db.Column(db.Integer, db.ForeignKey('episode.id'))
    progress = db.Column(db.String(50))  # timestamp progress
    created_at = db.Column(db.DateTime, default=get_utc_now)

class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))
    episode_id = db.Column(db.Integer, db.ForeignKey('episode.id'))
    watched_at = db.Column(db.DateTime, default=get_utc_now)
    progress = db.Column(db.String(50))