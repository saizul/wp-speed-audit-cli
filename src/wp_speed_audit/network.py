"""All network access lives here so the rest of the package stays pure and testable."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from . import __version__
from .crux import FieldData, build_query, error_message, origin_of, parse_record

USER_AGENT = f"wp-speed-audit/{__version__} (+https://saizul.com)"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
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


def fetch_page(
    url: str, samples: int = 3, timeout: float = 20.0, user_agent: str = USER_AGENT
) -> PageFetch:
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
                session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
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


def supports_brotli(url: str, timeout: float = 20.0, user_agent: str = USER_AGENT) -> bool:
    """Probe whether the server answers ``Accept-Encoding: br`` with Brotli."""
    try:
        with requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept-Encoding": "br"},
            stream=True,
            timeout=timeout,
        ) as response:
            return response.headers.get("Content-Encoding", "").lower() == "br"
    except requests.RequestException:
        return False


def _head_size(url: str, timeout: float, user_agent: str = USER_AGENT) -> Optional[int]:
    try:
        response = requests.head(
            url, headers={"User-Agent": user_agent}, allow_redirects=True, timeout=timeout
        )
    except requests.RequestException:
        return None
    length = response.headers.get("Content-Length", "")
    return int(length) if length.isdigit() else None


def head_sizes(
    urls: List[str], timeout: float = 10.0, workers: int = 8, user_agent: str = USER_AGENT
) -> Dict[str, Optional[int]]:
    """Return ``Content-Length`` per URL via concurrent HEAD requests (None if unknown)."""
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(urls, pool.map(lambda u: _head_size(u, timeout, user_agent), urls)))


def _crux_post(body: Dict[str, Any], api_key: str, timeout: float, user_agent: str) -> "requests.Response":
    # the key travels in a header, not the query string, so it cannot leak into logs or error messages
    return requests.post(
        CRUX_ENDPOINT,
        json=body,
        headers={"User-Agent": user_agent, "X-Goog-Api-Key": api_key},
        timeout=timeout,
    )


def _json_or_none(response: "requests.Response") -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def fetch_field_data(
    url: str,
    api_key: str,
    form_factor: str = "all",
    timeout: float = 20.0,
    user_agent: str = USER_AGENT,
) -> FieldData:
    """Query the CrUX API for ``url``; fall back to its origin when the page has no data.

    Never raises for API or network problems: the returned :class:`FieldData`
    carries an ``error`` instead, so a CrUX hiccup cannot break the audit.
    """
    attempts = [("url", url), ("origin", origin_of(url))]
    try:
        for scope, target in attempts:
            response = _crux_post(build_query(target, scope, form_factor), api_key, timeout, user_agent)
            if response.status_code == 404:
                continue  # not enough Chrome traffic for this page/origin
            payload = _json_or_none(response)
            if response.status_code != 200:
                return FieldData(form_factor=form_factor, error=error_message(response.status_code, payload))
            return parse_record(payload, form_factor)
    except requests.RequestException as exc:
        return FieldData(form_factor=form_factor, error=f"could not reach the CrUX API: {type(exc).__name__}")
    return FieldData(
        form_factor=form_factor,
        error="no CrUX data: neither the page nor its origin has enough Chrome traffic in the last 28 days",
    )
