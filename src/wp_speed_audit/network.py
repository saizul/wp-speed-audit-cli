"""All network access lives here so the rest of the package stays pure and testable."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from . import __version__

USER_AGENT = f"wp-speed-audit/{__version__} (+https://saizul.com)"
_HTTP_VERSIONS = {9: "HTTP/0.9", 10: "HTTP/1.0", 11: "HTTP/1.1"}


class AuditError(RuntimeError):
    """Raised when the target page cannot be fetched."""


@dataclass
class PageFetch:
    final_url: str
    status_code: int
    redirects: int
    headers: Dict[str, str]  # lowercase keys, from the last sample
    html: str
    http_version: str
    ttfb_ms: List[float] = field(default_factory=list)
    total_ms: List[float] = field(default_factory=list)


def fetch_page(url: str, samples: int = 3, timeout: float = 20.0) -> PageFetch:
    """GET ``url`` ``samples`` times on fresh connections and record timings.

    TTFB is the time until response headers are available (redirects, DNS, TCP
    and TLS included); total time additionally covers downloading the body.
    The headers and HTML of the last sample are kept, so a page cache has had
    a chance to warm up.
    """
    ttfb: List[float] = []
    total: List[float] = []
    last = None
    try:
        for _ in range(samples):
            with requests.Session() as session:
                session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
                start = time.perf_counter()
                response = session.get(url, stream=True, timeout=timeout)
                ttfb.append((time.perf_counter() - start) * 1000)
                response.content  # noqa: B018 - consume the body to time the download
                total.append((time.perf_counter() - start) * 1000)
                last = response
    except requests.RequestException as exc:
        raise AuditError(f"could not fetch {url}: {exc}") from exc

    assert last is not None  # samples >= 1 is enforced by the CLI
    return PageFetch(
        final_url=last.url,
        status_code=last.status_code,
        redirects=len(last.history),
        headers={k.lower(): v for k, v in last.headers.items()},
        html=last.text,
        http_version=_HTTP_VERSIONS.get(getattr(last.raw, "version", 0), "unknown"),
        ttfb_ms=ttfb,
        total_ms=total,
    )


def supports_brotli(url: str, timeout: float = 20.0) -> bool:
    """Probe whether the server answers ``Accept-Encoding: br`` with Brotli."""
    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "br"},
            stream=True,
            timeout=timeout,
        ) as response:
            return response.headers.get("Content-Encoding", "").lower() == "br"
    except requests.RequestException:
        return False


def _head_size(url: str, timeout: float) -> Optional[int]:
    try:
        response = requests.head(
            url, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=timeout
        )
    except requests.RequestException:
        return None
    length = response.headers.get("Content-Length", "")
    return int(length) if length.isdigit() else None


def head_sizes(urls: List[str], timeout: float = 10.0, workers: int = 8) -> Dict[str, Optional[int]]:
    """Return ``Content-Length`` per URL via concurrent HEAD requests (None if unknown)."""
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(urls, pool.map(lambda u: _head_size(u, timeout), urls)))
