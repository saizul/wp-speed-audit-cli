from pathlib import Path

import pytest

from wp_speed_audit.analyzer import analyze_html, image_format, is_third_party

PAGE_URL = "https://demo.example/"
FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"


@pytest.fixture(scope="module")
def analysis():
    return analyze_html(FIXTURE.read_text(encoding="utf-8"), PAGE_URL)


def test_detects_wordpress_and_version(analysis):
    assert analysis.is_wordpress
    assert analysis.wp_version == "6.5.2"


def test_detects_theme_and_plugins(analysis):
    assert analysis.theme == "astra"
    # elementor appears most often, so it is ranked first
    assert analysis.plugins[0] == "elementor"
    assert set(analysis.plugins) >= {"contact-form-7", "wordpress-seo", "module-thing"}


def test_render_blocking_css_skips_print_media_and_preload(analysis):
    assert analysis.render_blocking_css == [
        "https://fonts.googleapis.com/css2?family=Inter",
        "https://demo.example/wp-content/themes/astra/style.css?ver=4.6",
        "https://demo.example/wp-content/plugins/elementor/assets/css/frontend.min.css",
        "https://demo.example/wp-content/plugins/contact-form-7/includes/css/styles.css",
    ]


def test_render_blocking_js_skips_defer_async_module_and_body(analysis):
    assert analysis.render_blocking_js == [
        "https://demo.example/wp-includes/js/jquery/jquery.min.js",
        "https://www.googletagmanager.com/gtag/js?id=G-TEST",
    ]


def test_third_party_hosts(analysis):
    assert set(analysis.third_party_script_hosts) == {
        "www.googletagmanager.com",
        "cdn.example-widgets.net",
        "platform.twitter.example",
    }
    assert analysis.third_party_style_hosts == ["fonts.googleapis.com"]


def test_images_are_deduplicated_and_formats_detected(analysis):
    formats = {img.url.rsplit("/", 1)[-1]: img.format for img in analysis.images}
    assert formats == {
        "hero.jpg": "jpeg",
        "logo.png": "png",
        "banner.webp": "webp",
        "lazy.AVIF": "avif",
        "team-300.JPG": "jpeg",
        "mark.svg": "svg",
    }
    assert not analysis.images[0].is_modern
    assert next(i for i in analysis.images if i.format == "webp").is_modern


def test_non_wordpress_page():
    result = analyze_html("<html><head></head><body><p>hi</p></body></html>", PAGE_URL)
    assert not result.is_wordpress
    assert result.plugins == [] and result.theme is None


@pytest.mark.parametrize(
    "host, expected",
    [
        ("demo.example", False),
        ("cdn.demo.example", False),
        ("www.demo.example", False),
        ("notdemo.example", True),
        ("fonts.googleapis.com", True),
    ],
)
def test_is_third_party(host, expected):
    assert is_third_party(host, "www.demo.example") is expected


def test_image_format_ignores_query_string():
    assert image_format("https://x.test/a/b.WebP?ver=1") == "webp"
    assert image_format("https://x.test/a/noext") == "unknown"
