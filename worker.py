"""
worker.py - Standalone Archiver Worker

Script ini dijalankan di komputer / server lokal Anda (bukan di Fly.io).
Tugasnya:
1. Memeriksa Supabase untuk episode berstatus 'pending' / 'queued'.
2. Men-download video (prioritas server Wibufile).
3. Mengupdate progress real-time ke Supabase.
4. Mengunggah file ke Cloudflare R2.
5. Menandai status menjadi 'done'.
"""

import os
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# Load .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ArchiverWorker")

# Jumlah task yang diproses secara paralel (download + upload serentak)
# Sesuaikan dengan kecepatan internet & CPU kamu. Default: 2
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "2"))

# Import komponen archiver dan Storage (R2 / Cloudflare Stream)
try:
    from api.anime_archiver import (
        _supabase,
        _do_download_and_archive,
        get_r2_episode,
        _upsert_r2_episode,
        is_storage_configured,
        get_storage_provider,
    )
except ImportError:
    logger.error("Pastikan script dijalankan dari root direktori project!")
    sys.exit(1)


def fetch_pending_tasks(limit: int = 5) -> list[dict]:
    """Mengambil episode yang berstatus 'pending' atau 'queued' dari Supabase."""
    try:
        # Cari status pending atau queued
        res = _supabase("GET", "r2_episodes", params={
            "status": "in.(pending,queued)",
            "order": "created_at.asc",
            "limit": str(limit)
        })
        if isinstance(res, list):
            return res
        return []
    except Exception as exc:
        logger.error(f"Gagal mengambil task dari Supabase: {exc}")
        return []


def process_task(task: dict):
    """Memproses satu task download dan upload (Stream / R2)."""
    episode_url = task.get("episode_url")
    anime_url = task.get("anime_url") or ""
    task_id = task.get("id")

    if not episode_url:
        return

    logger.info(f"===> [Worker] Memproses Task #{task_id}: {episode_url}")

    try:
        result = _do_download_and_archive(episode_url, anime_url)
        logger.info(f"<=== [Worker] Hasil Task #{task_id}: {result.get('status')}")
    except Exception as exc:
        logger.error(f"[Worker] Error memproses task #{task_id}: {exc}")
        _upsert_r2_episode(episode_url, anime_url, "error", error_msg=str(exc)[:200])


def run_worker_loop(interval_seconds: int = 15):
    """Loop utama worker yang berjalan terus menerus."""
    if not is_storage_configured():
        provider = get_storage_provider()
        logger.error(f"[Worker] Storage provider '{provider}' belum terkonfigurasi di .env! Harap periksa file .env.")
        sys.exit(1)

    provider = get_storage_provider()
    logger.info("==================================================")
    logger.info("🚀 Anime Archiver Worker AKTIF di Mesin Lokal")
    logger.info(f"📦 Storage Provider: {provider.upper()}")
    logger.info(f"⏱️  Interval polling: {interval_seconds} detik")
    logger.info(f"⚡ Concurrency: {WORKER_CONCURRENCY} task paralel")
    logger.info("==================================================")


    with ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix="worker") as executor:
        while True:
            try:
                # Ambil task sebanyak concurrency supaya penuh
                tasks = fetch_pending_tasks(limit=WORKER_CONCURRENCY)
                if tasks:
                    logger.info(f"[Worker] Ditemukan {len(tasks)} task pending — menjalankan {len(tasks)} thread paralel.")
                    futures = {executor.submit(process_task, task): task for task in tasks}
                    for future in as_completed(futures):
                        task = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            logger.error(f"[Worker] Thread error untuk task #{task.get('id')}: {exc}")
                else:
                    # Tidak ada task, idle
                    pass
            except KeyboardInterrupt:
                logger.info("\n[Worker] Dihentikan oleh pengguna (Ctrl+C). Keluar...")
                break
            except Exception as e:
                logger.error(f"[Worker] Exception tidak terduga: {e}")

            time.sleep(interval_seconds)


if __name__ == "__main__":
    # Bisa atur interval via argumen: python worker.py 10
    poll_interval = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 15
    run_worker_loop(poll_interval)
