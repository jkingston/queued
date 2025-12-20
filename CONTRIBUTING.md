# Contributing to Queued

Thank you for your interest in contributing to Queued! This document provides guidelines for contributing.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/jkingston/queued
   cd queued
   ```

2. Install dependencies with uv:
   ```bash
   uv sync
   ```

3. Run the application:
   ```bash
   uv run queued
   ```

4. Run tests:
   ```bash
   uv run pytest
   ```

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

Before submitting a PR, ensure your code passes:
```bash
uv run ruff check src/
uv run ruff format src/
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and linting
5. Commit your changes with a descriptive message
6. Push to your fork
7. Open a Pull Request

## Reporting Issues

When reporting issues, please include:
- Your Python version (`python --version`)
- Your OS and version
- Steps to reproduce the issue
- Expected vs actual behavior
- Any error messages or tracebacks

## Feature Requests

Feature requests are welcome! Please open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered
