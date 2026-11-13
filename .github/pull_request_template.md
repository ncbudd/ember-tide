## Root cause

<!-- For a bug fix: what was actually wrong, in a sentence or two. For a feature: the need it serves. -->

## Fix

<!-- What this change does about it. -->

## Verification

<!-- How you checked it. Tick what applies; add screenshots if a screen changed. -->

- [ ] `python -m pytest -q` in `core/` (or the files I touched)
- [ ] `npx tsc --noEmit -p .` and `npm run build` in `web/` (if I touched the web app)
- [ ] `python scripts/publish_check.py`
- [ ] Tried it in the simulator

## Release note

<!-- One plain-language line for the changelog, or "none" if users will not notice. -->

## Checklist

- [ ] Behaviour changes have tests
- [ ] Wording is plain and calm; light and dark themes both look right
- [ ] Every bed write still goes through the engine's limits
- [ ] No personal data, real addresses, serial numbers or credentials in code, tests, docs or screenshots
