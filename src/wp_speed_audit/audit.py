"""Orchestrates fetching, analysis and recommendations into one result object."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .analyzer import PageAnalysis, analyze_html
from .baseline import Comparison
from .crux import FieldData
from .headers import CacheInfo, detect_cache, detect_cdn
from .network import USER_AGENT, fetch_field_data, fetch_page, head_sizes, supports_brotli
from .recommend import Recommendation, build_recommendations


@dataclass
class AuditResult:
    url: str
    final_url: str = ""
    status_code: int = 200
    redirects: int = 0
    ttfb_ms: List[float] = field(default_factory=list)
    total_ms: List[float] = field(default_factory=list)
    ttfb_median_ms: float = 0.0
    total_median_ms: float = 0.0
    http_version: str = "unknown"
    h3_advertised: bool = False
    compression: Optional[str] = None
    brotli: bool = False
    html_bytes: int = 0
    server: Optional[str] = None
    cdn: List[str] = field(default_factory=list)
    cache: CacheInfo = field(default_factory=CacheInfo)
    page: PageAnalysis = field(default_factory=PageAnalysis)
    field_data: Optional[FieldData] = None  # CrUX, only with --crux
    recommendations: List[Recommendation] = field(default_factory=list)
    comparison: Optional[Comparison] = None  # only with --baseline

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["comparison"] = self.comparison.to_dict() if self.comparison is not None else None
        return data


def run_audit(
    url: str,
    samples: int = 3,
    timeout: float = 20.0,
    max_images: int = 40,
    check_images: bool = True,
    user_agent: str = USER_AGENT,
    crux_api_key: Optional[str] = None,
    crux_form_factor: str = "all",
) -> AuditResult:
    fetched = fetch_page(url, samples=samples, timeout=timeout, user_agent=user_agent)
    headers = fetched.headers
    page = analyze_html(fetched.html, fetched.final_url)

    if check_images and page.images:
        checked = page.images[:max_images]
        sizes = head_sizes([img.url for img in checked], timeout=min(timeout, 10.0), user_agent=user_agent)
        for img in checked:
            img.size_bytes = sizes.get(img.url)

    result = AuditResult(
        url=url,
        final_url=fetched.final_url,
        status_code=fetched.status_code,
        redirects=fetched.redirects,
        ttfb_ms=[round(v, 1) for v in fetched.ttfb_ms],
        total_ms=[round(v, 1) for v in fetched.total_ms],
        ttfb_median_ms=round(statistics.median(fetched.ttfb_ms), 1),
        total_median_ms=round(statistics.median(fetched.total_ms), 1),
        http_version=fetched.http_version,
        h3_advertised="h3" in headers.get("alt-svc", ""),
        compression=headers.get("content-encoding") or None,
        brotli=supports_brotli(fetched.final_url, timeout, user_agent),
        html_bytes=len(fetched.html.encode("utf-8")),
        server=headers.get("server"),
        cdn=detect_cdn(headers),
        cache=detect_cache(headers),
        page=page,
    )
    if crux_api_key:
        result.field_data = fetch_field_data(
            fetched.final_url, crux_api_key, crux_form_factor, timeout=timeout, user_agent=user_agent
        )
    result.recommendations = build_recommendations(result)
    return result
