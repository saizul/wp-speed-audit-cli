"""Compare an audit with a previous ``--json`` report to catch regressions.

Everything here is pure: the baseline is a plain dictionary (the JSON the tool
wrote earlier) and the current run is an :class:`AuditResult`, so it is easy
to test without the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .audit import AuditResult

# A metric only counts as regressed when it grows by more than the allowed
# percentage AND by at least this absolute amount, so a 3 ms -> 6 ms jitter on a
# local server is not reported as a "100 % slower" failure.
NOISE_FLOOR = {"ms": 50.0, "bytes": 1024.0, "count": 1.0}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_KEY_WORDS = 4


class BaselineError(ValueError):
    """Raised when a baseline file cannot be read or is not a wp-speed-audit report."""


@dataclass
class MetricDelta:
    key: str
    label: str
    unit: str  # "ms", "bytes" or "count"
    before: float
    after: float
    budgeted: bool = False  # checked by --max-regression

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def percent(self) -> Optional[float]:
        """Relative change in percent; ``None`` when the baseline value is 0."""
        if self.before == 0:
            return None
        return self.delta / self.before * 100.0

    def regressed(self, max_percent: float) -> bool:
        if self.delta < NOISE_FLOOR[self.unit]:
            return False
        percent = self.percent
        return percent is None or percent > max_percent

    def to_dict(self) -> Dict[str, Any]:
        percent = self.percent
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "before": self.before,
            "after": self.after,
            "delta": round(self.delta, 1),
            "percent": None if percent is None else round(percent, 1),
        }


@dataclass
class Comparison:
    baseline_url: str
    url_changed: bool = False
    metrics: List[MetricDelta] = field(default_factory=list)
    cache_before: Optional[str] = None
    cache_after: Optional[str] = None
    plugins_added: List[str] = field(default_factory=list)
    plugins_removed: List[str] = field(default_factory=list)
    new_recommendations: List[str] = field(default_factory=list)
    resolved_recommendations: List[str] = field(default_factory=list)

    def metric(self, key: str) -> Optional[MetricDelta]:
        return next((m for m in self.metrics if m.key == key), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline_url": self.baseline_url,
            "url_changed": self.url_changed,
            "metrics": [m.to_dict() for m in self.metrics],
            "cache_before": self.cache_before,
            "cache_after": self.cache_after,
            "plugins_added": list(self.plugins_added),
            "plugins_removed": list(self.plugins_removed),
            "new_recommendations": list(self.new_recommendations),
            "resolved_recommendations": list(self.resolved_recommendations),
        }


def load_baseline(path: str) -> Dict[str, Any]:
    """Read and validate a report previously written with ``--json``."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise BaselineError(f"baseline {path} is not valid JSON: {exc}") from exc
    validate_baseline(data, path)
    return data


def validate_baseline(data: Any, source: str = "baseline") -> None:
    if not isinstance(data, dict):
        raise BaselineError(f"{source} is not a wp-speed-audit --json report (expected an object)")
    for key in ("ttfb_median_ms", "total_median_ms", "html_bytes"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BaselineError(f"{source} is not a wp-speed-audit --json report (missing numeric '{key}')")
    page = data.get("page", {})
    if not isinstance(page, dict):
        raise BaselineError(f"{source} has an invalid 'page' section")
    recs = data.get("recommendations", [])
    if not isinstance(recs, list):
        raise BaselineError(f"{source} has an invalid 'recommendations' section")


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _snapshot(data: Dict[str, Any]) -> Dict[str, float]:
    page = data.get("page") or {}
    hosts = set(_list(page.get("third_party_script_hosts"))) | set(_list(page.get("third_party_style_hosts")))
    return {
        "ttfb_median_ms": float(data.get("ttfb_median_ms") or 0),
        "total_median_ms": float(data.get("total_median_ms") or 0),
        "html_bytes": float(data.get("html_bytes") or 0),
        "images": float(len(_list(page.get("images")))),
        "render_blocking_css": float(len(_list(page.get("render_blocking_css")))),
        "render_blocking_js": float(len(_list(page.get("render_blocking_js")))),
        "third_party_hosts": float(len(hosts)),
    }


# key, label, unit, checked by --max-regression
_METRICS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("ttfb_median_ms", "TTFB (median)", "ms", True),
    ("total_median_ms", "Total time (median)", "ms", True),
    ("html_bytes", "HTML size", "bytes", True),
    ("images", "Images", "count", False),
    ("render_blocking_css", "Render-blocking CSS", "count", False),
    ("render_blocking_js", "Render-blocking JS", "count", False),
    ("third_party_hosts", "3rd-party hosts", "count", False),
)


def _rec_key(rec: Dict[str, Any]) -> str:
    """Identify a finding independently of the numbers and URLs in its message.

    The area plus the first few words (numbers masked) tell findings apart, e.g.
    ``Images: # image(s) exceed #`` versus ``Images: # of # images``.
    """
    words = _NUMBER_RE.sub("#", str(rec.get("message", ""))).split()[:_KEY_WORDS]
    return f"{rec.get('area', '?')}: {' '.join(words)}"


def _rec_label(rec: Dict[str, Any]) -> str:
    return f"{str(rec.get('priority', '?')).upper()} {rec.get('area', '?')}: {rec.get('message', '')}"


def _recs_by_key(recs: List[Any]) -> Dict[str, Dict[str, Any]]:
    return {_rec_key(r): r for r in recs if isinstance(r, dict)}


def compare(baseline: Dict[str, Any], result: "AuditResult") -> Comparison:
    """Diff ``result`` against a baseline report dictionary."""
    validate_baseline(baseline)
    current = result.to_dict()
    current.pop("comparison", None)
    before, after = _snapshot(baseline), _snapshot(current)

    baseline_url = str(baseline.get("final_url") or baseline.get("url") or "")
    current_url = result.final_url or result.url
    comparison = Comparison(
        baseline_url=baseline_url,
        url_changed=bool(baseline_url) and baseline_url.rstrip("/") != current_url.rstrip("/"),
        cache_before=(baseline.get("cache") or {}).get("verdict") if isinstance(baseline.get("cache"), dict) else None,
        cache_after=result.cache.verdict,
    )
    for key, label, unit, budgeted in _METRICS:
        comparison.metrics.append(MetricDelta(key, label, unit, before[key], after[key], budgeted))

    old_plugins = [p for p in _list((baseline.get("page") or {}).get("plugins")) if isinstance(p, str)]
    comparison.plugins_added = [p for p in result.page.plugins if p not in old_plugins]
    comparison.plugins_removed = [p for p in old_plugins if p not in result.page.plugins]

    old_recs = _recs_by_key(_list(baseline.get("recommendations")))
    new_recs = _recs_by_key(current.get("recommendations", []))
    comparison.new_recommendations = [_rec_label(r) for k, r in new_recs.items() if k not in old_recs]
    comparison.resolved_recommendations = [_rec_label(r) for k, r in old_recs.items() if k not in new_recs]
    return comparison


def check_regressions(comparison: Comparison, max_percent: float) -> List[str]:
    """One message per budgeted metric that grew by more than ``max_percent``."""
    failures = []
    for metric in comparison.metrics:
        if metric.budgeted and metric.regressed(max_percent):
            failures.append(
                f"{metric.label} regressed from {format_value(metric.before, metric.unit)} "
                f"to {format_value(metric.after, metric.unit)} ({format_change(metric)}), "
                f"more than --max-regression {max_percent:g}%"
            )
    return failures


def format_value(value: float, unit: str) -> str:
    if unit == "ms":
        return f"{value:.0f} ms"
    if unit == "bytes":
        return f"{value / 1024:.1f} KB"
    return f"{value:.0f}"


def format_change(metric: MetricDelta) -> str:
    """``+120 ms (+15.0%)``, ``-2`` or ``no change``."""
    if metric.delta == 0:
        return "no change"
    sign = "+" if metric.delta > 0 else "-"
    text = sign + format_value(abs(metric.delta), metric.unit)
    percent = metric.percent
    if percent is not None:
        text += f" ({percent:+.1f}%)"
    return text
