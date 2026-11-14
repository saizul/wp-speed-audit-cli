"""Command-line interface for wp-speed-audit."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from typing import List, Optional

from rich.console import Console

from . import __version__
from .audit import run_audit
from .baseline import BaselineError, check_regressions, compare, load_baseline
from .crux import API_KEY_ENV, FORM_FACTORS
from .network import USER_AGENT, AuditError
from .report import render, render_markdown


def normalize_url(url: str) -> str:
    url = url.strip()
    return url if "://" in url else "https://" + url


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if not number >= 0:  # also rejects NaN
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def check_thresholds(result, max_ttfb: Optional[float], max_total: Optional[float], max_html_kb: Optional[float]) -> List[str]:
    """Return one human-readable message per exceeded budget (empty list = pass)."""
    failures: List[str] = []
    if max_ttfb is not None and result.ttfb_median_ms / 1000.0 > max_ttfb:
        failures.append(f"median TTFB {result.ttfb_median_ms / 1000.0:.3f}s exceeds --max-ttfb {max_ttfb:g}s")
    if max_total is not None and result.total_median_ms / 1000.0 > max_total:
        failures.append(f"median total time {result.total_median_ms / 1000.0:.3f}s exceeds --max-total {max_total:g}s")
    if max_html_kb is not None and result.html_bytes / 1024.0 > max_html_kb:
        failures.append(f"HTML size {result.html_bytes / 1024.0:.1f} KB exceeds --max-html-kb {max_html_kb:g} KB")
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wp-speed-audit",
        description="Audit a WordPress page's speed signals from the outside.",
    )
    parser.add_argument("url", help="page URL to audit (https:// is assumed if omitted)")
    parser.add_argument("-n", "--samples", type=_positive_int, default=3, help="timing samples; the median is reported (default: 3)")
    parser.add_argument("--timeout", type=_positive_float, default=20.0, help="per-request timeout in seconds (default: 20)")
    parser.add_argument("--user-agent", default=USER_AGENT, metavar="UA", help="User-Agent header sent with every request (default: wp-speed-audit/<version>)")
    parser.add_argument("--max-images", type=_positive_int, default=40, help="max images to HEAD-check for size (default: 40)")
    parser.add_argument("--no-image-check", action="store_true", help="skip HEAD requests for image sizes")
    crux = parser.add_argument_group("field data", "Core Web Vitals of real Chrome users from the CrUX API")
    crux.add_argument("--crux", action="store_true", help=f"add p75 LCP, INP and CLS from the Chrome UX Report API (API key is read from ${API_KEY_ENV})")
    crux.add_argument("--crux-form-factor", choices=FORM_FACTORS, default="all", help="device type for --crux (default: all)")
    budget = parser.add_argument_group("CI budgets", "exit with status 1 when a budget is exceeded")
    budget.add_argument("--max-ttfb", type=_positive_float, metavar="SECONDS", help="fail if the median time to first byte is above this many seconds")
    budget.add_argument("--max-total", type=_positive_float, metavar="SECONDS", help="fail if the median total download time is above this many seconds")
    budget.add_argument("--max-html-kb", type=_positive_float, metavar="KB", help="fail if the HTML document is larger than this many KB (1 KB = 1024 bytes)")
    budget.add_argument("--baseline", metavar="FILE", help="compare with a report saved earlier with --json and show what changed")
    budget.add_argument("--max-regression", type=_non_negative_float, metavar="PERCENT", help="with --baseline: fail if median TTFB, median total time or HTML size grew by more than this many percent")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="print machine-readable JSON instead of tables")
    fmt.add_argument("--markdown", action="store_true", help="print the report as Markdown tables (paste into a GitHub issue)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    url = normalize_url(args.url)
    status_console = Console(stderr=True)
    crux_key = None
    baseline = None
    if args.max_regression is not None and not args.baseline:
        print("error: --max-regression needs --baseline FILE", file=sys.stderr)
        return 2
    if args.baseline:
        try:
            baseline = load_baseline(args.baseline)
        except BaselineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.crux:
        crux_key = os.environ.get(API_KEY_ENV, "").strip()
        if not crux_key:
            print(f"error: --crux needs a Chrome UX Report API key in the {API_KEY_ENV} environment variable", file=sys.stderr)
            return 2
    try:
        spinner = contextlib.nullcontext() if args.json or args.markdown else status_console.status("Auditing...")
        with spinner:
            result = run_audit(
                url,
                samples=args.samples,
                timeout=args.timeout,
                max_images=args.max_images,
                check_images=not args.no_image_check,
                user_agent=args.user_agent,
                crux_api_key=crux_key,
                crux_form_factor=args.crux_form_factor,
            )
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if baseline is not None:
        result.comparison = compare(baseline, result)
        if result.comparison.url_changed:
            print(f"warning: the baseline was taken for a different URL ({result.comparison.baseline_url})", file=sys.stderr)

    if result.field_data is not None and result.field_data.error:
        print(f"warning: {result.field_data.error}", file=sys.stderr)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif args.markdown:
        print(render_markdown(result), end="")
    else:
        render(result)

    failures = check_thresholds(result, args.max_ttfb, args.max_total, args.max_html_kb)
    if result.comparison is not None and args.max_regression is not None:
        failures += check_regressions(result.comparison, args.max_regression)
    for message in failures:
        print(f"budget exceeded: {message}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
