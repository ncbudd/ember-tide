# Contributing

Thanks for helping. Read [docs/HANDOFF.md](docs/HANDOFF.md) first: architecture, layout, conventions and how to run things.
By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), and Node 20 or newer.

```bash
cd core
uv venv
uv pip install -e ".[dev]"
uv run pytest -q                      # about three minutes

cd ../web
npm ci
npx tsc --noEmit -p .                 # type check
npm run build                         # production bundle into web/dist
```

Run the app against the built-in simulator (no bed needed):

```bash
cd core
ET_AUTH=off ET_TZ=America/New_York ET_DATA_DIR=./data-dev uv run python -m embertide    # http://localhost:8790
cd ../web && npm run dev                                                                 # hot reload, proxies the API
```

Studio, Simulator moves the clock, puts people in bed and raises faults. `ET_AUTH=off` is for local work only.
The Android app builds with Gradle (see [docs/android.md](docs/android.md)); it is not part of CI.

## Before you open a pull request

```bash
cd core && uv run pytest -q           # Python tests
cd ../web && npx tsc --noEmit -p . && npm run build
python scripts/publish_check.py       # no secrets, no private words
```

- Behaviour changes come with tests. Tests that need private real-world data (for example `test_real_hc.py`, through `ET_REAL_HC`) skip
  themselves when it is absent. Run only the files you touched while iterating, then the whole suite once.
- Keep pull requests small and about one thing. Say what changed and why, and how you checked it.
- Screenshots help for any visible change; use the simulator, never a real household.

## Style

- **Plain, calm language in the UI.** No jargon without a tooltip; say what happened and what to do. Big touch targets, right-handed layouts
  (primary controls on the right and bottom), a light theme with text contrast of at least 4.5:1, and the dark theme and the red night
  screen kept working.
- **Safety first.** Every write to the bed goes through the engine's limits. Don't add a path around it, and don't add commands that haven't
  been seen working on real firmware.
- Python: type hints, small focused modules, comments that explain *why*. TypeScript: strict mode, no `any` where a type is easy.
- Settings and every message people read are checked in tests; look at `tests/test_use_cases.py` and `tests/simnight.py` for the helpers.
- **No personal data** in code, tests, comments, docs or screenshots: no real names, addresses, device serial numbers, hostnames or domains.
  Use the generic seed names (Alex, Sam, "the dog") and example hosts (`homeassistant.local`, `192.168.1.x`, `sleep.example.com`).
- **No credentials, ever.** Don't commit `google-services.json`, service-account keys, keystores, `.env` files or data folders; `.gitignore`
  covers them, but check `git status` before you commit.

## Reporting problems and asking for hardware support

Use the issue templates (bug, feature, hardware support). For a security problem follow [SECURITY.md](SECURITY.md) and don't open a public issue.

## Licence of contributions

Ember & Tide is under the [PolyForm Noncommercial License 1.0.0](LICENSE). By sending a pull request you agree that
your contribution is licensed under the same terms, and that the project owner may also offer it under other terms
(for example a separately agreed commercial licence), so the project can stay in one piece.
