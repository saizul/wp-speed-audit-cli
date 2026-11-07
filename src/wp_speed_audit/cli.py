"""Command-line interface for wp-speed-audit."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from typing import List, Optional

from rich.console import Console

from . import __version__
from .audit import run_audit
from .network import USER_AGENT, AuditError
from .report import render


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
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of tables")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    url = normalize_url(args.url)
    status_console = Console(stderr=True)
    try:
        spinner = contextlib.nullcontext() if args.json else status_console.status("Auditing...")
        with spinner:
            result = run_audit(
                url,
                samples=args.samples,
                timeout=args.timeout,
                max_images=args.max_images,
                check_images=not args.no_image_check,
                user_agent=args.user_agent,
            )
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        render(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
