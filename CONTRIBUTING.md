# Contributing

Thanks for helping. Diffplane writes configuration to real switches, so changes are held to a
careful standard: correct first, small, and tested.

## Before you start

- Know the project's rules (one writer to NetBox, store intent and derive the rest, never deploy
  without a diff, never manage the management plane) and read
  [docs/architecture.md](docs/architecture.md).
- For anything larger than a fix, open an issue first and describe the change.
- **Never test against switches you do not own, or against production.** Use a lab switch, or
  only the tests below.
- Security problems go through [SECURITY.md](SECURITY.md), not issues.

## Setting up

```sh
cd deploy && python3 gen_env.py      # writes deploy/.env with random secrets (never commit it)
docker compose build orchestrator
```

## Tests

```sh
# Python (generators, rulebook, orchestrator), in a container from the orchestrator image
scripts/test.sh                      # or e.g. scripts/test.sh tests/generators -k bgp

# GUI
cd gui && npm ci && npx tsc --noEmit -p . && npx vitest run && npm run build
```

- A generator change comes with golden configs in `tests/golden/` (`UPDATE_GOLDEN=1` rewrites
  them; review the diff) and patch tests in both directions.
- A new or changed rulebook rule gets a patch test in `tests/rulebook/`.
- **Vendor syntax is verified, not guessed.** If a command or its undo form has not been seen
  on a real switch, say so in the pull request.

## Commits and pull requests

- Small commits, one concern each, with a message that says why.
- Fill in the pull request template; CI must pass.
- Install the pre-commit hooks: `pip install pre-commit && pre-commit install`. They run
  gitleaks; never commit a secret, a real switch's address, serial or host key, or a
  production configuration.
- By contributing you agree that your contribution is licensed under the Apache License 2.0.
