-- SQL Migration: Membuat tabel r2_episodes untuk self-hosting anime via Cloudflare R2
-- Jalankan query SQL ini di SQL Editor pada Dashboard Supabase Anda.

CREATE TABLE IF NOT EXISTS r2_episodes (
    id               BIGSERIAL PRIMARY KEY,
    episode_url      TEXT NOT NULL,          -- URL episode Samehadaku (e.g. https://v2.samehadaku.how/boku-no-hero-episode-12/)
    anime_url        TEXT NOT NULL,          -- URL main anime Samehadaku (e.g. https://v2.samehadaku.how/anime/boku-no-hero/)
    storage_provider TEXT NOT NULL DEFAULT 'r2', -- Provider: 'r2' | 'stream'
    r2_key           TEXT NOT NULL DEFAULT '', -- Path objek di R2 jika pakai R2 (e.g. anime/boku-no-hero/episode-12.mp4)
    r2_url           TEXT,                   -- URL direct/presigned file R2
    stream_uid       TEXT,                   -- Media UID video di Cloudflare Stream
    stream_hls_url   TEXT,                   -- URL manifest HLS (.m3u8) Cloudflare Stream
    stream_embed_url TEXT,                   -- URL embed iframe Cloudflare Stream
    file_size        BIGINT,                 -- Ukuran file dalam bytes
    duration         INT,                    -- Durasi video (detik, jika ada)
    quality          TEXT,                   -- Kualitas/extensi file (e.g. mp4, m3u8)
    status           TEXT NOT NULL DEFAULT 'pending', -- Status: pending | downloading | done | error
    error_msg        TEXT,                   -- Log error jika gagal
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT r2_episodes_episode_url_key UNIQUE (episode_url)
);

-- Indeks untuk meningkatkan performa query
CREATE INDEX IF NOT EXISTS r2_episodes_anime_url_idx ON r2_episodes (anime_url);
CREATE INDEX IF NOT EXISTS r2_episodes_status_idx ON r2_episodes (status);

-- Jika tabel sudah ada sebelumnya, jalankan ALTER TABLE berikut untuk menambahkan kolom Cloudflare Stream:
ALTER TABLE r2_episodes ADD COLUMN IF NOT EXISTS storage_provider TEXT DEFAULT 'r2';
ALTER TABLE r2_episodes ADD COLUMN IF NOT EXISTS stream_uid TEXT;
ALTER TABLE r2_episodes ADD COLUMN IF NOT EXISTS stream_hls_url TEXT;
ALTER TABLE r2_episodes ADD COLUMN IF NOT EXISTS stream_embed_url TEXT;

