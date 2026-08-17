-- SQL Migration: Membuat tabel r2_episodes untuk self-hosting anime via Cloudflare R2
-- Jalankan query SQL ini di SQL Editor pada Dashboard Supabase Anda.

CREATE TABLE IF NOT EXISTS r2_episodes (
    id            BIGSERIAL PRIMARY KEY,
    episode_url   TEXT NOT NULL,          -- URL episode Samehadaku (e.g. https://v2.samehadaku.how/boku-no-hero-episode-12/)
    anime_url     TEXT NOT NULL,          -- URL main anime Samehadaku (e.g. https://v2.samehadaku.how/anime/boku-no-hero/)
    r2_key        TEXT NOT NULL DEFAULT '', -- Path objek di R2 (e.g. anime/boku-no-hero/episode-12.mp4)
    r2_url        TEXT,                   -- URL direct/presigned file R2
    file_size     BIGINT,                 -- Ukuran file dalam bytes
    duration      INT,                    -- Durasi video (detik, jika ada)
    quality       TEXT,                   -- Kualitas/extensi file (e.g. mp4, m3u8)
    status        TEXT NOT NULL DEFAULT 'pending', -- Status: pending | downloading | done | error
    error_msg     TEXT,                   -- Log error jika gagal
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT r2_episodes_episode_url_key UNIQUE (episode_url)
);

-- Indeks untuk meningkatkan performa query
CREATE INDEX IF NOT EXISTS r2_episodes_anime_url_idx ON r2_episodes (anime_url);
CREATE INDEX IF NOT EXISTS r2_episodes_status_idx ON r2_episodes (status);
