# Repository Guidelines

## Project Structure & Module Organization

- `app/` contains the FastAPI application and domain modules. Start at `app/main.py`; shared configuration, logging, resilience, caching, and metrics live in `app/core/`.
- Platform integrations are under `app/platforms/`, with Feishu-specific code in `app/feishu/`, Telegram code in `app/platforms/telegram/`, and Discord code in `app/platforms/discord/`.
- `app/graph/` contains LangGraph workflows, while `app/fetcher/`, `app/llm/`, `app/rag/`, `app/queue/`, `app/db/`, `app/subscription/`, and `app/chat/` own their respective services.
- `tests/` contains pytest tests organized by feature. Architecture decisions and operational notes are in `docs/`; Prometheus and Grafana configuration is in `monitoring/`; runnable utilities are in `scripts/`.

## Build, Test, and Development Commands

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pytest -v
docker-compose up -d --build
```

Use `requirements-lock.txt` for reproducible or production installs. The Docker Compose stack starts the app, MySQL, Redis, Prometheus, and Grafana. Verify a local service with `curl http://localhost:8000/health`.

## Coding Style & Naming Conventions

Use Python 3.10+ with four-space indentation, type hints, and small single-purpose modules. Follow existing `snake_case` names for modules, functions, and variables; use `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants. Preserve the repository's async/thread boundary and adapter interfaces when changing integrations. No formatter or linter is currently configured; keep imports, line layout, and docstrings consistent with neighboring code.

## Testing Guidelines

Tests use pytest, pytest-asyncio, pytest-mock, and pytest-cov. Name files `test_<feature>.py` and tests `test_<behavior>`. Run `pytest -v` for the full suite, or target a subset with `pytest -v -k "telegram"`. Pytest reports coverage for `app/`; CI policy requires at least 50% coverage when the full suite is run.

## Commit & Pull Request Guidelines

Use concise imperative commit subjects with the established prefixes: `feat:`, `fix:`, `chore:`, or `docs:`. Pull requests should explain the behavior change, identify affected modules, include test results, and link relevant issues or ADRs. Include screenshots or sample requests when changing user-facing cards, bot responses, or monitoring dashboards.

## Security & Configuration

Copy `.env.example` to `.env` for local setup and never commit credentials, tokens, or generated `chroma_data/` state. Keep `ADMIN_API_TOKEN` configured before enabling `/admin/*`; these endpoints can trigger bulk delivery and embedding work.
