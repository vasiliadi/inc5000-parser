# AGENTS.md

[README.md](README.md) has the overview, setup, and run commands. This file holds the conventions that apply to every change; per-module detail lives in [Reference docs](#reference-docs) below.

## Workflow conventions

- **Run Python through `uv`:** `uv run src/scraper.py`, not `python src/scraper.py`.
- **Add and bump dependencies with `uv add <package>`** so `uv` owns `pyproject.toml` and `uv.lock` — hand-editing the manifest desyncs the lockfile.
- **Gate every commit on these passing:**

  ```bash
  uvx ruff format .
  uvx ruff check .
  uvx ty check .
  ```

  When the change touches `src/analyzer.py`, also run `uv run marimo check --fix src/analyzer.py`.
- **Prefix the commit subject with the area that changed** — `scraper`, `analysis`, `research`, or `config` for module changes; `docs` or `chore` when the change is cross-cutting. The description already conveys what kind of change it is, and the area is what people scan for when reviewing history.

  ```text
  area: description

  [optional body]
  ```

## Reference docs

Load these on demand, when working in the matching area — not up front.

- [docs/scraper.md](docs/scraper.md) — read before changing `src/scraper.py` or the in-page JS (`NAV_JS`/`WALK_JS`): the firecrawl persistent session, the three phases, `HEADERS`/dedupe/knobs, plus firecrawl and site-specific gotchas.
- [docs/analyzer.md](docs/analyzer.md) — read before changing `src/analyzer.py`: the marimo notebook's stack, cell wiring, brush-to-filter constraint, and outlier knobs.
- [docs/researcher.md](docs/researcher.md) — read before changing `src/researcher.py`: Parallel Task API enrichment (create+result per worker, the rate limiter, JSONL checkpoint/resume, knobs).
