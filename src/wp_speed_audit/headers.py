"""Interpretation of HTTP response headers: page cache signals and CDN detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

# Headers commonly set by page caches, reverse proxies and CDNs.
CACHE_HEADERS = (
    "x-cache",
    "cf-cache-status",
    "x-cache-handler",
    "x-litespeed-cache",
    "x-proxy-cache",
    "x-fastcgi-cache",
    "x-kinsta-cache",
    "x-wp-cf-super-cache",
    "x-hcdn-cache-status",
    "x-qc-cache",
    "cdn-cache",
)

_HIT_WORDS = ("hit",)
_MISS_WORDS = ("miss", "bypass", "dynamic", "expired", "uncacheable")


@dataclass
class CacheInfo:
    headers: Dict[str, str] = field(default_factory=dict)
    verdict: str = "none"  # hit | miss | unknown | none
    age_seconds: Optional[int] = None
    cache_control: Optional[str] = None


def classify_cache_value(value: str) -> Optional[bool]:
    """True for a hit, False for a miss/bypass, None when the value is not conclusive."""
    lowered = value.lower()
    if any(word in lowered for word in _MISS_WORDS):
        return False
    if any(word in lowered for word in _HIT_WORDS):
        return True
    return None


def detect_cache(headers: Mapping[str, str]) -> CacheInfo:
    """Inspect lowercase-keyed ``headers`` for page-cache evidence."""
    found = {name: headers[name] for name in CACHE_HEADERS if name in headers}
    age = headers.get("age", "")
    info = CacheInfo(
        headers=found,
        age_seconds=int(age) if age.isdigit() else None,
        cache_control=headers.get("cache-control"),
    )
    verdicts = [classify_cache_value(value) for value in found.values()]
    if True in verdicts:
        info.verdict = "hit"
    elif False in verdicts:
        info.verdict = "miss"
    elif found or info.age_seconds:
        info.verdict = "unknown"
    return info


def detect_cdn(headers: Mapping[str, str]) -> List[str]:
    """Return the names of CDNs/edge networks suggested by ``headers``."""
    server = headers.get("server", "").lower()
    via = headers.get("via", "").lower()
    x_cache = headers.get("x-cache", "").lower()
    found: List[str] = []

    def add(name: str, condition: bool) -> None:
        if condition and name not in found:
            found.append(name)

    add("Cloudflare", "cf-ray" in headers or server == "cloudflare")
    add("Amazon CloudFront", "x-amz-cf-id" in headers or "cloudfront" in via or "cloudfront" in x_cache)
    add("Fastly", "x-fastly-request-id" in headers or "fastly" in via or "cache-" in headers.get("x-served-by", "").lower())
    add("Akamai", "akamai" in server or any(k.startswith("x-akamai") for k in headers))
    add("Sucuri", "x-sucuri-id" in headers or "sucuri" in server)
    add("BunnyCDN", server.startswith("bunnycdn") or "cdn-pullzone" in headers)
    add("KeyCDN", "keycdn" in server)
    add("Imperva", "x-iinfo" in headers or "x-cdn" in headers and "imperva" in headers["x-cdn"].lower())
    add("QUIC.cloud", "x-qc-pop" in headers)
    add("Azure Front Door", "x-azure-ref" in headers)
    add("Vercel", "x-vercel-id" in headers)
    return found
