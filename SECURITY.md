# Security

## Reporting a vulnerability

Please report privately through GitHub's "Report a vulnerability" (Security tab) on this repository, or open a
minimal issue asking for a private contact without details. Don't post exploits publicly. Expect an
acknowledgement within a week; this is a small hobby project, so fixes are best-effort.

## Supported versions

Only the latest release (and `main`) gets fixes. The project is early (0.x).

## Security model in brief

- **Accounts:** the first visit creates the **owner**. Roles are **owner** (everything), **sleeper** (their own
  side; the partner's side only as far as the partner shares it) and **viewer** (read only). Scripts and Home
  Assistant use per-purpose API keys (`Authorization: Bearer et_...`) created by the owner.
- **Run it behind HTTPS.** Put it behind a tunnel or reverse proxy that terminates TLS (Cloudflare Tunnel, Caddy,
  nginx, Tailscale), set `ET_PUBLIC_URL`, and set `ET_TRUST_PROXY=on` only when that proxy is the sole way in.
  Don't port-forward the plain HTTP port to the internet.
- **`ET_AUTH=off`** disables sign-in. Use it only for testing on a trusted LAN, never on a reachable address.
- **The MQTT broker is LAN-open by design.** The Orion bed's local protocol speaks plain MQTT to a broker you
  run, with no per-device credentials on the bed side. Firewall the broker to your LAN (and the bed's VLAN);
  anyone who can reach it can talk to the bed.
- **Writes are bounded.** Every command goes through one engine with temperature limits and rate limits; firmware,
  Wi-Fi and low-level pump/valve commands are refused.
- **Secrets stay out of the repo.** Firebase config (`google-services.json`), the service-account key, VAPID keys
  and `.env` files are git-ignored. `scripts/publish_check.py` scans for common leaks.
