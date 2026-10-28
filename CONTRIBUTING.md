# Contributing

Thanks for taking the time to contribute. This project is alpha software, so
bug reports and focused pull requests are especially valuable.

## Reporting bugs

Open an issue using the bug report template and include:

- What you expected to happen and what actually happened.
- Your OS, Python version, and whether you were using `mock`, `faust-mock`, or
  a live Fusion 360 connection.
- The full traceback or agent output, with API keys and personal paths removed.

Security issues should not be filed publicly — see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/kushalsrinivas/fusion-360-agent.git
cd fusion-360-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

The default `mock` mode needs no API key and no Fusion installation:

```bash
python -m app.main --headless "Create a 50mm cube"
python -m app.main              # TUI
pytest -q
```

To test the real MCP protocol without Fusion, use `--mode faust-mock`. Live
modeling requires Fusion 360, the Fusion360MCP add-in, and `uvx`; see the
README for setup details.

## Making changes

- Keep the dependency list small and the existing layering intact
  (`app/graph`, `app/fusion`, `app/tui`, `app/models`, `app/storage`).
- Add or update tests for behavior changes. Run the full suite before opening
  a pull request.
- Follow the style of the surrounding code: type hints, small functions, and
  comments only where the code cannot explain itself.
- Use clear commit messages, preferably
  [Conventional Commits](https://www.conventionalcommits.org/) (`fix:`,
  `feat:`, `docs:`, `chore:`).

## Pull requests

A pull request should:

1. Explain the problem and the approach.
2. Link the related issue, if one exists.
3. Include tests or a clear manual verification step.
4. Keep unrelated refactors out of the diff.

Never commit `.env` files, API keys, tokens, generated exports, or Fusion
documents. `.env` is git-ignored; use `.env.example` for documented settings.

By contributing, you agree that your contributions are licensed under the
MIT License.
