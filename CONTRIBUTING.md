# Contributing

Thanks for helping. Read `docs/HANDOFF.md` first (architecture, conventions, how to run things).

## Before you open a PR

```bash
cd core && uv venv && uv pip install -e ".[dev]" && uv run pytest -q      # Python tests
cd ../web && npm ci && npx tsc --noEmit -p . && npm run build              # types and build
python scripts/publish_check.py                                            # no secrets / private words
```

Behaviour changes come with tests. Tests that need private real-world data (e.g. `test_real_hc.py`, via
`ET_REAL_HC`) skip themselves when it is absent.

## Style

- Plain, calm language in the UI; big touch targets; right-handed layouts; light theme contrast of at least 4.5:1.
- Safety first: every bed write goes through the engine's limits. Don't add paths around it.
- No personal data in code, tests, comments or docs: use the generic seed names (Alex, Sam, "the dog") and
  example hosts (`homeassistant.local`, `192.168.1.x`, `example.com`).
- No real credentials, ever. Don't commit `google-services.json`, keystores, `.env` files or data directories.
- Keep comments about *why*; keep modules small and focused.
