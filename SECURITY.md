# Security

## Reporting a vulnerability

Please report privately through GitHub's private vulnerability reporting: open the **Security** tab of this
repository and choose **Report a vulnerability**
(`https://github.com/Ncbudd/ember-tide/security/advisories/new`). Don't post exploits publicly. Expect an
acknowledgement within a week; this is a small hobby project, so fixes are best-effort.

## Supported versions

Only the latest release (and `main`) gets fixes. The project is early (0.x).

## What is checked automatically

Every change runs Semgrep, CodeQL, dependency review, gitleaks and Trivy, plus a weekly OpenSSF Scorecard. Released
server images are scanned, signed with Sigstore cosign, and carry an SBOM and build provenance attestation. Publishing
a release waits for the maintainer's approval.

## Security model in brief

- **Accounts:** the first visit from the home network creates the **owner** (directly on the local address, never
  through a tunnel or proxy). Roles are **owner** (everything), **sleeper** (their own
  side; the partner's side only as far as the partner shares it) and **viewer** (read only). Scripts and Home
  Assistant use per-purpose API keys (`Authorization: Bearer et_...`) created by the owner.
- **Phone extras and the watch** use limited device keys (`et_d_...`) each person makes for themselves: the snapshot
  and a fixed list of own-side actions, tied to the phone's session (signing out ends them), revocable in Settings.
  The live feed opens with a single-use 30-second ticket, so no session goes in a URL.
- **Run it behind HTTPS.** Put it behind a tunnel or reverse proxy that terminates TLS (Cloudflare Tunnel, Caddy,
  nginx, Tailscale), set `ET_PUBLIC_URL`, and set `ET_TRUST_PROXY=on` so limits count the visitor's address
  (headers are believed only from a private or loopback peer, Cloudflare's `CF-Connecting-IP` first, otherwise the
  right-most outside hop of `X-Forwarded-For`).
  Don't port-forward the plain HTTP port to the internet.
- **`ET_AUTH=off`** disables sign-in. Use it only for testing on a trusted LAN, never on a reachable address.
- **The MQTT broker is LAN-open by design.** The Orion bed's local protocol speaks plain MQTT to a broker you
  run, with no per-device credentials on the bed side. Firewall the broker to your LAN (and the bed's VLAN);
  anyone who can reach it can talk to the bed.
- **Writes are bounded.** Every command goes through one engine with temperature limits and rate limits; firmware,
  Wi-Fi and low-level pump/valve commands are refused.
- **Secrets stay out of the repo.** Firebase config (`google-services.json`), the service-account key, VAPID keys
  and `.env` files are git-ignored. `scripts/publish_check.py` scans for common leaks.
