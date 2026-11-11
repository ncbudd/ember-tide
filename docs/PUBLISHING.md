# Publishing checklist (private now, public later)

Private notes (servers, devices, household details, the denylist) live in a separate directory next to this repo,
`../ember-tide-private/`, which is never part of this repository.

## 1. Check the tree

```bash
python scripts/publish_check.py --history
```

(In a worktree or another checkout, pass the denylist explicitly: `PUBLISH_DENYLIST=/path/to/ember-tide-private/publish-denylist.txt`.
The default lookup is relative to the repository's parent folder.) It scans tracked files for secret patterns and for the private words listed in
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

## 2b. Docs, pictures and README placeholders

- `README.md` and `docs/install.md` clone from `https://github.com/Ncbudd/ember-tide.git`. Replace the placeholder with the real
  account in the publish copy (`sed -i 's#Ncbudd#Ncbudd#' README.md docs/*.md`), and check for others (`sleep.example.com`,
  `192.168.1.x` are deliberate examples and stay).
- The screenshots in `docs/images/` come from the simulator with Alex and Sam (recipe and scripts: `scripts/screenshots/README.md`).
  Regenerate them when the screens change; look at each one for real names, addresses or errors. Keep the folder small (under about 6 MB).
- Docs that must stay accurate with the code: `docs/install.md`, `docs/bed-cutover.md`, `docs/android.md`, `docs/configuration.md` (the
  environment table: re-derive it with `grep -rn "ET_" core/embertide/config.py core/embertide/fcm.py core/embertide/trace.py`),
  `docker-compose.example.yml` and `deploy/mosquitto/`.
- Repository files for a public project: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.github/ISSUE_TEMPLATE/`,
  `.github/pull_request_template.md`. Turn on private vulnerability reporting in the GitHub settings so `SECURITY.md` is true.

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
