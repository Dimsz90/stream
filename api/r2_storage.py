"""
api/r2_storage.py
Modul interaksi dengan Cloudflare R2 via boto3 (S3-compatible).

Env vars yang dibutuhkan:
  R2_ACCOUNT_ID        — Cloudflare account ID
  R2_ACCESS_KEY_ID     — R2 API token access key
  R2_SECRET_ACCESS_KEY — R2 API token secret
  R2_BUCKET_NAME       — nama bucket R2
  R2_PUBLIC_URL        — base URL publik (opsional, contoh: https://pub-xxx.r2.dev)
  R2_PRESIGNED_TTL     — durasi link presigned dalam detik (default 86400 = 24 jam)
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

R2_ACCOUNT_ID        = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID     = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME       = os.environ.get("R2_BUCKET_NAME", "").strip()
R2_PUBLIC_URL        = os.environ.get("R2_PUBLIC_URL", "").strip().rstrip("/")
R2_PRESIGNED_TTL     = int(os.environ.get("R2_PRESIGNED_TTL", "86400"))

_s3_client = None


def _configured() -> bool:
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)


def _client():
    """Lazy-init boto3 S3 client yang point ke Cloudflare R2."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError(
            "boto3 belum terinstall. Tambahkan 'boto3>=1.35.0' ke requirements.txt"
        ) from exc

    if not _configured():
        raise RuntimeError(
            "R2 belum dikonfigurasi. Set env vars: "
            "R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME"
        )

    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    _s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
        region_name="auto",
    )
    return _s3_client


def _multipart_config(file_size: int):
    """Config multipart upload berdasarkan ukuran file."""
    try:
        from boto3.s3.transfer import TransferConfig
        return TransferConfig(
            multipart_threshold=100 * 1024 * 1024,   # 100 MB
            multipart_chunksize=50 * 1024 * 1024,    # 50 MB per chunk
            max_concurrency=4,
        )
    except ImportError:
        return None


def upload_to_r2(local_path: str, r2_key: str, content_type: str = "video/mp4") -> bool:
    """
    Upload file lokal ke R2.

    Args:
        local_path: Path ke file lokal yang akan diupload.
        r2_key: Key/path di dalam bucket R2 (e.g. "anime/one-piece/eps-1050.mp4").
        content_type: MIME type file.

    Returns:
        True jika berhasil, False jika gagal.
    """
    try:
        client = _client()
        file_size = os.path.getsize(local_path)
        logger.info(f"[R2] Uploading {local_path} -> {r2_key} ({file_size:,} bytes)")

        extra_args = {
            "ContentType": content_type,
            "CacheControl": "public, max-age=31536000, immutable",
        }

        config = _multipart_config(file_size)

        with open(local_path, "rb") as f:
            if config:
                client.upload_fileobj(f, R2_BUCKET_NAME, r2_key, ExtraArgs=extra_args, Config=config)
            else:
                client.upload_fileobj(f, R2_BUCKET_NAME, r2_key, ExtraArgs=extra_args)

        logger.info(f"[R2] Upload berhasil: {r2_key}")
        return True
    except Exception as exc:
        logger.error(f"[R2] Upload gagal {r2_key}: {exc}")
        return False


def get_presigned_url(r2_key: str, ttl: int | None = None) -> str:
    """
    Generate presigned URL untuk akses file R2.
    Jika R2_PUBLIC_URL diset, langsung return URL publik (lebih cepat).

    Args:
        r2_key: Key file di R2.
        ttl: Durasi valid dalam detik (default: R2_PRESIGNED_TTL env var).

    Returns:
        URL string, atau string kosong jika gagal.
    """
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{r2_key}"

    try:
        client = _client()
        ttl = ttl or R2_PRESIGNED_TTL
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": r2_key},
            ExpiresIn=ttl,
        )
        return url
    except Exception as exc:
        logger.error(f"[R2] Presign gagal {r2_key}: {exc}")
        return ""


def get_public_url(r2_key: str) -> str:
    """Return URL publik atau presigned URL untuk suatu key."""
    return get_presigned_url(r2_key)


def key_exists(r2_key: str) -> bool:
    """Cek apakah key/file sudah ada di R2 bucket."""
    try:
        client = _client()
        client.head_object(Bucket=R2_BUCKET_NAME, Key=r2_key)
        return True
    except Exception as exc:
        err_str = str(exc)
        if "404" in err_str or "NoSuchKey" in err_str or "Not Found" in err_str:
            return False
        logger.warning(f"[R2] head_object error {r2_key}: {exc}")
        return False


def delete_from_r2(r2_key: str) -> bool:
    """Hapus file dari R2."""
    try:
        client = _client()
        client.delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key)
        logger.info(f"[R2] Deleted: {r2_key}")
        return True
    except Exception as exc:
        logger.error(f"[R2] Delete gagal {r2_key}: {exc}")
        return False


def is_configured() -> bool:
    """Return True jika semua R2 env vars sudah di-set."""
    return _configured()
