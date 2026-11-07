"""Static analysis of a page's HTML: images, blocking assets, third parties, WP footprint."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

MODERN_FORMATS = frozenset({"webp", "avif"})
OVERSIZED_IMAGE_BYTES = 200 * 1024

_PLUGIN_RE = re.compile(r"/wp-content/plugins/([A-Za-z0-9_.-]+)/")
_THEME_RE = re.compile(r"/wp-content/themes/([A-Za-z0-9_.-]+)/")
_FORMAT_ALIASES = {"jpg": "jpeg", "jpe": "jpeg"}


@dataclass
class ImageInfo:
    url: str
    format: str
    size_bytes: Optional[int] = None

    @property
    def is_modern(self) -> bool:
        return self.format in MODERN_FORMATS or self.format == "svg"

    @property
    def is_oversized(self) -> bool:
        return self.size_bytes is not None and self.size_bytes > OVERSIZED_IMAGE_BYTES


@dataclass
class PageAnalysis:
    is_wordpress: bool = False
    wp_version: Optional[str] = None
    theme: Optional[str] = None
    plugins: List[str] = field(default_factory=list)
    images: List[ImageInfo] = field(default_factory=list)
    render_blocking_css: List[str] = field(default_factory=list)
    render_blocking_js: List[str] = field(default_factory=list)
    third_party_script_hosts: List[str] = field(default_factory=list)
    third_party_style_hosts: List[str] = field(default_factory=list)


def image_format(url: str) -> str:
    """Guess an image format from the URL path extension."""
    path = urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return _FORMAT_ALIASES.get(ext, ext) or "unknown"


def _site_root(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def is_third_party(host: str, site_host: str) -> bool:
    host, root = host.lower(), _site_root(site_host.lower())
    return bool(host) and not (host == root or host.endswith("." + root))


def _ranked(counter: Counter) -> List[str]:
    return [slug for slug, _ in counter.most_common()]


def _image_url(tag) -> Optional[str]:
    for attr in ("src", "data-src", "data-lazy-src"):
        value = (tag.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return value
    srcset = (tag.get("srcset") or tag.get("data-srcset") or "").strip()
    if srcset:
        return srcset.split(",")[0].split()[0]
    return None


def analyze_html(html: str, page_url: str) -> PageAnalysis:
    """Parse ``html`` (fetched from ``page_url``) into a :class:`PageAnalysis`."""
    soup = BeautifulSoup(html, "html.parser")
    site_host = urlparse(page_url).hostname or ""
    result = PageAnalysis()

    generator = soup.find("meta", attrs={"name": "generator"})
    generator_text = (generator.get("content") or "") if generator else ""
    if generator_text.lower().startswith("wordpress"):
        result.wp_version = generator_text.split(" ", 1)[1] if " " in generator_text else None

    plugins = Counter(_PLUGIN_RE.findall(html))
    themes = Counter(_THEME_RE.findall(html))
    result.plugins = _ranked(plugins)
    result.theme = themes.most_common(1)[0][0] if themes else None
    result.is_wordpress = bool(
        generator_text.lower().startswith("wordpress")
        or plugins
        or themes
        or "/wp-includes/" in html
    )

    seen_images = set()
    for tag in soup.find_all("img"):
        raw = _image_url(tag)
        if not raw:
            continue
        url = urljoin(page_url, raw)
        if url not in seen_images:
            seen_images.add(url)
            result.images.append(ImageInfo(url=url, format=image_format(url)))

    script_hosts: Counter = Counter()
    style_hosts: Counter = Counter()
    for script in soup.find_all("script", src=True):
        url = urljoin(page_url, script["src"])
        host = urlparse(url).hostname or ""
        if is_third_party(host, site_host):
            script_hosts[host] += 1
    for link in soup.find_all("link", href=True):
        if "stylesheet" in (link.get("rel") or []):
            url = urljoin(page_url, link["href"])
            host = urlparse(url).hostname or ""
            if is_third_party(host, site_host):
                style_hosts[host] += 1
    result.third_party_script_hosts = _ranked(script_hosts)
    result.third_party_style_hosts = _ranked(style_hosts)

    head = soup.head
    if head is not None:
        for link in head.find_all("link", href=True):
            if "stylesheet" not in (link.get("rel") or []):
                continue
            if (link.get("media") or "all").strip().lower() == "print":
                continue
            result.render_blocking_css.append(urljoin(page_url, link["href"]))
        for script in head.find_all("script", src=True):
            is_module = (script.get("type") or "").lower() == "module"
            if script.has_attr("async") or script.has_attr("defer") or is_module:
                continue
            result.render_blocking_js.append(urljoin(page_url, script["src"]))

    return result


def format_counts(images: List[ImageInfo]) -> Dict[str, int]:
    return dict(Counter(img.format for img in images).most_common())
