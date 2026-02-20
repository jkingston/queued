# Queued - TUI SFTP Download Manager

## Commands

All commands MUST be run via `uv run`. NEVER use bare `pip`, `uv pip`, `python`, or `ruff` commands.

```bash
uv run pytest              # run tests
uv run ruff check          # lint
uv run ruff format         # format
uv run ruff check --fix    # auto-fix lint issues
uv run python -m queued    # run the app
```

## Project Structure

- `src/queued/` - main package
- `tests/` - pytest test suite (pytest-asyncio, mode=auto)
- `pyproject.toml` - project config, dependencies, ruff config
- `flake.nix` - Nix build/dev shell

## Key Conventions

- Python >=3.14 required
- Use `uv sync` to install/update dependencies (never pip)
- Ruff for linting and formatting (config in pyproject.toml)
- No string-quoted forward references in type annotations (PEP 649 is default in 3.14)
