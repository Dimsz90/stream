"""
api/cloudflare_stream.py
Modul interaksi dengan Cloudflare Stream API (VOD / Adaptive Bitrate Streaming).

Menyediakan:
- TUS Resumable Upload (chunked upload untuk file video besar)
- Generate URL HLS (.m3u8), DASH (.mpd), Iframe Embed, dan Thumbnail
- Pengecekan status encoding video (ready, inprogress, error)

Env vars yang didukung:
  CLOUDFLARE_ACCOUNT_ID          — Cloudflare Account ID (bisa juga fallback ke R2_ACCOUNT_ID)
  CLOUDFLARE_STREAM_API_TOKEN   — Cloudflare API Token dengan permission "Stream:Edit" (atau CLOUDFLARE_API_TOKEN)
  CLOUDFLARE_STREAM_DOMAIN       — Custom subdomain Cloudflare Stream (opsional, misal: customer-xxx.cloudflarestream.com)
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Callable

import requests

logger = logging.getLogger(__name__)

# ── Environment Configurations ───────────────────────────────────────────────

CLOUDFLARE_ACCOUNT_ID = (
    os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    or os.environ.get("CF_ACCOUNT_ID")
    or os.environ.get("R2_ACCOUNT_ID")
    or ""
).strip()

CLOUDFLARE_STREAM_API_TOKEN = (
    os.environ.get("CLOUDFLARE_STREAM_API_TOKEN")
    or os.environ.get("CLOUDFLARE_API_TOKEN")
    or os.environ.get("CF_STREAM_API_TOKEN")
    or ""
).strip()

CLOUDFLARE_STREAM_DOMAIN = (
    os.environ.get("CLOUDFLARE_STREAM_DOMAIN")
    or os.environ.get("CF_STREAM_DOMAIN")
    or ""
).strip().rstrip("/")


def is_configured() -> bool:
    """Cek apakah Cloudflare Stream sudah dikonfigurasi dengan lengkap."""
    return bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_STREAM_API_TOKEN)


def _get_api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {CLOUDFLARE_STREAM_API_TOKEN}",
    }


# ── URL Helpers ───────────────────────────────────────────────────────────────

def get_hls_url(stream_uid: str) -> str:
    """Return HLS (.m3u8) playlist manifest URL untuk video UID tertentu."""
    if not stream_uid:
        return ""
    if CLOUDFLARE_STREAM_DOMAIN:
        domain = CLOUDFLARE_STREAM_DOMAIN.replace("https://", "").replace("http://", "")
        return f"https://{domain}/{stream_uid}/manifest/video.m3u8"
    return f"https://videodelivery.net/{stream_uid}/manifest/video.m3u8"


def get_dash_url(stream_uid: str) -> str:
    """Return DASH (.mpd) manifest URL."""
    if not stream_uid:
        return ""
    if CLOUDFLARE_STREAM_DOMAIN:
        domain = CLOUDFLARE_STREAM_DOMAIN.replace("https://", "").replace("http://", "")
        return f"https://{domain}/{stream_uid}/manifest/video.mpd"
    return f"https://videodelivery.net/{stream_uid}/manifest/video.mpd"


def get_iframe_url(stream_uid: str) -> str:
    """Return URL embed iframe video player Cloudflare Stream."""
    if not stream_uid:
        return ""
    if CLOUDFLARE_STREAM_DOMAIN:
        domain = CLOUDFLARE_STREAM_DOMAIN.replace("https://", "").replace("http://", "")
        return f"https://{domain}/{stream_uid}/iframe"
    return f"https://iframe.videodelivery.net/{stream_uid}"


def get_thumbnail_url(stream_uid: str, time_sec: int | None = None) -> str:
    """Return URL thumbnail video."""
    if not stream_uid:
        return ""
    suffix = f"?time={time_sec}s" if time_sec is not None else ""
    if CLOUDFLARE_STREAM_DOMAIN:
        domain = CLOUDFLARE_STREAM_DOMAIN.replace("https://", "").replace("http://", "")
        return f"https://{domain}/{stream_uid}/thumbnails/thumbnail.jpg{suffix}"
    return f"https://videodelivery.net/{stream_uid}/thumbnails/thumbnail.jpg{suffix}"


# ── TUS Upload Implementation ────────────────────────────────────────────────

def upload_to_stream(
    local_path: str,
    name: str = "",
    meta: dict | None = None,
    chunk_size: int = 50 * 1024 * 1024,  # 50 MB chunk
    progress_callback: Callable[[int, int, float], None] | None = None,
) -> dict:
    """
    Upload file lokal ke Cloudflare Stream menggunakan TUS Resumable Protocol.

    Args:
        local_path: Path file lokal (misal .mp4 hasil download yt-dlp).
        name: Judul / label video di Cloudflare Stream.
        meta: Metadata tambahan (misal {'anime': 'One Piece', 'episode': '1050'}).
        chunk_size: Ukuran chunk upload (default 50 MB).
        progress_callback: Callback(uploaded_bytes, total_bytes, pct).

    Returns:
        dict berisi 'success', 'uid', 'hls_url', 'iframe_url', 'dash_url', atau 'error'.
    """
    if not is_configured():
        return {
            "success": False,
            "error": "Cloudflare Stream belum dikonfigurasi (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_STREAM_API_TOKEN kosong)",
        }

    if not os.path.exists(local_path):
        return {"success": False, "error": f"File lokal tidak ditemukan: {local_path}"}

    file_size = os.path.getsize(local_path)
    if file_size <= 0:
        return {"success": False, "error": "Ukuran file 0 byte"}

    filename = name or os.path.basename(local_path)

    # 1. Siapkan metadata TUS (Base64 encoded key-value)
    metadata_items = {
        "name": filename,
    }
    if meta:
        for k, v in meta.items():
            if v is not None:
                metadata_items[str(k)] = str(v)

    b64_metadata = ",".join(
        f"{k} {base64.b64encode(str(v).encode('utf-8')).decode('utf-8')}"
        for k, v in metadata_items.items()
    )

    # 2. Request TUS Upload Creation ke Cloudflare Stream API
    creation_url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/stream"
    creation_headers = {
        **_get_api_headers(),
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(file_size),
        "Upload-Metadata": b64_metadata,
    }

    try:
        logger.info(f"[CF Stream] Memulai TUS upload '{filename}' ({file_size:,} bytes)...")
        res = requests.post(creation_url, headers=creation_headers, timeout=30)
        if res.status_code not in (200, 201):
            err_msg = f"HTTP {res.status_code}: {res.text[:300]}"
            logger.error(f"[CF Stream] Gagal inisialisasi TUS upload: {err_msg}")
            return {"success": False, "error": err_msg}

        upload_location = res.headers.get("Location") or res.headers.get("location")
        stream_uid = (
            res.headers.get("stream-media-id")
            or res.headers.get("Stream-Media-Id")
            or ""
        )

        if not upload_location:
            # Coba cari dari response JSON jika ada
            try:
                res_data = res.json()
                upload_location = res_data.get("result", {}).get("uploadURL")
                stream_uid = stream_uid or res_data.get("result", {}).get("uid")
            except Exception:
                pass

        if not upload_location:
            return {"success": False, "error": "Cloudflare Stream tidak mengembalikan Header Location TUS"}

        # Extract UID dari upload_location jika belum didapat
        if not stream_uid:
            parts = upload_location.rstrip("/").split("/")
            if len(parts) >= 1:
                stream_uid = parts[-1].split("?")[0]

        logger.info(f"[CF Stream] TUS Upload dialokasikan. UID: {stream_uid}, URL: {upload_location[:60]}...")

    except Exception as exc:
        logger.error(f"[CF Stream] Request error saat create TUS upload: {exc}")
        return {"success": False, "error": str(exc)}

    # 3. Kirim video dalam chunks via PATCH request
    offset = 0
    max_retries_per_chunk = 4

    try:
        with open(local_path, "rb") as f:
            while offset < file_size:
                f.seek(offset)
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break

                current_chunk_length = len(chunk_data)
                chunk_uploaded = False

                for attempt in range(1, max_retries_per_chunk + 1):
                    try:
                        patch_headers = {
                            "Tus-Resumable": "1.0.0",
                            "Upload-Offset": str(offset),
                            "Content-Type": "application/offset+octet-stream",
                        }
                        # Beberapa endpoint TUS butuh auth header, videodelivery direct URL biasanya sudah tokenized
                        if "api.cloudflare.com" in upload_location:
                            patch_headers.update(_get_api_headers())

                        patch_res = requests.patch(
                            upload_location,
                            data=chunk_data,
                            headers=patch_headers,
                            timeout=120,
                        )

                        if patch_res.status_code in (200, 204):
                            returned_offset = patch_res.headers.get("Upload-Offset") or patch_res.headers.get("upload-offset")
                            if returned_offset is not None:
                                offset = int(returned_offset)
                            else:
                                offset += current_chunk_length
                            chunk_uploaded = True
                            break
                        elif patch_res.status_code == 409:
                            # 409 Conflict: offset mismatch, cek offset terkini dari server
                            logger.warning("[CF Stream] Offset mismatch (409), menyinkronkan offset...")
                            head_res = requests.head(
                                upload_location,
                                headers={"Tus-Resumable": "1.0.0", **_get_api_headers()},
                                timeout=20,
                            )
                            if head_res.status_code in (200, 204):
                                sync_offset = head_res.headers.get("Upload-Offset")
                                if sync_offset is not None:
                                    offset = int(sync_offset)
                            break
                        else:
                            logger.warning(
                                f"[CF Stream] Chunk upload attempt {attempt} gagal: HTTP {patch_res.status_code} {patch_res.text[:150]}"
                            )
                            time.sleep(attempt * 2)

                    except Exception as patch_exc:
                        logger.warning(f"[CF Stream] Error attempt {attempt} kirim chunk: {patch_exc}")
                        time.sleep(attempt * 2)

                if not chunk_uploaded:
                    return {
                        "success": False,
                        "error": f"Gagal upload chunk pada offset {offset} setelah {max_retries_per_chunk} percobaan",
                    }

                pct = round((offset / file_size) * 100, 1)
                logger.info(f"[CF Stream] Uploading '{filename}': {pct}% ({offset:,}/{file_size:,} bytes)")
                if progress_callback:
                    try:
                        progress_callback(offset, file_size, pct)
                    except Exception:
                        pass

        # Selesai upload
        hls_url = get_hls_url(stream_uid)
        dash_url = get_dash_url(stream_uid)
        iframe_url = get_iframe_url(stream_uid)
        thumb_url = get_thumbnail_url(stream_uid)

        logger.info(f"[CF Stream] Upload SUKSES untuk '{filename}'. UID: {stream_uid} -> HLS: {hls_url}")

        return {
            "success": True,
            "uid": stream_uid,
            "hls_url": hls_url,
            "dash_url": dash_url,
            "iframe_url": iframe_url,
            "thumbnail_url": thumb_url,
            "file_size": file_size,
        }

    except Exception as exc:
        logger.error(f"[CF Stream] Upload exception: {exc}")
        return {"success": False, "error": str(exc)}


# ── Status & Details ─────────────────────────────────────────────────────────

def get_stream_details(stream_uid: str) -> dict | None:
    """
    Ambil detail video dan status encoding dari Cloudflare Stream API.

    Returns:
        dict info video (status, duration, dimensions, dll), atau None jika gagal/tidak ditemukan.
    """
    if not is_configured() or not stream_uid:
        return None

    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/stream/{stream_uid}"
    try:
        res = requests.get(url, headers=_get_api_headers(), timeout=15)
        if res.status_code == 200:
            data = res.json()
            return data.get("result")
        logger.warning(f"[CF Stream] get_stream_details status {res.status_code}: {res.text[:200]}")
        return None
    except Exception as exc:
        logger.error(f"[CF Stream] get_stream_details error: {exc}")
        return None
