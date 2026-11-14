import json

import pytest

from wp_speed_audit import cli
from wp_speed_audit.baseline import (
    BaselineError,
    MetricDelta,
    check_regressions,
    compare,
    format_change,
    load_baseline,
)
from wp_speed_audit.recommend import build_recommendations
from wp_speed_audit.report import render, render_markdown

from test_recommend_and_cli import _slow_result


def _result(ttfb=1200.0, total=1500.0):
    result = _slow_result()
    result.ttfb_median_ms = ttfb
    result.total_median_ms = total
    result.html_bytes = 40 * 1024
    result.recommendations = build_recommendations(result)
    return result


def _baseline(**overrides):
    data = _result(ttfb=1000.0, total=1400.0).to_dict()
    data.update(overrides)
    return data


def _write(tmp_path, data, name="baseline.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_compare_reports_metric_deltas():
    comparison = compare(_baseline(), _result())
    ttfb = comparison.metric("ttfb_median_ms")
    assert (ttfb.before, ttfb.after) == (1000.0, 1200.0)
    assert ttfb.delta == 200.0
    assert ttfb.percent == pytest.approx(20.0)
    assert comparison.metric("html_bytes").delta == 0
    assert not comparison.url_changed
    assert comparison.to_dict()["metrics"][0] == {
        "key": "ttfb_median_ms",
        "label": "TTFB (median)",
        "unit": "ms",
        "before": 1000.0,
        "after": 1200.0,
        "delta": 200.0,
        "percent": 20.0,
    }


def test_compare_lists_plugin_and_recommendation_changes():
    baseline = _baseline()
    baseline["page"]["plugins"] = ["elementor", "old-slider"]
    baseline["recommendations"] = [
        {"priority": "high", "area": "Images", "message": "2 image(s) exceed 200 KB; the largest is 500 KB (https://x/a.jpg)."},
        {"priority": "low", "area": "Fonts", "message": "Self-host Google Fonts."},
    ]
    comparison = compare(baseline, _result())
    assert "old-slider" in comparison.plugins_removed
    assert "contact-form-7" in comparison.plugins_added
    assert "elementor" not in comparison.plugins_added
    assert comparison.resolved_recommendations == ["LOW Fonts: Self-host Google Fonts."]
    # the Images message only differs in its numbers, so it is the same finding
    assert not any(label.startswith("HIGH Images") for label in comparison.new_recommendations)
    assert any("Caching" in label for label in comparison.new_recommendations)


def test_url_change_is_flagged():
    comparison = compare(_baseline(final_url="https://other.example/"), _result())
    assert comparison.url_changed
    assert comparison.baseline_url == "https://other.example/"
    assert not compare(_baseline(final_url="http://demo.example"), _result()).url_changed


def test_regressions_respect_percent_and_noise_floor():
    comparison = compare(_baseline(), _result())
    failures = check_regressions(comparison, 10)
    assert len(failures) == 1
    assert "TTFB (median) regressed from 1000 ms to 1200 ms (+200 ms (+20.0%))" in failures[0]
    assert check_regressions(comparison, 25) == []
    # +40 ms is +100 % but below the 50 ms noise floor
    tiny = compare(_baseline(ttfb_median_ms=40.0, total_median_ms=1500.0), _result(ttfb=80.0))
    assert check_regressions(tiny, 10) == []


def test_improvements_never_fail_and_zero_baseline_counts():
    assert check_regressions(compare(_baseline(ttfb_median_ms=5000.0, total_median_ms=5000.0), _result()), 0) == []
    zero = MetricDelta("html_bytes", "HTML size", "bytes", 0, 4096, True)
    assert zero.percent is None
    assert zero.regressed(500)
    assert format_change(zero) == "+4.0 KB"
    assert format_change(MetricDelta("images", "Images", "count", 6, 4)) == "-2 (-33.3%)"
    assert format_change(MetricDelta("images", "Images", "count", 6, 6)) == "no change"


@pytest.mark.parametrize(
    "content, message",
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "expected an object"),
        ('{"ttfb_median_ms": 1}', "missing numeric 'total_median_ms'"),
        ('{"ttfb_median_ms": true, "total_median_ms": 1, "html_bytes": 1}', "missing numeric 'ttfb_median_ms'"),
        ('{"ttfb_median_ms": 1, "total_median_ms": 1, "html_bytes": 1, "page": []}', "invalid 'page'"),
    ],
)
def test_load_baseline_rejects_bad_files(tmp_path, content, message):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BaselineError, match=message):
        load_baseline(str(path))


def test_load_baseline_missing_file(tmp_path):
    with pytest.raises(BaselineError, match="cannot read baseline"):
        load_baseline(str(tmp_path / "nope.json"))


def test_cli_json_includes_comparison(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    path = _write(tmp_path, _baseline())
    assert cli.main(["demo.example", "--json", "--baseline", path]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["comparison"]["metrics"][0]["delta"] == 200.0
    assert data["comparison"]["baseline_url"] == "http://demo.example/"


def test_cli_json_without_baseline_has_null_comparison(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    assert cli.main(["demo.example", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["comparison"] is None


def test_cli_max_regression_fails_build(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    path = _write(tmp_path, _baseline())
    assert cli.main(["demo.example", "--json", "--baseline", path, "--max-regression", "10"]) == 1
    assert "budget exceeded: TTFB (median) regressed" in capsys.readouterr().err
    assert cli.main(["demo.example", "--json", "--baseline", path, "--max-regression", "50"]) == 0


def test_cli_saved_report_round_trips_as_baseline(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    assert cli.main(["demo.example", "--json"]) == 0
    path = tmp_path / "saved.json"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert cli.main(["demo.example", "--json", "--baseline", str(path), "--max-regression", "0"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert all(m["delta"] == 0 for m in data["comparison"]["metrics"])
    assert data["comparison"]["new_recommendations"] == []


def test_cli_baseline_errors_exit_2(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: pytest.fail("must not audit"))
    assert cli.main(["demo.example", "--max-regression", "5"]) == 2
    assert "--max-regression needs --baseline" in capsys.readouterr().err
    assert cli.main(["demo.example", "--baseline", str(tmp_path / "missing.json")]) == 2
    assert "cannot read baseline" in capsys.readouterr().err


def test_cli_rejects_negative_max_regression(capsys):
    with pytest.raises(SystemExit):
        cli.main(["demo.example", "--baseline", "x.json", "--max-regression", "-1"])
    assert "must be >= 0" in capsys.readouterr().err


def test_cli_warns_on_url_change(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    path = _write(tmp_path, _baseline(final_url="https://other.example/"))
    assert cli.main(["demo.example", "--json", "--baseline", path]) == 0
    assert "different URL" in capsys.readouterr().err


def test_markdown_has_comparison_section():
    result = _result()
    result.comparison = compare(_baseline(), result)
    md = render_markdown(result)
    assert "### Compared with baseline: http://demo.example/" in md
    assert "| Metric | Baseline | Now | Change |" in md
    assert "| TTFB (median) | 1000 ms | 1200 ms | +200 ms (+20.0%) |" in md
    assert "| HTML size | 40.0 KB | 40.0 KB | no change |" in md
    assert "### Compared with baseline" not in render_markdown(_result())


def test_terminal_render_has_comparison_table():
    from rich.console import Console

    result = _result()
    baseline = _baseline()
    baseline["page"]["plugins"] = ["[bold]odd[/bold]"]
    result.comparison = compare(baseline, result)
    console = Console(record=True, width=200)
    render(result, console=console)
    text = console.export_text()
    assert "Compared with baseline: http://demo.example/" in text
    assert "+200 ms (+20.0%)" in text
    assert "[bold]odd[/bold]" in text  # escaped, not interpreted as markup
