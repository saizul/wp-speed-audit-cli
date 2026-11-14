# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `--baseline FILE` compares the audit with a report saved earlier with `--json`: median TTFB and total time, HTML size, image count, render-blocking assets, third-party hosts, page-cache verdict, plugins added or removed, and new or resolved recommendations. Shown as a table, a Markdown section and a `comparison` object in the JSON output.
- `--max-regression PERCENT` exits with status `1` when median TTFB, median total time or HTML size grew by more than the given percentage against the baseline (with a 50 ms / 1 KB noise floor).

### Changed

- `requires-python` is now `>=3.10`, matching the versions tested in CI.
- The JSON report always contains a `comparison` key (`null` without `--baseline`).

## [0.1.0]

### Added

- Outside-in speed audit of a WordPress page: TTFB and total time, compression, HTTP version, page cache, CDN, images, render-blocking assets, third-party hosts, theme and plugins, with prioritised recommendations.
- `--json` and `--markdown` output, `--user-agent` and `--timeout` options.
- `--max-ttfb`, `--max-total` and `--max-html-kb` CI budgets.
- Docker image on GitHub Container Registry.
- Core Web Vitals field data from the Chrome UX Report API (`--crux`).
