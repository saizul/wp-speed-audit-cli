import pytest

from wp_speed_audit import cli
from wp_speed_audit.audit import AuditResult
from wp_speed_audit.headers import CacheInfo
from wp_speed_audit.recommend import Recommendation
from wp_speed_audit.report import render_markdown

from test_recommend_and_cli import _slow_result
from wp_speed_audit.recommend import build_recommendations


def _result():
    result = _slow_result()
    result.recommendations = build_recommendations(result)
    return result


def test_markdown_has_sections_and_valid_tables():
    md = render_markdown(_result())
    assert md.startswith("## WordPress speed audit: http://demo.example/")
    for heading in ("### Overview", "### Assets", "### Recommendations"):
        assert heading in md
    assert "| Metric | Value |" in md
    assert "| TTFB (median) | 1200 ms |" in md
    assert "| Theme | astra |" in md
    assert "| HIGH | Caching |" in md
    # every table row has the same number of cells as its header
    block = []
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            block.append(line)
        elif block:
            cols = {len(row.replace("\\|", "").split("|")) for row in block}
            assert len(cols) == 1
            block = []


def test_markdown_escapes_pipes_and_newlines():
    result = AuditResult(url="https://x.example/", final_url="https://x.example/", cache=CacheInfo())
    result.recommendations = [Recommendation("low", "Misc", "use a | b\nsecond line")]
    md = render_markdown(result)
    assert "use a \\| b second line" in md


def test_markdown_empty_recommendations():
    md = render_markdown(AuditResult(url="https://x.example/", cache=CacheInfo()))
    assert "| - | - | Nothing to flag. |" in md


def test_cli_markdown_output(monkeypatch, capsys):
    result = _result()
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: result)
    assert cli.main(["demo.example", "--markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## WordPress speed audit")
    assert out.endswith("\n")


def test_json_and_markdown_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["demo.example", "--json", "--markdown"])
