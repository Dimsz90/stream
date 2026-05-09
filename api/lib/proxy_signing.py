from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote, urlencode

from .config import REQUIRE_SUBSCRIPTION, SUBSCRIPTION_SECRET


PROXY_TOKEN_TTL = 60 * 60 * 4


def _message(url: str, exp: int) -> str:
    return f"proxy:{url}:{int(exp)}"


def sign_proxy_url(url: str, base: str = "", ttl: int = PROXY_TOKEN_TTL) -> str:
    exp = int(time.time()) + int(ttl)
    sig = hmac.new(SUBSCRIPTION_SECRET.encode(), _message(url, exp).encode(), hashlib.sha256).hexdigest()
    path = f"{base}/api/proxy" if base else "/api/proxy"
    return f"{path}?{urlencode({'url': url, 'exp': exp, 'sig': sig})}"


def validate_proxy_signature(url: str, exp, sig: str) -> bool:
    if not REQUIRE_SUBSCRIPTION:
        return True
    try:
        exp = int(exp)
    except Exception:
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(SUBSCRIPTION_SECRET.encode(), _message(url, exp).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(sig or ""))


def legacy_unsigned_allowed() -> bool:
    return not REQUIRE_SUBSCRIPTION
