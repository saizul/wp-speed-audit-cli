# wp-speed-audit

[![CI](https://github.com/saizul/wp-speed-audit-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/wp-speed-audit-cli/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white) ![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

A small command-line tool that audits a WordPress page **from the outside** (no login, no plugin install) and tells you what to fix first.

It measures the things that usually decide whether a WordPress site feels fast:

- **TTFB and total HTML time**, sampled several times on fresh connections (median reported)
- **Compression** (gzip / Brotli), **HTTP version**, HTML size
- **Page-cache evidence**: `x-cache`, `cf-cache-status`, `x-cache-handler`, `x-litespeed-cache`, `x-proxy-cache`, `age`, `Cache-Control` and more
- **CDN detection**: Cloudflare, CloudFront, Fastly, Akamai, Sucuri, BunnyCDN, KeyCDN, Imperva, QUIC.cloud and others
- **Images**: count, formats (flags anything that is not WebP/AVIF/SVG) and size via `HEAD` `Content-Length` (flags files over 200 KB)
- **Render-blocking** CSS and JS in `<head>`
- **Third-party** script and stylesheet hosts
- **WordPress footprint**: version, theme and plugins, read from `/wp-content/themes/<slug>/` and `/wp-content/plugins/<slug>/` paths
- Optional **Core Web Vitals field data** (`--crux`): p75 LCP, INP and CLS of real Chrome users from the Chrome UX Report API, shown next to the lab measurements
- A **prioritised list of recommendations** (high / medium / low)

## Install

Requires Python 3.9+.

```bash
git clone <this repository>
cd wp-speed-audit-cli
python -m venv .venv && source .venv/bin/activate
pip install .            # or: pip install -e ".[dev]" for development
```

## Usage

```bash
wp-speed-audit https://example.com/
wp-speed-audit example.com/blog/ -n 5            # 5 timing samples
wp-speed-audit example.com --no-image-check      # skip image HEAD requests
wp-speed-audit example.com --json > audit.json   # machine-readable
wp-speed-audit example.com --user-agent "Mozilla/5.0" --timeout 30   # custom UA and timeout
wp-speed-audit example.com --markdown > audit.md # paste into a GitHub issue
CRUX_API_KEY=... wp-speed-audit example.com --crux   # add real-user Core Web Vitals
python -m wp_speed_audit example.com             # without the console script
```

| Option | Meaning |
| --- | --- |
| `-n, --samples N` | Timing samples; the median is reported (default 3) |
| `--timeout S` | Per-request timeout in seconds, must be > 0 (default 20) |
| `--user-agent UA` | `User-Agent` header for every request (page, Brotli probe, image `HEAD`s). Useful when a WAF blocks the default |
| `--max-images N` | Maximum images to `HEAD`-check (default 40) |
| `--no-image-check` | Do not request image sizes |
| `--json` | Print JSON instead of tables |
| `--markdown` | Print the report as Markdown tables, ready to paste into a GitHub issue (cannot be combined with `--json`) |
| `--crux` | Add Core Web Vitals field data from the Chrome UX Report API; the API key is read from the `CRUX_API_KEY` environment variable (see [Field data](#field-data-core-web-vitals-from-crux)) |
| `--crux-form-factor {all,phone,desktop,tablet}` | Device type for `--crux` (default `all`) |
| `--max-ttfb SECONDS` | CI budget: exit `1` if the median time to first byte is above this |
| `--max-total SECONDS` | CI budget: exit `1` if the median total download time is above this |
| `--max-html-kb KB` | CI budget: exit `1` if the HTML document is larger than this (1 KB = 1024 bytes) |

Exit codes: `0` success (and all budgets met), `1` a `--max-*` budget was exceeded (each violation is printed to stderr as `budget exceeded: ...`; the report is still printed), `2` the page could not be fetched, or `--crux` was given without an API key.

```bash
# fail a CI job when the homepage is slower than 800 ms TTFB or its HTML exceeds 150 KB
wp-speed-audit example.com --json --max-ttfb 0.8 --max-html-kb 150 > audit.json
```

## Field data (Core Web Vitals from CrUX)

Everything above is a lab measurement taken from the machine running the tool. `--crux` adds what real Chrome users experienced over the trailing 28 days, from the [Chrome UX Report API](https://developer.chrome.com/docs/crux/api):

```bash
export CRUX_API_KEY=your-key          # never passed on the command line
wp-speed-audit example.com/shop/ --crux
wp-speed-audit example.com --crux --crux-form-factor phone --markdown > audit.md
```

```text
        Field data (Chrome UX Report, real users, p75)
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Metric ┃ p75                   ┃ Rating            ┃ Good loads ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ LCP    │ 2100 ms               │ good              │ 81%        │
│ INP    │ 350 ms                │ needs improvement │ 70%        │
│ CLS    │ 0.05                  │ good              │ 90%        │
│ FCP    │ 1500 ms               │ good              │ -          │
│ TTFB   │ 950 ms  (lab: 120 ms) │ needs improvement │ -          │
└────────┴───────────────────────┴───────────────────┴────────────┘
Core Web Vitals assessment: FAILED (page-level data for https://example.com/shop/; devices: phone; 2026-03-01 to 2026-03-28)
```

(Illustrative numbers.)

- **API key**: create one in the Google Cloud console with the *Chrome UX Report API* enabled (it is free) and put it in `CRUX_API_KEY`. The key is sent in the `X-Goog-Api-Key` header and is never printed, logged or written to the JSON/Markdown output.
- **Page first, then origin**: the audited URL (after redirects) is queried first. If Chrome has too little traffic for that page, the tool falls back to the whole origin and says so (`page-level` / `origin-level`).
- **Ratings** use the standard thresholds: LCP 2.5 s / 4 s, INP 200 ms / 500 ms, CLS 0.1 / 0.25, FCP 1.8 s / 3 s, TTFB 0.8 s / 1.8 s. The assessment is `PASSED` when LCP, INP and CLS are all good at the 75th percentile (INP may be absent on pages with few interactions).
- "Good loads" is the share of page loads in the good bucket.
- LCP, INP or CLS rated *needs improvement* or *poor* adds a `Core Web Vitals` recommendation (medium / high priority).
- With `--json`, the data is under `field_data` (`null` without `--crux`): `scope`, `target`, `form_factor`, `period_start`, `period_end`, `assessment`, `error` and a `metrics` list of `{key, label, p75, unit, rating, good_share}`.
- If the site has no CrUX data, the key is rejected, the quota is exhausted or the API is unreachable, a `warning:` is printed to stderr, the report says why field data is unavailable, and the rest of the audit is unaffected (exit code stays `0`).

## Sample output

Produced by auditing the bundled test fixture (`tests/fixtures/sample.html`) served from a local web server, so timings are not representative. Output is abridged for width (`...` marks shortened text):

```text
                       WordPress speed audit: http://127.0.0.1:8765/
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric              ┃ Value                                                             ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Status              │ 200 (0 redirect(s))                                               │
│ TTFB (median)       │ 2 ms                                                              │
│ Total time (median) │ 2 ms                                                              │
│ Compression         │ none                                                              │
│ Page cache          │ NONE                                                              │
│ CDN                 │ none detected                                                     │
│ HTML size           │ 2 KB                                                              │
│ WordPress           │ yes (v6.5.2)                                                      │
│ Theme               │ astra                                                             │
│ Plugins             │ elementor, contact-form-7, wordpress-seo, preloaded, module-thing │
└─────────────────────┴───────────────────────────────────────────────────────────────────┘

                                    Assets
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Item                   ┃ Details                                                            ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Images                 │ 6 (jpeg: 2, png: 1, webp: 1, avif: 1, svg: 1); known size 714 KB   │
│   oversized            │ 625 KB  http://127.0.0.1:8765/wp-content/uploads/2025/01/hero.jpg  │
│ Render-blocking CSS    │ 4                                                                  │
│ Render-blocking JS     │ 2                                                                  │
│ 3rd-party script hosts │ www.googletagmanager.com, cdn.example-widgets.net, ...             │
│ 3rd-party style hosts  │ fonts.googleapis.com                                               │
└────────────────────────┴────────────────────────────────────────────────────────────────────┘

                                  Recommendations
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Priority ┃ Area            ┃ Recommendation                                                ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ HIGH     │ Security        │ The page is served over plain HTTP. Install a TLS ...         │
│ HIGH     │ Caching         │ No page-cache headers were detected. Enable a page cache ...  │
│ HIGH     │ Compression     │ The HTML response is not compressed. Enable gzip or Brotli.   │
│ HIGH     │ Images          │ 1 image(s) exceed 200 KB; the largest is 625 KB ...           │
│ MEDIUM   │ CDN             │ No CDN detected. ...                                          │
│ MEDIUM   │ Images          │ 3 of 6 images are not WebP/AVIF. Convert them ...             │
│ MEDIUM   │ Render blocking │ 2 script(s) in <head> block rendering. Add defer/async ...    │
│ MEDIUM   │ Render blocking │ 4 stylesheets in <head> block rendering. Combine them ...     │
└──────────┴─────────────────┴───────────────────────────────────────────────────────────────┘
```

`--json` emits the same data (plus every timing sample) as a single JSON document, e.g. `data["recommendations"][0]`:

```json
{ "priority": "high", "area": "Security", "message": "The page is served over plain HTTP. ..." }
```

## How it works and its limits

- Timings include DNS, TCP and TLS because every sample uses a fresh connection. The headers of the **last** sample are used for cache detection, so a cold page cache has had a chance to warm up.
- The HTTP client is `requests`, which speaks HTTP/1.x only. The tool therefore reports what `requests` negotiated and notes when `Alt-Svc` advertises HTTP/3; it does not claim the site lacks HTTP/2.
- Brotli support is detected with an extra request sending `Accept-Encoding: br`.
- Image sizes come from `HEAD` `Content-Length`. Servers that omit it show as unknown (`-`), and image formats are inferred from the URL extension.
- Plugin and theme detection only sees assets that load on the audited page. Plugins that add nothing to the front end are invisible from outside.
- It audits the one URL you give it; it is not a replacement for Lighthouse or WebPageTest lab data. Field data is only available with `--crux` and only for sites with enough Chrome traffic.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests parse a saved HTML fixture and synthetic header dictionaries; they never touch the network (the CrUX API is faked as well).

Layout: `analyzer.py` (HTML parsing), `headers.py` (cache/CDN), `crux.py` (CrUX request/response handling), `network.py` (all HTTP), `recommend.py` (rules), `audit.py` (orchestration), `report.py` (rich output), `cli.py`.

## License

MIT, see [LICENSE](LICENSE). Built by [Saizul Amin](https://saizul.com).

## Run with Docker

A multi-arch image (amd64, arm64) is published to GitHub Container Registry on every change to `main`:

```bash
docker run --rm ghcr.io/saizul/wp-speed-audit-cli:latest https://example.com
# write reports to the current folder
docker run --rm -v "$PWD:/work" ghcr.io/saizul/wp-speed-audit-cli:latest --help
```
