## What and why

<!-- One or two sentences: what changes, and what problem it solves. -->

## How I checked it

- [ ] `uv run pytest -q` in `core/` (or the files I touched)
- [ ] `npx tsc --noEmit -p .` and `npm run build` in `web/` (if I touched the web app)
- [ ] `python scripts/publish_check.py`
- [ ] Tried it in the simulator (screenshot below if the screen changed)

## Checklist

- [ ] Behaviour changes have tests
- [ ] Wording is plain and calm; light and dark themes both look right
- [ ] Every bed write still goes through the engine's limits
- [ ] No personal data, real addresses, serial numbers or credentials in code, tests, docs or screenshots
