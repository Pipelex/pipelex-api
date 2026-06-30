# Contributing to Pipelex API

Thank you for your interest in contributing! Contributions are very welcome — first-time contributors included. Join our community on [Discord](https://go.pipelex.com/discord) and feel free to reach out in `#code-contributions` and `#pipeline-contributions` with questions.

All interactions in Discord, codebases, mailing lists, events, and any other Pipelex activities are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## What we accept

This repository is the **Pipelex API server** — a FastAPI wrapper around the Pipelex runtime. Contributions are welcome for:

- **Bug fixes** — crashes, incorrect responses, performance issues
- **Features** — new endpoints, request/response shapes, deployment options
- **Refactors** — internal cleanup, better abstractions
- **Tests** — coverage gaps, regression guards
- **Docs** — README, `docs/`, examples, OpenAPI clarifications
- **CI/CD** — GitHub Actions, Docker tooling, release scripts

For changes to the underlying pipeline engine (TOML schema, pipe types, inference routing, …), open the issue or PR in [`Pipelex/pipelex`](https://github.com/Pipelex/pipelex) instead — that's the runtime, not this server.

Issues tagged `good first issue` or `help-welcome` are easy entry points. If you'd like to work on an untagged issue, comment with your approach so a maintainer can assign it before you start.

## Requirements

- Python 3.11 through 3.14 (see `pyproject.toml`'s `requires-python`)
- [uv](https://docs.astral.sh/uv/) ≥ 0.7.2
- Docker (only required if you want to test the published image flow)

## Development setup

```bash
# Fork, then clone your fork
git clone https://github.com/<your-username>/pipelex-api.git
cd pipelex-api

# Install dependencies (creates .venv and installs runtime + dev extras)
make install

# Configure environment — only PIPELEX_GATEWAY_API_KEY is required.
# Get a free key (with free credits) at https://app.pipelex.com.
cp .env.example .env
$EDITOR .env

# Run the API locally (hot reload)
make run

# Verify
curl http://localhost:8081/health
```

Never commit `.env`. The full set of supported variables is documented in [`docs/configuration.md`](docs/configuration.md).

## Pull request process

1. Create a branch named `<your-handle>/<category>/<short-slug>` where category is one of `feature`, `fix`, `refactor`, `docs`, `cicd`, `chore`.
2. Make your changes.
3. Run `make fui` (remove unused imports), `make c` (format + lint + pyright + mypy), and `make tp` (tests). All must pass.
4. Push to your fork and open a PR that links to an existing issue (PRs without a linked issue may not be accepted).
5. Fill in the PR template's title and description.
6. Mark the PR as **Draft** until CI is green; switch to **Ready for review** when it is.
7. Address review feedback; a maintainer will merge once approved.

The first time you open a PR, the CLA-assistant bot will guide you through signing the Contributor License Agreement (we use [CLA assistant lite](https://github.com/marketplace/actions/cla-assistant-lite)).

## License

This project is licensed under the [MIT License](LICENSE). By submitting a PR you confirm that your contribution is licensed under the same terms.
