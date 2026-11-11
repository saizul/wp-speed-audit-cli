"""Turn an audit result into a prioritised list of recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

from .analyzer import OVERSIZED_IMAGE_BYTES

if TYPE_CHECKING:  # pragma: no cover
    from .audit import AuditResult

HIGH, MEDIUM, LOW = "high", "medium", "low"
_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}

SLOW_TTFB_MS = 800
OKAY_TTFB_MS = 400
SLOW_TOTAL_MS = 3000
LARGE_HTML_BYTES = 150 * 1024
MANY_THIRD_PARTY_HOSTS = 5
MANY_PLUGINS = 15


_FIELD_ADVICE = {
    "lcp": "Target 2.5 s or less: speed up the server response, preload and compress the hero image, and remove render-blocking assets.",
    "inp": "Target 200 ms or less: cut main-thread JavaScript (page-builder add-ons, sliders, chat widgets, trackers) and delay non-critical scripts.",
    "cls": "Target 0.1 or less: set width and height on images and embeds, reserve space for ads and banners, and preload web fonts.",
}


@dataclass
class Recommendation:
    priority: str
    area: str
    message: str


def build_recommendations(result: "AuditResult") -> List[Recommendation]:
    recs: List[Recommendation] = []

    def add(priority: str, area: str, message: str) -> None:
        recs.append(Recommendation(priority, area, message))

    if result.final_url.startswith("http://"):
        add(HIGH, "Security", "The page is served over plain HTTP. Install a TLS certificate and redirect all traffic to HTTPS.")

    if result.ttfb_median_ms > SLOW_TTFB_MS:
        add(HIGH, "Server", f"Median TTFB is {result.ttfb_median_ms:.0f} ms (target < {OKAY_TTFB_MS} ms). Enable full-page caching, upgrade PHP, and check for slow plugins or database queries.")
    elif result.ttfb_median_ms > OKAY_TTFB_MS:
        add(MEDIUM, "Server", f"Median TTFB is {result.ttfb_median_ms:.0f} ms (target < {OKAY_TTFB_MS} ms). Consider page caching, object caching or a closer hosting region.")
    if result.total_median_ms > SLOW_TOTAL_MS:
        add(MEDIUM, "Server", f"The HTML document alone takes {result.total_median_ms:.0f} ms to download; check page size and compression.")
    if result.redirects > 1:
        add(MEDIUM, "Server", f"The request went through {result.redirects} redirects. Link directly to the final canonical URL.")

    if result.cache.verdict == "none":
        add(HIGH, "Caching", "No page-cache headers were detected. Enable a page cache (server-level, or a plugin such as WP Super Cache, LiteSpeed Cache or W3 Total Cache).")
    elif result.cache.verdict == "miss":
        add(MEDIUM, "Caching", "The page cache reported a MISS/BYPASS on the last request. Check that cookies or query strings are not bypassing the cache.")
    control = (result.cache.cache_control or "").lower()
    if any(token in control for token in ("no-store", "no-cache", "private", "max-age=0")):
        add(MEDIUM, "Caching", f"Cache-Control is '{result.cache.cache_control}', which prevents browser and CDN caching of the HTML.")

    if not result.compression:
        add(HIGH, "Compression", "The HTML response is not compressed. Enable gzip or Brotli on the server.")
    elif not result.brotli:
        add(LOW, "Compression", "Only gzip was detected. Brotli typically shrinks text assets 15-20% more.")

    if not result.cdn:
        add(MEDIUM, "CDN", "No CDN detected. A CDN (for example Cloudflare or Bunny) reduces latency for visitors far from the origin.")

    if result.html_bytes > LARGE_HTML_BYTES:
        add(MEDIUM, "HTML", f"The HTML is {result.html_bytes // 1024} KB. Remove inline bloat (page-builder markup, inline CSS/JSON) and paginate long content.")

    page = result.page
    legacy = [img for img in page.images if not img.is_modern]
    if legacy:
        priority = HIGH if len(legacy) > 5 else MEDIUM
        add(priority, "Images", f"{len(legacy)} of {len(page.images)} images are not WebP/AVIF. Convert them (for example with ShortPixel, Imagify or EWWW).")
    oversized = [img for img in page.images if img.is_oversized]
    if oversized:
        largest = max(oversized, key=lambda i: i.size_bytes or 0)
        priority = HIGH if (largest.size_bytes or 0) > 3 * OVERSIZED_IMAGE_BYTES else MEDIUM
        add(priority, "Images", f"{len(oversized)} image(s) exceed {OVERSIZED_IMAGE_BYTES // 1024} KB; the largest is {(largest.size_bytes or 0) // 1024} KB ({largest.url}). Resize and compress them.")

    if page.render_blocking_js:
        priority = HIGH if len(page.render_blocking_js) > 2 else MEDIUM
        add(priority, "Render blocking", f"{len(page.render_blocking_js)} script(s) in <head> block rendering. Add defer/async or move them to the footer.")
    if len(page.render_blocking_css) > 3:
        add(MEDIUM, "Render blocking", f"{len(page.render_blocking_css)} stylesheets in <head> block rendering. Combine them and inline critical CSS.")
    elif page.render_blocking_css:
        add(LOW, "Render blocking", f"{len(page.render_blocking_css)} stylesheet(s) in <head> block rendering; consider inlining critical CSS.")

    hosts = set(page.third_party_script_hosts) | set(page.third_party_style_hosts)
    if len(hosts) > MANY_THIRD_PARTY_HOSTS:
        add(MEDIUM, "Third parties", f"Assets load from {len(hosts)} third-party hosts. Remove unused trackers/widgets and self-host fonts where possible.")

    if len(page.plugins) > MANY_PLUGINS:
        add(LOW, "WordPress", f"{len(page.plugins)} plugins load front-end assets. Audit them and deactivate what you do not need.")

    field_data = getattr(result, "field_data", None)
    if field_data is not None and not field_data.error:
        where = "this page" if field_data.scope == "url" else "this origin"
        for metric in field_data.metrics:
            if metric.key not in _FIELD_ADVICE or metric.rating == "good":
                continue
            priority = HIGH if metric.rating == "poor" else MEDIUM
            add(priority, "Core Web Vitals", f"Real users of {where} see a p75 {metric.label} of {metric.display} ({metric.rating}, CrUX field data). {_FIELD_ADVICE[metric.key]}")

    recs.sort(key=lambda r: _ORDER[r.priority])
    return recs
