import io
import json

import pytest
import requests
from rich.console import Console

from wp_speed_audit import audit, cli, crux, network
from wp_speed_audit.audit import AuditResult
from wp_speed_audit.crux import FieldData, FieldMetric
from wp_speed_audit.headers import CacheInfo
from wp_speed_audit.recommend import build_recommendations
from wp_speed_audit.report import render, render_markdown

FAKE_KEY = "test-key-not-real"


def _payload(scope="url", target="https://shop.example/", lcp=2100, inp=180, cls="0.05", extra=True):
    metrics = {
        "largest_contentful_paint": {
            "histogram": [{"start": 0, "end": 2500, "density": 0.8123}, {"start": 2500, "end": 4000, "density": 0.12}, {"start": 4000, "density": 0.0677}],
            "percentiles": {"p75": lcp},
        },
        "cumulative_layout_shift": {
            "histogram": [{"start": "0.00", "end": "0.10", "density": 0.9}],
            "percentiles": {"p75": cls},
        },
    }
    if inp is not None:
        metrics["interaction_to_next_paint"] = {"histogram": [{"start": 0, "end": 200, "density": 0.7}], "percentiles": {"p75": inp}}
    if extra:
        metrics["first_contentful_paint"] = {"percentiles": {"p75": 1500}}
        metrics["experimental_time_to_first_byte"] = {"percentiles": {"p75": 950}}
    return {
        "record": {
            "key": {scope: target},
            "metrics": metrics,
            "collectionPeriod": {
                "firstDate": {"year": 2026, "month": 3, "day": 1},
                "lastDate": {"year": 2026, "month": 3, "day": 28},
            },
        }
    }


class _Resp:
    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def _fake_post(responses, calls):
    queue = list(responses)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return post


# ---- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(2500, "good"), (2500.1, "needs improvement"), (4000, "needs improvement"), (4001, "poor"), (0, "good")],
)
def test_rate_boundaries(value, expected):
    assert crux.rate(value, 2500, 4000) == expected


def test_origin_of():
    assert crux.origin_of("https://Shop.example:8443/blog/post/?a=1#x") == "https://Shop.example:8443"
    assert crux.origin_of("http://example.com") == "http://example.com"


def test_build_query():
    body = crux.build_query("https://x.example/p/", "url")
    assert body["url"] == "https://x.example/p/" and "formFactor" not in body
    assert set(body["metrics"]) == set(crux.METRICS)
    assert crux.build_query("https://x.example", "origin", "phone") == {
        "origin": "https://x.example", "metrics": list(crux.METRICS), "formFactor": "PHONE",
    }


@pytest.mark.parametrize("scope,form", [("page", "all"), ("url", "watch")])
def test_build_query_rejects_bad_arguments(scope, form):
    with pytest.raises(ValueError):
        crux.build_query("https://x.example/", scope, form)


def test_parse_record_full():
    data = crux.parse_record(_payload(), "phone")
    assert data.error is None
    assert (data.scope, data.target, data.form_factor) == ("url", "https://shop.example/", "phone")
    assert (data.period_start, data.period_end) == ("2026-03-01", "2026-03-28")
    assert [m.key for m in data.metrics] == ["lcp", "inp", "cls", "fcp", "ttfb"]
    lcp = data.metric("lcp")
    assert lcp.p75 == 2100 and lcp.rating == "good" and lcp.good_share == 81.2 and lcp.display == "2100 ms"
    cls_metric = data.metric("cls")
    assert cls_metric.p75 == 0.05 and cls_metric.unit == "" and cls_metric.display == "0.05"  # string in the API
    assert data.metric("fcp").good_share is None
    assert data.metric("ttfb").rating == "needs improvement"
    assert data.assessment == "passed"
    assert data.metric("nope") is None


@pytest.mark.parametrize(
    "kwargs,assessment",
    [
        ({"lcp": 4500}, "failed"),
        ({"inp": 350}, "failed"),
        ({"cls": "0.30"}, "failed"),
        ({"inp": None}, "passed"),  # INP may be missing on low-interaction pages
        ({"inp": None, "lcp": 2600}, "failed"),
    ],
)
def test_parse_record_assessment(kwargs, assessment):
    assert crux.parse_record(_payload(**kwargs)).assessment == assessment


def test_parse_record_no_assessment_without_lcp_or_cls():
    payload = _payload()
    del payload["record"]["metrics"]["largest_contentful_paint"]
    data = crux.parse_record(payload)
    assert data.assessment is None and data.error is None and data.metric("lcp") is None


def test_parse_record_origin_scope_and_missing_period():
    payload = _payload(scope="origin", target="https://shop.example")
    del payload["record"]["collectionPeriod"]
    data = crux.parse_record(payload)
    assert data.scope == "origin" and data.target == "https://shop.example"
    assert data.period_start is None and data.period_end is None


def test_parse_record_skips_unusable_values():
    payload = _payload(lcp="n/a", cls=None, inp=-5, extra=False)
    data = crux.parse_record(payload)
    assert data.metrics == [] and "no usable metrics" in data.error
    payload = _payload(lcp=float("nan"))
    assert crux.parse_record(payload).metric("lcp") is None
    payload = _payload()
    payload["record"]["metrics"]["first_contentful_paint"] = "broken"
    payload["record"]["metrics"]["largest_contentful_paint"]["histogram"] = []
    data = crux.parse_record(payload)
    assert data.metric("fcp") is None and data.metric("lcp").good_share is None


@pytest.mark.parametrize("payload", [None, [], {}, {"record": "x"}, {"error": {"code": 500}}])
def test_parse_record_malformed(payload):
    data = crux.parse_record(payload, "desktop")
    assert data.error == "unexpected response from the CrUX API" and data.form_factor == "desktop"


def test_error_message_variants():
    msg = crux.error_message(403, {"error": {"message": "API key not valid."}})
    assert "HTTP 403: API key not valid." in msg and crux.API_KEY_ENV in msg
    assert "quota" in crux.error_message(429, None)
    assert crux.error_message(500, None) == "CrUX API returned HTTP 500"
    assert crux.error_message(503, {"error": {"message": "backend down"}}).endswith("HTTP 503: backend down")
    assert "HTTP 400)" in crux.error_message(400, {"error": "nope"})


# ---- network layer (requests is always faked) -------------------------------


def test_fetch_field_data_url_level(monkeypatch):
    calls = []
    monkeypatch.setattr(network.requests, "post", _fake_post([_Resp(200, _payload())], calls))
    data = network.fetch_field_data("https://shop.example/", FAKE_KEY, "phone", timeout=9, user_agent="UA/1")
    assert data.scope == "url" and data.metric("lcp").p75 == 2100 and data.form_factor == "phone"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == network.CRUX_ENDPOINT and FAKE_KEY not in url  # key is not put in the query string
    assert kwargs["headers"] == {"User-Agent": "UA/1", "X-Goog-Api-Key": FAKE_KEY}
    assert kwargs["timeout"] == 9
    assert kwargs["json"]["url"] == "https://shop.example/" and kwargs["json"]["formFactor"] == "PHONE"


def test_fetch_field_data_falls_back_to_origin(monkeypatch):
    calls = []
    origin = _payload(scope="origin", target="https://shop.example")
    monkeypatch.setattr(network.requests, "post", _fake_post([_Resp(404, {"error": {"code": 404}}), _Resp(200, origin)], calls))
    data = network.fetch_field_data("https://shop.example/product/x/?ref=1", FAKE_KEY)
    assert data.scope == "origin" and data.error is None
    assert [c[1]["json"].get("url") for c in calls] == ["https://shop.example/product/x/?ref=1", None]
    assert calls[1][1]["json"]["origin"] == "https://shop.example"
    assert "formFactor" not in calls[1][1]["json"]


def test_fetch_field_data_no_data_anywhere(monkeypatch):
    calls = []
    monkeypatch.setattr(network.requests, "post", _fake_post([_Resp(404), _Resp(404)], calls))
    data = network.fetch_field_data("https://tiny.example/", FAKE_KEY)
    assert len(calls) == 2 and data.metrics == [] and data.error.startswith("no CrUX data")


def test_fetch_field_data_api_error_does_not_retry_or_leak_key(monkeypatch):
    calls = []
    resp = _Resp(403, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    monkeypatch.setattr(network.requests, "post", _fake_post([resp], calls))
    data = network.fetch_field_data("https://shop.example/", FAKE_KEY)
    assert len(calls) == 1 and "HTTP 403" in data.error and FAKE_KEY not in data.error


def test_fetch_field_data_network_failure(monkeypatch):
    calls = []
    boom = requests.ConnectionError(f"failed for key={FAKE_KEY}")
    monkeypatch.setattr(network.requests, "post", _fake_post([boom], calls))
    data = network.fetch_field_data("https://shop.example/", FAKE_KEY)
    assert data.error == "could not reach the CrUX API: ConnectionError" and FAKE_KEY not in data.error


def test_fetch_field_data_non_json_body(monkeypatch):
    monkeypatch.setattr(network.requests, "post", _fake_post([_Resp(200, bad_json=True)], []))
    assert network.fetch_field_data("https://shop.example/", FAKE_KEY).error == "unexpected response from the CrUX API"
    monkeypatch.setattr(network.requests, "post", _fake_post([_Resp(502, bad_json=True)], []))
    assert network.fetch_field_data("https://shop.example/", FAKE_KEY).error == "CrUX API returned HTTP 502"


# ---- audit, recommendations, rendering, CLI ---------------------------------


def _fetched():
    return network.PageFetch(
        final_url="https://shop.example/final/", status_code=200, redirects=0,
        headers={"content-encoding": "gzip"}, html="<html><head></head><body></body></html>",
        http_version="HTTP/1.1", ttfb_ms=[100.0], total_ms=[150.0],
    )


def test_run_audit_only_queries_crux_with_a_key(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "fetch_page", lambda *a, **k: _fetched())
    monkeypatch.setattr(audit, "supports_brotli", lambda *a, **k: False)

    def fake_field(url, key, form_factor, **kwargs):
        seen.append((url, key, form_factor, kwargs["timeout"], kwargs["user_agent"]))
        return crux.parse_record(_payload(lcp=5000), form_factor)

    monkeypatch.setattr(audit, "fetch_field_data", fake_field)
    assert audit.run_audit("https://shop.example/").field_data is None and seen == []
    result = audit.run_audit("https://shop.example/", timeout=5, user_agent="UA/2", crux_api_key=FAKE_KEY, crux_form_factor="desktop")
    assert seen == [("https://shop.example/final/", FAKE_KEY, "desktop", 5, "UA/2")]  # final URL after redirects
    assert result.field_data.metric("lcp").rating == "poor"
    assert ("high", "Core Web Vitals") in [(r.priority, r.area) for r in result.recommendations]
    dumped = result.to_dict()["field_data"]
    assert dumped["assessment"] == "failed" and dumped["metrics"][0]["key"] == "lcp"
    assert FAKE_KEY not in json.dumps(result.to_dict())


def _result(field_data):
    return AuditResult(
        url="https://shop.example/", final_url="https://shop.example/", ttfb_median_ms=120.0, total_median_ms=200.0,
        compression="br", brotli=True, cdn=["Cloudflare"],
        cache=CacheInfo(headers={"cf-cache-status": "HIT"}, verdict="hit"), field_data=field_data,
    )


def test_recommendations_from_field_data():
    data = crux.parse_record(_payload(lcp=4200, inp=300, cls="0.02", scope="origin", target="https://shop.example"))
    recs = build_recommendations(_result(data))
    assert [(r.priority, r.area) for r in recs] == [("high", "Core Web Vitals"), ("medium", "Core Web Vitals")]
    assert "p75 LCP of 4200 ms (poor" in recs[0].message and "this origin" in recs[0].message
    assert "INP of 300 ms (needs improvement" in recs[1].message
    cls_only = build_recommendations(_result(crux.parse_record(_payload(cls="0.31"))))
    assert len(cls_only) == 1 and "CLS of 0.31" in cls_only[0].message and "this page" in cls_only[0].message


def test_no_field_recommendations_when_good_missing_or_errored():
    assert build_recommendations(_result(crux.parse_record(_payload()))) == []  # slow field TTFB/FCP alone add nothing
    assert build_recommendations(_result(None)) == []
    assert build_recommendations(_result(FieldData(error="no CrUX data"))) == []


def _rich(result):
    buf = io.StringIO()
    render(result, Console(file=buf, width=160, color_system=None))
    return buf.getvalue()


def test_rich_report_shows_field_table_only_when_requested():
    assert "Field data" not in _rich(_result(None))
    out = _rich(_result(crux.parse_record(_payload(inp=350), "phone")))
    assert "Field data (Chrome UX Report" in out
    assert "2100 ms" in out and "350 ms" in out and "needs improvement" in out and "81%" in out
    assert "950 ms  (lab: 120 ms)" in out
    assert "Core Web Vitals assessment: FAILED" in out and "devices: phone" in out and "2026-03-01 to 2026-03-28" in out


def test_rich_report_field_error():
    out = _rich(_result(FieldData(error="no CrUX data: too little traffic")))
    assert "Field data unavailable: no CrUX data: too little traffic" in out


def test_markdown_field_section():
    md = render_markdown(_result(crux.parse_record(_payload())))
    assert "### Field data (Chrome UX Report)" in md
    assert "| Metric | p75 | Rating | Good loads |" in md
    assert "| LCP | 2100 ms | good | 81% |" in md
    assert "| CLS | 0.05 | good | 90% |" in md
    assert "| FCP | 1500 ms | good | - |" in md
    assert "Core Web Vitals assessment: PASSED (page-level data for https://shop.example/; devices: all" in md
    assert md.index("### Field data") < md.index("### Recommendations")
    assert "### Field data" not in render_markdown(_result(None))
    err = render_markdown(_result(FieldData(error="quota exceeded")))
    assert "Field data unavailable: quota exceeded" in err and "| Metric | p75 |" not in err


def test_assessment_caption_without_enough_data():
    payload = _payload()
    del payload["record"]["metrics"]["cumulative_layout_shift"]
    assert "Core Web Vitals assessment: not enough data" in render_markdown(_result(crux.parse_record(payload)))


def test_cli_crux_requires_key_in_environment(monkeypatch, capsys):
    monkeypatch.delenv(crux.API_KEY_ENV, raising=False)
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: pytest.fail("audit must not run without a key"))
    assert cli.main(["shop.example", "--crux"]) == 2
    assert crux.API_KEY_ENV in capsys.readouterr().err
    monkeypatch.setenv(crux.API_KEY_ENV, "   ")
    assert cli.main(["shop.example", "--crux"]) == 2


def test_cli_passes_key_and_form_factor(monkeypatch, capsys):
    seen = {}

    def fake_run(url, **kwargs):
        seen.update(kwargs)
        return _result(crux.parse_record(_payload(), kwargs["crux_form_factor"]))

    monkeypatch.setenv(crux.API_KEY_ENV, f" {FAKE_KEY} ")
    monkeypatch.setattr(cli, "run_audit", fake_run)
    assert cli.main(["shop.example", "--crux", "--crux-form-factor", "phone", "--json"]) == 0
    assert seen["crux_api_key"] == FAKE_KEY and seen["crux_form_factor"] == "phone"
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["field_data"]["form_factor"] == "phone" and data["field_data"]["metrics"][0]["p75"] == 2100
    assert FAKE_KEY not in captured.out and captured.err == ""


def test_cli_without_crux_ignores_environment_key(monkeypatch, capsys):
    seen = {}

    def fake_run(url, **kwargs):
        seen.update(kwargs)
        return _result(None)

    monkeypatch.setenv(crux.API_KEY_ENV, FAKE_KEY)
    monkeypatch.setattr(cli, "run_audit", fake_run)
    assert cli.main(["shop.example", "--json"]) == 0
    assert seen["crux_api_key"] is None
    assert json.loads(capsys.readouterr().out)["field_data"] is None


def test_cli_warns_when_field_data_unavailable_but_succeeds(monkeypatch, capsys):
    monkeypatch.setenv(crux.API_KEY_ENV, FAKE_KEY)
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result(FieldData(error="no CrUX data: too little traffic")))
    assert cli.main(["shop.example", "--crux", "--markdown"]) == 0
    captured = capsys.readouterr()
    assert "warning: no CrUX data" in captured.err and "Field data unavailable" in captured.out


def test_cli_rejects_unknown_form_factor():
    with pytest.raises(SystemExit):
        cli.main(["shop.example", "--crux", "--crux-form-factor", "watch"])


def test_field_metric_display():
    assert FieldMetric("cls", "CLS", 0.1, "", "good").display == "0.10"
    assert FieldMetric("lcp", "LCP", 2499.6, "ms", "good").display == "2500 ms"
