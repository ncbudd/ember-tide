# Ember & Tide

A local-first controller for dual-zone smart mattress toppers, starting with the **Orion Sleep** topper.
One of you runs hot (Tide), one runs cold (Ember), and each side follows its own temperature rhythm.

- **The rhythm starts when you're actually in bed on your side.** Pre-cooling waits for you, there's a bedtime
  window, and it handles getting up at night.
- **Hot flash and cold flash** with sensible defaults (55°F / 86°F for 15 min, easing back) that you can tune.
- **A Simple screen for 3 AM:** one temperature, big buttons, deep-red night colors.
- **Phone app for quick toggles, Studio for power tools:** a curve editor against real nights, a night explorer
  that shows where every number came from, a bed console, a simulator and data export.
- **A Simple app for the less techy sleeper:** just temperature, flashes and an easy bedtime/wake schedule. The
  person who set it up keeps the detail in Studio.
- **Autopilot that earns its place:** a calibration week to learn your normal before it suggests anything, then
  small explained changes (suggest or act, within your limits), on your cadence (every morning, weekly, monthly).
  You decide how much it trusts the bed, the watch and the room.
- **Context, not just sensors:** phone at home, on the way home (pre-cool so the bed is ready when you arrive),
  evening events and early starts from a calendar feed, bedroom lights. A tuning guide turns patterns into
  plain suggestions (bedtime later? warm wake earlier?).
- **Plays well with what you have:** Home Assistant (entities in, MQTT discovery out), presence radar, bed scales,
  room sensors, Health Connect (Android), bedside dials (ESPHome), Wear OS tile and complication. Misbehaving
  devices are set aside automatically and explained.
- **Phones, tablets, foldables:** hinge-aware layouts, a navigation rail on big screens.
- **Local first:** talks to the bed over its own MQTT protocol, or through Orion's cloud with an API key.
  Nothing updates by itself.

> Status: early (0.1). Works against the built-in simulator and the Orion local/cloud protocols. Not affiliated
> with or endorsed by Orion Longevity Inc.

## Try it in 1 minute (simulator, no bed needed)

```bash
docker build -t ember-tide .
docker run --rm -p 8790:8790 -e ET_TZ=America/New_York ember-tide
```
Open http://localhost:8790 on your phone or computer. Studio is at `/studio`. Use **Studio → Simulator** to jump
to bedtime, speed up time, and put people in or out of bed.

## Connect a real Orion topper

| | Cloud (API key) | Local (MQTT) |
|---|---|---|
| Setup | Paste an API key from app.orionsleep.com/api-keys | Your own MQTT broker plus one router DNS record |
| Internet needed | Yes | No |
| Updates | ~10 s polling | Live, about 1 per second |
| Official Orion app | Keeps working | Stops seeing the bed while local (switch back to update firmware) |

See `docker-compose.example.yml`. Local setup details: `docs/LOCAL.md`.

## Run from source

```bash
cd core && uv venv && uv pip install -e ".[dev]" && uv run pytest
cd ../web && npm install && npm run build
cd ../core && ET_TZ=America/New_York uv run python -m embertide    # http://localhost:8790
```
During UI work, run `npm run dev` in `web/` (proxies the API to :8790).

## Configuration (environment)

| Variable | Default | |
|---|---|---|
| `ET_BED` | `sim` | `sim`, `orion-cloud` or `orion-mqtt` |
| `ET_TZ` | `UTC` | Your time zone. Schedules are local-time. |
| `ET_ORION_API_KEY` | | for `orion-cloud` |
| `ET_MQTT_URL` | `mqtt://localhost:1883` | for `orion-mqtt` |
| `ET_ORION_SERIAL` | auto | |
| `ET_DATA_DIR` | `./data` | SQLite database lives here |
| `ET_PORT` | `8790` | |
| `ET_AUTH` | `on` | Sign-in. The first visit creates the owner account. `off` only for testing on a trusted network. |
| `ET_PUBLIC_URL` | | How phones reach the server (e.g. `https://bed.example.com`), so notification buttons work |
| `ET_TRUST_PROXY` | `off` | `on` behind Cloudflare/nginx, so sign-in throttling sees real client addresses |
| `ET_LLM_URL` / `ET_LLM_MODEL` | | Any OpenAI-compatible endpoint (e.g. Ollama) for Ask |

### Accounts and access

- **Owner**: everything, including devices, the bed console and People & access.
- **Sleeper**: their own side fully; the partner's side only as far as the partner shares it (Settings → Sharing).
- **Viewer**: can look, can't change anything.

Invite the other sleeper from Studio → People & access: it makes a one-time link, nothing to install. Home Assistant,
dials and scripts use API keys from the same page (`Authorization: Bearer et_…`).

Locked out of the owner account? On the server: `python -m embertide reset-password <username>`
(in Docker: `docker exec -it ember-tide python -m embertide reset-password <username>`) prints a one-time link.

## Safety

- Every write goes through one engine with limits: everyday range 12–35 °C, the bed's hard range 10–45 °C,
  per-side rate limits, and an end time stored before any flash starts.
- The local driver only sends commands verified on Orion firmware 2.6.2. Firmware, Wi-Fi and low-level
  pump/valve commands are refused.
- **Don't expose it to the internet without HTTPS.** Sign-in is built in (`ET_AUTH=on`, the default); put the
  server behind a TLS tunnel or reverse proxy, or a VPN (Tailscale, WireGuard). Firewall the MQTT broker to your
  LAN. See `SECURITY.md`.

## Roadmap

Done: notifications (Firebase, UnifiedPush/ntfy, Home Assistant), Home Assistant in and out, radar/scale presence,
calendar feeds, Android app with Health Connect, Wear OS tile, Autopilot with calibration, tuning guide, Simple app.
Next: signed release builds, a step-by-step setup wizard, native iOS app (HealthKit, Apple Watch), Oura, Siri
Shortcuts, MCP server for voice/AI.

## Credits

Built on community protocol work: tdickman's local MQTT integration, borski's documented cloud API, and firmware
notes shared on r/Orion_Sleep.

## License

MIT © Ncbudd
