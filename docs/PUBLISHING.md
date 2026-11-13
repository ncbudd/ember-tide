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
- README, SECURITY.md and LICENSE are accurate; GitHub security reporting is enabled; the first release
  (below) has been made from the public commit, so the image in `docker-compose.example.yml` exists.
- Settings: branch protection on `main`. CI needs no secrets; only the release workflow uses the ones listed below.

## 4. Releasing a version

A release is one tag. The workflow `.github/workflows/release.yml` does the rest.

1. **CHANGELOG.** Add a section headed `## X.Y.Z` (the text after it, such as "(first public release)", is fine). Its body becomes the release notes.
   In the same pull request set the version to `X.Y.Z` in `core/embertide/__init__.py`, `core/pyproject.toml` and `web/package.json`
   (and the two root entries in `web/package-lock.json`): the release stops if they don't match the tag. The notes also get
   the APK signing certificate's SHA-256 fingerprint automatically.
   The checks in section 1 must pass.
2. **Tag and push the tag to the publish repository** (the one made in section 2, not the working repository):

   ```bash
   cd ../ember-tide-publish
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   Or run the **Release** workflow by hand from the Actions tab and type the version; it then tags the commit it ran on.
3. **What the workflow produces:**
   - Python tests, web typecheck and build must pass first.
   - The server image `ghcr.io/ncbudd/ember-tide`, tagged `X.Y.Z`, `X.Y` and `latest`, for linux/amd64 and linux/arm64, with OCI labels
     (source, license, version).
   - Signed Android APKs built with `EMBERTIDE_VERSION=X.Y.Z` and a versionCode that only goes up: `major*10000 + minor*100 + patch`
     (0.1.0 is 100, 0.2.3 is 203, 1.0.0 is 10000; keep minor and patch under 100). Phone and watch get the same numbers.
   - A GitHub Release `vX.Y.Z` with `ember-tide-X.Y.Z-android-google-push.apk`, `ember-tide-X.Y.Z-android-no-google.apk`,
     `ember-tide-X.Y.Z-wear-os.apk` and `SHA256SUMS.txt`, with the CHANGELOG section as the notes.
   If `GOOGLE_SERVICES_JSON_B64` is missing the Google-push APK is skipped with a warning; if the signing key is missing the build stops.
4. **Check the result**: the Release page has four files, the image has three tags, and `sha256sum -c SHA256SUMS.txt` passes.

### Secrets (repository Settings, Secrets and variables, Actions)

| Secret | What it is |
|---|---|
| `ANDROID_KEYSTORE_B64` | the release keystore file, base64 encoded |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore password |
| `ANDROID_KEY_ALIAS` | the key's alias inside it |
| `ANDROID_KEY_PASSWORD` | the key's password (if the key has its own; otherwise the same as the keystore's) |
| `GOOGLE_SERVICES_JSON_B64` | `google-services.json` of the Firebase project, base64 encoded (Google-push APK only) |

Set them from the folder that holds the files, without typing a value on the command line (the commands read from files and prompts):

```bash
base64 -w0 embertide-release.jks | gh secret set ANDROID_KEYSTORE_B64 --repo Ncbudd/ember-tide
gh secret set ANDROID_KEYSTORE_PASSWORD --repo Ncbudd/ember-tide        # paste at the prompt
gh secret set ANDROID_KEY_ALIAS --repo Ncbudd/ember-tide
gh secret set ANDROID_KEY_PASSWORD --repo Ncbudd/ember-tide
base64 -w0 google-services.json | gh secret set GOOGLE_SERVICES_JSON_B64 --repo Ncbudd/ember-tide
```

(On macOS use `base64 -i file`; in PowerShell, `[Convert]::ToBase64String([IO.File]::ReadAllBytes("file")) | gh secret set NAME`.)
The workflow decodes the keystore to a temporary file, never prints a secret, and deletes the file at the end.

**Back up the signing key.** Android only installs an update signed with the same key as the app already on the phone, and the watch app only pairs with a
phone app signed with the same key. If the keystore is lost, every user has to uninstall and reinstall to move to a new key, and the watch tile has to be set up again.
Keep the keystore file and its passwords in at least two safe places (a password manager and an offline copy), never in the repository.

### While the repository is private

A private repository's image on ghcr.io is private too, so pulling needs a login first: `docker login ghcr.io -u <github-user>` with a personal
access token that has the `read:packages` permission. The Release downloads need a signed-in GitHub account with access to the repository.
Both open up to everyone when the repository and the package are made public (the package's visibility is set separately, in the package settings on GitHub).
