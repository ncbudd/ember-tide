# Configuration reference

The server is configured with environment variables (`core/embertide/config.py`, plus two read elsewhere). Everything has a
safe default: with nothing set you get the simulator on port 8790 with sign-in on. Empty values count as unset. Most
day-to-day settings (rhythms, notifications, devices, people) are in the app and Studio, not here.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `ET_BED` | `sim` | Which bed driver to use: `sim` (built-in simulator), `orion-mqtt` (the Orion topper over local MQTT) or `orion-cloud` (the maker's cloud API). |
| `ET_TZ` | `TZ`, else `UTC` | Your IANA time zone, for example `Europe/London`. Schedules and nights are local time, so set this. The standard `TZ` variable is used when `ET_TZ` is empty. |
| `ET_MQTT_URL` | `mqtt://localhost:1883` | The broker the bed talks to (`orion-mqtt` only). A login can go in the URL: `mqtt://user:password@host:1883`. |
| `ET_ORION_SERIAL` | auto | Serial number of the bed to control. Auto-detected from the first bed that reports; set it if several beds share the broker. |
| `ET_ORION_API_KEY` | none | Personal API key from the maker's web app (`orion-cloud` only). Never put it in a file you share. |
| `ET_DATA_DIR` | `./data` (the Docker image sets `/data`) | Where the database (`embertide.db`), the Web Push key, night traces and optional credentials live. Back this folder up. |
| `ET_HOST` | `0.0.0.0` | Address the server listens on. Use `127.0.0.1` if a reverse proxy on the same machine is the only client. |
| `ET_PORT` | `8790` | Port the server listens on (the Docker image sets 8790). |
| `ET_WEB_DIR` | `<repo>/web/dist` (the Docker image sets `/app/web`) | Folder with the built web app. |
| `ET_AUTH` | `on` | Sign-in. `off`, `0`, `false` or `no` turns it off: for testing on a trusted machine only, never on a reachable address. |
| `ET_PUBLIC_URL` | none | How phones reach the server, for example `https://sleep.example.com`. Makes buttons in notifications work. Can also be set in Studio, Notifications. |
| `ET_TRUST_PROXY` | `off` | `on` (or `1`, `true`, `yes`) behind a reverse proxy or tunnel, so rate limits see real visitor addresses. Forwarding headers are believed only from a private or loopback peer; Cloudflare's `CF-Connecting-IP` wins. |
| `ET_HA_MQTT_URL` | none | Home Assistant's MQTT broker, for example `mqtt://user:password@192.168.1.10:1883`. Can also be set in Studio, Integrations. |
| `ET_HA_DISCOVERY_PREFIX` | `homeassistant` | MQTT discovery prefix used for Home Assistant entities. Change only if you changed it in Home Assistant. |
| `ET_FCM_CREDENTIALS` | `<data dir>/fcm-service-account.json` | Path to your own Firebase service-account key, to send Google push notifications to the Android app. Optional. |
| `ET_LLM_URL` | none | An OpenAI-compatible endpoint for the optional "Ask" assistant and morning review, for example Ollama at `http://host:11434/v1`. Unset means built-in analysis only; nothing leaves the house. |
| `ET_LLM_MODEL` | `qwen2.5:7b` | Model name sent to that endpoint. |
| `ET_LLM_KEY` | none | API key for the endpoint, if it needs one. |
| `ET_LLM_PROVIDER` | `openai` | `openai` (any OpenAI-compatible server) or `anthropic` (needs `pip install "embertide[claude]"`; key from `ET_LLM_KEY` or the SDK's own credentials). |
| `ET_SIM_SPEED` | `1` | Clock speed for the simulator only (`ET_BED=sim`). For example `60` runs a minute per second. Also changeable in Studio, Simulator. |
| `ET_TRACE` | `on` | A compact JSON line every 15 seconds in `<data dir>/traces/`, kept 14 days, for tuning presence on real nights (no names). `off`, `0` or `false` disables it. |
| `TZ` | none | Standard time-zone variable; used by the container and as the fallback for `ET_TZ`. |

That is 22 variables (21 `ET_` plus `TZ`). Settings that are secrets (`ET_ORION_API_KEY`, `ET_LLM_KEY`, URLs with passwords) are best
kept in a `.env` file next to your Compose file with restricted permissions, which is git-ignored here:

```yaml
    env_file: .env
```

## Files in the data folder

| Path | Created by | Notes |
|---|---|---|
| `embertide.db` (+ `-wal`, `-shm`) | the server | SQLite database: everything. |
| `vapid-private.pem` | first Web Push use | The server's Web Push key. Keep it private. |
| `fcm-service-account.json` | you | Optional Google push credentials. |
| `traces/trace-YYYY-MM-DD.jsonl` | the server | Presence tuning traces, 14 days. |

## Ports

| Port | What | Exposure |
|---|---|---|
| 8790 | web app, Studio and API | LAN, or behind HTTPS if reachable from outside |
| 1883 | the bed's MQTT broker (the Compose example) | LAN only, never forwarded |

## Settings kept in the app

Rhythms, people and their preferences, Autopilot, devices (radar, scales, room sensors, calendar, bedside dials), notification
targets, Home Assistant, cloud wearable accounts and invitations are stored in the database and edited in the app and Studio. See
[settings-map.md](settings-map.md) for where each one lives and [integrations.md](integrations.md) for cloud accounts and generic
webhooks.
