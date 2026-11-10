# Publishing checklist (private now, public later)

Private notes (servers, devices, household details, the denylist) live in a separate directory next to this repo,
`../ember-tide-private/`, which is never part of this repository.

## 1. Check the tree

```bash
python scripts/publish_check.py --history
```

It scans tracked files for secret patterns and for the private words listed in
`../ember-tide-private/publish-denylist.txt` (override with `PUBLISH_DENYLIST=path`). `--history` also checks every
commit message and every line any commit ever added (words since edited out or deleted included; `--all-refs` for
every branch). It must exit 0. Also run the Python tests, `npx tsc --noEmit -p .` and `npm run build`.

## 2. Publish from a fresh, single-commit history

The old commit messages mention the household, and old commits may contain data that was later scrubbed. Don't
push them. Export a clean snapshot of the current commit into its own repository next to this one:

```bash
git archive --format=tar HEAD | (mkdir ../ember-tide-publish && tar -x -C ../ember-tide-publish)
cd ../ember-tide-publish
git init -b main
git add -A
git -c user.name=Ncbudd -c user.email=209378361+Ncbudd@users.noreply.github.com commit -m "Ember & Tide 0.1.0"   # no AI trailers
PUBLISH_DENYLIST=../ember-tide-private/publish-denylist.txt python scripts/publish_check.py --history   # must be clean
gh repo create Ncbudd/ember-tide --private --source . --push
```

`git archive` leaves out files marked `export-ignore` (working notes for the local copy: list them in the
working repository's `.git/info/attributes`, which is never published).
This working repository keeps its full history and is never pushed. To publish later changes: run the checks
here, then copy the tree over the publish repository (`git archive HEAD | tar -x -C ../ember-tide-publish`; delete
files that were removed), review `git status` there, and commit as Ncbudd with a plain message.

## 3. Before flipping to public

- Calibration week confirmed and the Autopilot defaults you want to ship are set.
- Any secret that was ever committed (even in old history that is not being published) is rotated: Orion API
  key, `et_` API keys, VAPID keys, MQTT passwords, Firebase service-account key.
- Firebase config (`google-services.json`, service-account JSON) is not in the repo or its history.
- Demo/test data is generic (Alex, Sam, Rex); no real heart-rate exports, serials or addresses.
- `python scripts/publish_check.py --history` is clean on the exact commit being published.
- README, SECURITY.md and LICENSE are accurate; GitHub security reporting is enabled; a container image is
  built from the public commit if you want one (`docker-compose.example.yml` builds locally until then).
- Settings: branch protection on `main`, Actions secrets empty (CI needs none).
