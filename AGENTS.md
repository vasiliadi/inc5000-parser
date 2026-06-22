# AGENTS.md

See [README.md](README.md) for the overview, setup, and run commands. This file holds the always-applicable workflow conventions; the detail needed to *modify* the scraper (`src/parser.py`) and the analyzer (`src/analysis.py`) lives in `docs/` and is linked under [Reference docs](#reference-docs) below — read those only when working in the relevant area.

## Workflow conventions

- **Always run Python through `uv`.** Use `uv run <script>` (e.g. `uv run src/parser.py`) instead of bare `python`. Never invoke the interpreter directly.
- **Add dependencies with `uv add <package>`.** Don't hand-edit `pyproject.toml` to add or bump dependencies — let `uv` manage the manifest and `uv.lock`.
- **Before every commit, all three of these must pass:**

  ```bash
  uvx ruff format .
  uvx ruff check .
  uvx ty check .
  ```
  
- **Commit messages use the scope-prefixed format.** Lead with the area of the codebase that changed, not a change type — the description already conveys what kind of change it is, and the scope is what people actually scan for when debugging or reviewing history.

  ```text
  scope: description

  [optional body]
  ```

## Reference docs

Read these only when working in the relevant area — don't load them up front.

- [docs/scraper.md](docs/scraper.md) — how the scraper works (firecrawl persistent session, the three phases, the `HEADERS`/dedupe/knobs) plus the firecrawl and site-specific gotchas. Read before changing `src/parser.py` or editing the in-page JS (`NAV_JS`/`WALK_JS`).
- [docs/analyzer.md](docs/analyzer.md) — the marimo notebook's stack, cell wiring, brush-to-filter constraint, and outlier knobs. Read before changing `src/analysis.py`.
