"""Core Web Vitals field data from the Chrome UX Report (CrUX) API.

This module is pure: it builds request bodies and turns API responses into
:class:`FieldData`. The HTTP call itself lives in :mod:`wp_speed_audit.network`.

CrUX reports what real Chrome users experienced over the trailing 28 days,
which complements the lab-style measurements the rest of the tool takes from
a single machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

API_KEY_ENV = "CRUX_API_KEY"
FORM_FACTORS = ("all", "phone", "desktop", "tablet")

GOOD, NEEDS_IMPROVEMENT, POOR = "good", "needs improvement", "poor"

# API metric name -> (short key, label, unit, good up to, poor above)
METRICS = {
    "largest_contentful_paint": ("lcp", "LCP", "ms", 2500, 4000),
    "interaction_to_next_paint": ("inp", "INP", "ms", 200, 500),
    "cumulative_layout_shift": ("cls", "CLS", "", 0.1, 0.25),
    "first_contentful_paint": ("fcp", "FCP", "ms", 1800, 3000),
    "experimental_time_to_first_byte": ("ttfb", "TTFB", "ms", 800, 1800),
}
CORE_WEB_VITALS = ("lcp", "inp", "cls")


@dataclass
class FieldMetric:
    key: str  # lcp, inp, cls, fcp or ttfb
    label: str
    p75: float
    unit: str  # "ms", or "" for the unitless CLS
    rating: str  # good / needs improvement / poor
    good_share: Optional[float] = None  # share of page loads rated good, 0-100

    @property
    def display(self) -> str:
        return f"{self.p75:.2f}" if not self.unit else f"{self.p75:.0f} {self.unit}"


@dataclass
class FieldData:
    """CrUX result for one page or origin (``error`` set when unavailable)."""

    scope: str = ""  # "url" or "origin"
    target: str = ""  # the URL or origin the record describes
    form_factor: str = "all"
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    metrics: List[FieldMetric] = field(default_factory=list)
    assessment: Optional[str] = None  # "passed", "failed", or None when a core metric is missing
    error: Optional[str] = None

    def metric(self, key: str) -> Optional[FieldMetric]:
        return next((m for m in self.metrics if m.key == key), None)


def origin_of(url: str) -> str:
    """``https://example.com/blog/?x=1`` -> ``https://example.com``."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def rate(value: float, good: float, poor: float) -> str:
    """Rate a p75 value against the Core Web Vitals thresholds."""
    if value <= good:
        return GOOD
    return POOR if value > poor else NEEDS_IMPROVEMENT


def build_query(target: str, scope: str, form_factor: str = "all") -> Dict[str, Any]:
    """Request body for ``records:queryRecord``."""
    if scope not in ("url", "origin"):
        raise ValueError(f"scope must be 'url' or 'origin', not {scope!r}")
    if form_factor not in FORM_FACTORS:
        raise ValueError(f"form factor must be one of {', '.join(FORM_FACTORS)}")
    body: Dict[str, Any] = {scope: target, "metrics": list(METRICS)}
    if form_factor != "all":
        body["formFactor"] = form_factor.upper()
    return body


def _date(value: Any) -> Optional[str]:
    try:
        return f"{int(value['year']):04d}-{int(value['month']):02d}-{int(value['day']):02d}"
    except (KeyError, TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    # the API sends CLS percentiles as strings ("0.10") and timings as numbers
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def _good_share(histogram: Any) -> Optional[float]:
    try:
        return round(float(histogram[0]["density"]) * 100, 1)
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def parse_record(payload: Any, form_factor: str = "all") -> FieldData:
    """Turn a successful ``queryRecord`` response into :class:`FieldData`.

    Metrics without a usable p75 are skipped. The assessment follows Google's
    rule: LCP, INP and CLS must all be good at the 75th percentile. INP is
    allowed to be missing (pages with few interactions have none); without LCP
    or CLS there is no assessment.
    """
    record = payload.get("record") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        return FieldData(form_factor=form_factor, error="unexpected response from the CrUX API")
    key = record.get("key") if isinstance(record.get("key"), dict) else {}
    scope = "url" if "url" in key else "origin"
    data = FieldData(scope=scope, target=str(key.get(scope, "")), form_factor=form_factor)
    period = record.get("collectionPeriod") if isinstance(record.get("collectionPeriod"), dict) else {}
    data.period_start = _date(period.get("firstDate"))
    data.period_end = _date(period.get("lastDate"))

    raw_metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    for api_name, (short, label, unit, good, poor) in METRICS.items():
        raw = raw_metrics.get(api_name)
        if not isinstance(raw, dict):
            continue
        percentiles = raw.get("percentiles") if isinstance(raw.get("percentiles"), dict) else {}
        p75 = _to_float(percentiles.get("p75"))
        if p75 is None:
            continue
        data.metrics.append(FieldMetric(short, label, p75, unit, rate(p75, good, poor), _good_share(raw.get("histogram"))))

    if not data.metrics:
        data.error = "the CrUX record contains no usable metrics"
        return data
    core = [data.metric(k) for k in CORE_WEB_VITALS]
    if data.metric("lcp") is not None and data.metric("cls") is not None:
        data.assessment = "passed" if all(m.rating == GOOD for m in core if m is not None) else "failed"
    return data


def error_message(status_code: int, payload: Any) -> str:
    """Readable message for a non-200 CrUX response (never includes the key)."""
    detail = ""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        detail = str(payload["error"].get("message") or "")
    if status_code in (400, 401, 403):
        hint = f"check the API key in {API_KEY_ENV} and that the Chrome UX Report API is enabled for it"
        return f"CrUX API rejected the request (HTTP {status_code}{': ' + detail if detail else ''}); {hint}"
    if status_code == 429:
        return "CrUX API quota exceeded (HTTP 429); try again later"
    return f"CrUX API returned HTTP {status_code}{': ' + detail if detail else ''}"
