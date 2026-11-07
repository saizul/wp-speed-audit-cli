import json
from pathlib import Path

from wp_speed_audit import cli
from wp_speed_audit.analyzer import ImageInfo, analyze_html
from wp_speed_audit.audit import AuditResult
from wp_speed_audit.headers import CacheInfo
from wp_speed_audit.recommend import build_recommendations

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"


def _slow_result() -> AuditResult:
    page = analyze_html(FIXTURE.read_text(encoding="utf-8"), "http://demo.example/")
    page.images[0].size_bytes = 900 * 1024
    return AuditResult(
        url="http://demo.example/",
        final_url="http://demo.example/",
        ttfb_median_ms=1200.0,
        total_median_ms=1500.0,
        compression=None,
        cache=CacheInfo(),
        page=page,
    )


def _areas(recs):
    return [(r.priority, r.area) for r in recs]


def test_slow_unoptimised_site_gets_high_priority_items_first():
    recs = build_recommendations(_slow_result())
    priorities = [r.priority for r in recs]
    assert priorities == sorted(priorities, key={"high": 0, "medium": 1, "low": 2}.get)
    areas = _areas(recs)
    for expected in [
        ("high", "Security"),
        ("high", "Server"),
        ("high", "Caching"),
        ("high", "Compression"),
        ("high", "Images"),  # 900 KB image
        ("medium", "CDN"),
    ]:
        assert expected in areas


def test_healthy_site_has_no_high_priority_items():
    page = analyze_html("<html><head></head><body></body></html>", "https://ok.example/")
    page.images = [ImageInfo("https://ok.example/a.webp", "webp", 20_000)]
    result = AuditResult(
        url="https://ok.example/",
        final_url="https://ok.example/",
        ttfb_median_ms=120.0,
        total_median_ms=200.0,
        compression="br",
        brotli=True,
        cdn=["Cloudflare"],
        cache=CacheInfo(headers={"cf-cache-status": "HIT"}, verdict="hit"),
        page=page,
    )
    assert build_recommendations(result) == []


def test_cli_json_output(monkeypatch, capsys):
    result = _slow_result()
    result.recommendations = build_recommendations(result)
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: result)
    assert cli.main(["demo.example", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["page"]["theme"] == "astra"
    assert data["recommendations"][0]["priority"] == "high"
    assert data["page"]["images"][0]["size_bytes"] == 900 * 1024


def test_cli_reports_fetch_errors(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise cli.AuditError("could not fetch https://nope.invalid: boom")

    monkeypatch.setattr(cli, "run_audit", boom)
    assert cli.main(["nope.invalid", "--json"]) == 2
    assert "could not fetch" in capsys.readouterr().err


def test_normalize_url():
    assert cli.normalize_url("example.com") == "https://example.com"
    assert cli.normalize_url(" http://example.com ") == "http://example.com"
