from wp_speed_audit.headers import classify_cache_value, detect_cache, detect_cdn


def test_cloudflare_hit():
    headers = {"server": "cloudflare", "cf-ray": "abc-DAC", "cf-cache-status": "HIT", "age": "120"}
    assert detect_cdn(headers) == ["Cloudflare"]
    cache = detect_cache(headers)
    assert cache.verdict == "hit"
    assert cache.age_seconds == 120
    assert cache.headers == {"cf-cache-status": "HIT"}


def test_litespeed_miss():
    cache = detect_cache({"x-litespeed-cache": "miss", "cache-control": "no-cache"})
    assert cache.verdict == "miss"
    assert cache.cache_control == "no-cache"


def test_handler_header_alone_is_inconclusive():
    assert detect_cache({"x-cache-handler": "lscache"}).verdict == "unknown"


def test_no_cache_headers():
    assert detect_cache({"server": "nginx"}).verdict == "none"


def test_multiple_cdns_and_dedup():
    headers = {"x-amz-cf-id": "1", "via": "1.1 abc.cloudfront.net (CloudFront)", "x-sucuri-id": "7"}
    assert detect_cdn(headers) == ["Amazon CloudFront", "Sucuri"]


def test_no_cdn():
    assert detect_cdn({"server": "Apache"}) == []


def test_classify_cache_value():
    assert classify_cache_value("HIT from cloudfront") is True
    assert classify_cache_value("Miss from cloudfront") is False
    assert classify_cache_value("DYNAMIC") is False
    assert classify_cache_value("wp-rocket") is None
