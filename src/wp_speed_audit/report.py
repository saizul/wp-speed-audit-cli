"""Rich terminal rendering of an :class:`AuditResult`."""

from __future__ import annotations

from typing import List, Optional

from rich.console import Console
from rich.table import Table

from .analyzer import format_counts
from .audit import AuditResult

_PRIORITY_STYLE = {"high": "bold red", "medium": "yellow", "low": "cyan"}
_RATING_STYLE = {"good": "green", "needs improvement": "yellow", "poor": "bold red"}


def _kb(size: Optional[int]) -> str:
    return "-" if size is None else f"{size / 1024:.0f} KB"


def _overview(result: AuditResult) -> Table:
    table = Table(title=f"WordPress speed audit: {result.final_url}", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", overflow="fold")

    samples = ", ".join(f"{v:.0f}" for v in result.ttfb_ms)
    http = result.http_version + (" (h3 advertised)" if result.h3_advertised else "")
    compression = result.compression or "none"
    if result.brotli:
        compression += " (Brotli supported)"
    cache = result.cache
    cache_text = cache.verdict.upper()
    if cache.headers:
        cache_text += " - " + ", ".join(f"{k}: {v}" for k, v in cache.headers.items())

    rows = [
        ("Status", f"{result.status_code} ({result.redirects} redirect(s))"),
        ("TTFB (median)", f"{result.ttfb_median_ms:.0f} ms  [samples: {samples}]"),
        ("Total time (median)", f"{result.total_median_ms:.0f} ms"),
        ("HTTP version", http),
        ("Compression", compression),
        ("Page cache", cache_text),
        ("Cache-Control", cache.cache_control or "-"),
        ("CDN", ", ".join(result.cdn) or "none detected"),
        ("Server", result.server or "-"),
        ("HTML size", _kb(result.html_bytes)),
        ("WordPress", "yes" + (f" (v{result.page.wp_version})" if result.page.wp_version else "") if result.page.is_wordpress else "not detected"),
        ("Theme", result.page.theme or "-"),
        ("Plugins", ", ".join(result.page.plugins) or "-"),
    ]
    for name, value in rows:
        table.add_row(name, value)
    return table


def _assets(result: AuditResult) -> Table:
    page = result.page
    table = Table(title="Assets", show_header=True, header_style="bold")
    table.add_column("Item")
    table.add_column("Details", overflow="fold")
    formats = ", ".join(f"{fmt}: {n}" for fmt, n in format_counts(page.images).items()) or "-"
    known = [i.size_bytes for i in page.images if i.size_bytes is not None]
    table.add_row("Images", f"{len(page.images)} ({formats}); known size {_kb(sum(known))}")
    for img in sorted((i for i in page.images if i.is_oversized), key=lambda i: -(i.size_bytes or 0))[:5]:
        table.add_row("  oversized", f"{_kb(img.size_bytes)}  {img.url}")
    table.add_row("Render-blocking CSS", str(len(page.render_blocking_css)))
    table.add_row("Render-blocking JS", str(len(page.render_blocking_js)))
    table.add_row("3rd-party script hosts", ", ".join(page.third_party_script_hosts) or "-")
    table.add_row("3rd-party style hosts", ", ".join(page.third_party_style_hosts) or "-")
    return table


def _recommendations(result: AuditResult) -> Table:
    table = Table(title="Recommendations", show_header=True, header_style="bold")
    table.add_column("Priority")
    table.add_column("Area")
    table.add_column("Recommendation", overflow="fold")
    for rec in result.recommendations:
        style = _PRIORITY_STYLE[rec.priority]
        table.add_row(f"[{style}]{rec.priority.upper()}[/]", rec.area, rec.message)
    if not result.recommendations:
        table.add_row("-", "-", "Nothing to flag. Nice work.")
    return table


def _field_rows(result: AuditResult) -> List[List[str]]:
    """Rows of the field-data table: metric, p75, rating, share of good loads."""
    data = result.field_data
    if data is None or data.error:
        return []
    rows = []
    for metric in data.metrics:
        share = "-" if metric.good_share is None else f"{metric.good_share:.0f}%"
        value = metric.display
        if metric.key == "ttfb":
            value += f"  (lab: {result.ttfb_median_ms:.0f} ms)"
        rows.append([metric.label, value, metric.rating, share])
    return rows


def _field_caption(result: AuditResult) -> str:
    data = result.field_data
    if data is None:
        return ""
    if data.error:
        return f"Field data unavailable: {data.error}"
    parts = [f"{'page' if data.scope == 'url' else 'origin'}-level data for {data.target}", f"devices: {data.form_factor}"]
    if data.period_start and data.period_end:
        parts.append(f"{data.period_start} to {data.period_end}")
    verdict = {"passed": "PASSED", "failed": "FAILED"}.get(data.assessment or "", "not enough data")
    return f"Core Web Vitals assessment: {verdict} ({'; '.join(parts)})"


def _field(result: AuditResult) -> Table:
    table = Table(title="Field data (Chrome UX Report, real users, p75)", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("p75", overflow="fold")
    table.add_column("Rating")
    table.add_column("Good loads")
    for label, value, rating, share in _field_rows(result):
        table.add_row(label, value, f"[{_RATING_STYLE[rating]}]{rating}[/]", share)
    return table


def render(result: AuditResult, console: Optional[Console] = None) -> None:
    console = console or Console()
    for table in (_overview(result), _assets(result)):
        console.print(table)
        console.print()
    if result.field_data is not None:
        if _field_rows(result):
            console.print(_field(result))
        console.print(_field_caption(result), markup=False, highlight=False, soft_wrap=True)
        console.print()
    console.print(_recommendations(result))
    console.print()


def _md(text: object) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_table(headers: List[str], rows: List[List[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(_md(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(result: AuditResult) -> str:
    """Return the audit as GitHub-flavoured Markdown, ready to paste into an issue."""
    page = result.page
    cache = result.cache
    compression = result.compression or "none"
    if result.brotli:
        compression += " (Brotli supported)"
    cache_text = cache.verdict.upper()
    if cache.headers:
        cache_text += " - " + ", ".join(f"{k}: {v}" for k, v in cache.headers.items())
    wordpress = "not detected"
    if page.is_wordpress:
        wordpress = "yes" + (f" (v{page.wp_version})" if page.wp_version else "")
    known = [i.size_bytes for i in page.images if i.size_bytes is not None]
    formats = ", ".join(f"{fmt}: {n}" for fmt, n in format_counts(page.images).items()) or "-"

    overview = [
        ["Status", f"{result.status_code} ({result.redirects} redirect(s))"],
        ["TTFB (median)", f"{result.ttfb_median_ms:.0f} ms"],
        ["Total time (median)", f"{result.total_median_ms:.0f} ms"],
        ["HTTP version", result.http_version + (" (h3 advertised)" if result.h3_advertised else "")],
        ["Compression", compression],
        ["Page cache", cache_text],
        ["Cache-Control", cache.cache_control or "-"],
        ["CDN", ", ".join(result.cdn) or "none detected"],
        ["Server", result.server or "-"],
        ["HTML size", _kb(result.html_bytes)],
        ["WordPress", wordpress],
        ["Theme", page.theme or "-"],
        ["Plugins", ", ".join(page.plugins) or "-"],
    ]
    assets = [
        ["Images", f"{len(page.images)} ({formats}); known size {_kb(sum(known))}"],
        ["Render-blocking CSS", len(page.render_blocking_css)],
        ["Render-blocking JS", len(page.render_blocking_js)],
        ["3rd-party script hosts", ", ".join(page.third_party_script_hosts) or "-"],
        ["3rd-party style hosts", ", ".join(page.third_party_style_hosts) or "-"],
    ]
    recs = [[r.priority.upper(), r.area, r.message] for r in result.recommendations]
    if not recs:
        recs = [["-", "-", "Nothing to flag."]]

    parts = [
        f"## WordPress speed audit: {_md(result.final_url or result.url)}",
        "### Overview",
        _md_table(["Metric", "Value"], overview),
        "### Assets",
        _md_table(["Item", "Details"], assets),
    ]
    if result.field_data is not None:
        parts.append("### Field data (Chrome UX Report)")
        if _field_rows(result):
            parts.append(_md_table(["Metric", "p75", "Rating", "Good loads"], _field_rows(result)))
        parts.append(_md(_field_caption(result)))
    parts += [
        "### Recommendations",
        _md_table(["Priority", "Area", "Recommendation"], recs),
    ]
    return "\n\n".join(parts) + "\n"
